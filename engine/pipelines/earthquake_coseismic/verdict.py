"""3.01 earthquake_coseismic — registry entry point.

The registry calls exactly this: run(bundle: SceneBundle, providers=None) -> PipelineResult.
The pipeline sees ONLY the bundle (no retriever, no other pipeline's output); anything beyond the
bundle arrives through THIS pipeline's fetch.py, which resolves providers in a documented order
(injected -> module seam -> real adapter). Flow and status table: docs/IMPLEMENTATION_PLAN.md 4.2
and 8.4; typed result contract: engine/common/result.py.
"""
from __future__ import annotations

import dataclasses
import hashlib
import math

import numpy as np

from engine.common.result import EvidenceItem, LayerRef, PipelineResult, Source

from . import detection as det
from . import model
from . import prep
from . import uncatalogued
from .fetch import fetch_events, fetch_shakemap_note
from .lib_local import core_bounds_lonlat, encode_layer
from .types import EqParams

REFERENCES_USED = [
    "Okada (1985, 1992) DC3D elastic dislocation — engine/lib/okada.py",
    "Hanks & Kanamori (1979) moment–magnitude relation",
    "Wells & Coppersmith (1994) rupture-area scaling (all slip types)",
    "Rodriguez & Martin (1992) coherence–phase Cramér-Rao sigma (via P06 sigma_m)",
]
PIPELINE_VERSION = "1.2.0"          # final-prompt fixes: events_unscreenable split, dip-90 okada fix,
#                                   # stable evidence ids, layer bounds_lonlat, R2 coverage check


def _bundle_seed(bundle) -> int:
    """Determinism rule: seeds come from the bundle's content hash (same bundle -> same result)."""
    try:
        return int(hashlib.sha256(str(bundle.bundle_id).encode("utf-8")).hexdigest()[:8], 16)
    except Exception:
        return 0


def _evidence(eid: str, kind: str, claim: str, *, support: dict | None = None,
              level: str = "info", source_ref: str | None = None) -> EvidenceItem:
    return EvidenceItem(id=eid, kind=kind, claim=claim, support=support, level=level, source_ref=source_ref)


def _insufficient(reasons: list[str], warnings: list[str] | None = None, tile: str = "",
                  epoch: str | None = None, bundle_id: str | None = None,
                  params: "EqParams | None" = None) -> PipelineResult:
    return PipelineResult(
        pipeline="earthquake_coseismic", tile=tile, epoch=epoch, status="INSUFFICIENT_EVIDENCE",
        summary="Not enough usable data to evaluate coseismic deformation on this tile.",
        raw_p=None, confidence=None,
        evidence=[_evidence(f"insufficient.{i}", "insufficient", r, level="caveat")
                  for i, r in enumerate(reasons)],
        layers=[], provenance=[], data_used=[], references_used=REFERENCES_USED,
        diagnostics={"decision": {"rule": "insufficient_evidence"}, "pairs_analysed": []},
        versions=_versions(), warnings=warnings or [], bundle_id=bundle_id,
        detail={"decision": {"rule": "insufficient_evidence"}, "pairs_analysed": [],
                "params": params.to_dict() if params is not None else {}},
    )


def _versions() -> dict:
    return {"pipeline": PIPELINE_VERSION, "lib.okada": "1.0", "lib.los": "1.0",
            "lib.detect": "1.0", "lib.geo": "1.0", "result_contract": "1.0"}


def run(bundle, providers: dict | None = None) -> PipelineResult:
    params = EqParams.from_triggers(getattr(bundle.request, "triggers", None))
    providers = providers or {}
    warnings: list[str] = []

    # engine flags that matter for LOS honesty (surfaced into evidence)
    flag_notes = [
        f"{f.code}: {f.message}" for f in bundle.flags
        if f.code in ("los_convention_suspect", "pairs_flipped", "fetch_error")
    ]

    stack = bundle.stack()
    if stack is None or stack.interferometric is None or not stack.interferometric.pairs:
        return _insufficient(["no usable NISAR data (M1 interferograms) for this tile/window"],
                             flag_notes, bundle.tile.id, bundle.epoch, bundle.bundle_id, params=params)

    # ---- 1. pair selection -------------------------------------------------
    indices, reasons = prep.select_pairs(bundle, params)
    if not indices:
        return _insufficient(reasons, flag_notes, bundle.tile.id, bundle.epoch, bundle.bundle_id,
                             params=params)

    # ---- 2-3. per-pair prep + detection -------------------------------------
    analyses = []
    pair_records = []
    for idx in indices:
        analysis, reason = prep.analyse_pair(bundle, stack, idx, params)
        if analysis is None:
            pair_records.append({"pair_index": idx, "skipped": True, "reason": reason})
            continue
        summary, grids = det.detection_pass(
            analysis.disp, analysis.sigma_eff, analysis.mask,
            np.asarray(stack.interferometric.coherence[idx][bundle.grids.g80.core_slices()], dtype=np.float64),
            getattr(analysis, "iono_floor_m", None),
            analysis.pixel_size,
            k=params.significance_k, floor_m=params.detection_floor_m,
            cluster_min_pixels=params.cluster_min_pixels,
        )
        summary.null_screen = det.run_null_scene_screen(
            analysis.sigma_eff, analysis.mask, analysis.pixel_size,
            k=params.significance_k, floor_m=params.detection_floor_m,
        )
        analysis.summary = summary          # type: ignore[attr-defined]
        analysis.grids = grids              # type: ignore[attr-defined]
        analyses.append(analysis)
        warnings.extend(analysis.warnings)
        pair_records.append({
            "granule_ids": list(analysis.granule_ids),
            "ref_time": analysis.ref_time, "sec_time": analysis.sec_time,
            "valid_pixels": summary.valid_pixels,
            "ref_offset_m": analysis.ref_offset_m, "ref_sigma_m": analysis.ref_sigma_m,
            "detected_pixels": summary.detected_pixels,
            "affected_area_km2": summary.affected_area_km2,
            "clusters": summary.clusters, "null_screen": summary.null_screen,
            "flags": analysis.flags,
            "selected": False,
        })

    if not analyses:
        return _insufficient(
            [r.get("reason", "pair analysis failed") for r in pair_records] or ["no pair produced a usable analysis"],
            flag_notes + warnings, bundle.tile.id, bundle.epoch, bundle.bundle_id, params=params)

    # best pair = largest affected area (tie-break: more valid pixels), deterministic
    best = max(analyses, key=lambda a: (a.summary.affected_area_km2, a.summary.valid_pixels, -a.pair_index))
    for rec in pair_records:
        rec["selected"] = (rec.get("granule_ids") == list(best.granule_ids))

    # ---- 4. events (fetch failure => INSUFFICIENT_EVIDENCE, never "no event")
    bbox = tuple(bundle.tile.bbox_lonlat)  # (W, S, E, N)
    events_res = fetch_events(bbox, best.ref_time, best.sec_time, params, providers=providers)
    provenance: list[Source] = list(getattr(events_res, "provenance", ()) or ())
    if events_res.error or events_res.degraded:
        reason = events_res.error or "event provider reported degraded results"
        out = _insufficient([f"catalog events could not be retrieved: {reason}"],
                            flag_notes + warnings + pair_note_lines(pair_records),
                            bundle.tile.id, bundle.epoch, bundle.bundle_id, params=params)
        out.provenance = provenance
        return out
    events = list(events_res.events)

    # ---- 5. forward-model screens (top-N ranked events, ALL their planes) ---
    selection = model.rank_events(events, best.ref_time, best.sec_time,
                                  tuple(bundle.tile.center_lonlat), params)
    screens: list[dict] = []
    for ev, dist in selection.ranked[: params.forward_top_events]:
        for plane in range(max(1, len(ev.nodal_planes)) if ev.nodal_planes else 1):
            screens.append(model.forward_screen(best, ev, bundle.tile.epsg, params, plane_index=plane))
    for item in selection.excluded[:20]:
        warnings.append(f"event {item['id']} not considered: {item['reason']}")

    # ---- 5b. uncatalogued branch: detection present, GENUINELY no catalog event
    # (an in-window event without a mechanism is a data gap -> events_unscreenable,
    #  NOT an uncatalogued candidate — final-prompt fix 1). The fit itself lives in
    # uncatalogued.py: strike scan + least-squares refinement, analytic slip with a
    # stress-drop cap; seed derives from the bundle content hash (determinism rule).
    uncatalogued_fit: dict | None = None
    if not selection.ranked and best.summary.detected_pixels > 0:
        fit_params = dataclasses.replace(params, uncat_seed=_bundle_seed(bundle))
        uncatalogued_fit = uncatalogued.fit_uncatalogued(best, best.summary, bundle.tile.epsg, fit_params)
        if not uncatalogued_fit.get("attempted"):
            warnings.append(f"uncatalogued-source fit not attempted: {uncatalogued_fit.get('reason')}")
            uncatalogued_fit = None

    # ---- 6. decision ---------------------------------------------------------
    status, raw_p, rule, extra = model.decide(
        best.summary, screens, events_known=True, params=params,
        uncatalogued=uncatalogued_fit, n_events_in_window=len(selection.ranked))
    if rule == "events_unscreenable":
        in_window = ", ".join(ev.id for ev, _ in selection.ranked[:3])
        warnings.append(
            f"catalogued event(s) in the acquisition window ({in_window}) publish no usable "
            "moment tensor — the coseismic screen cannot run (events_unscreenable)")

    # ---- 7. evidence + layers ------------------------------------------------
    layers: dict[str, dict] = {}
    best_screen = model.best_screen(screens)
    if best_screen is not None and status in ("SUPPORTED", "NOT_SUPPORTED"):
        from .model import prediction_field
        best_event = next((ev for ev, _ in selection.ranked if ev.id == best_screen["event_id"]), None)
        if best_event is not None:
            pred, _ = prediction_field(best, best_event, bundle.tile.epsg,
                                       plane_index=best_screen.get("nodal_plane", 0))
            if pred is not None:
                residual = np.where(best.mask, best.disp - pred, np.nan)
                layers["residual"] = encode_layer(
                    "residual", residual,
                    units="m (LOS, + = toward sensor; measurement minus Okada prediction)",
                    colormap="diverging_teal_terracotta",
                    note=f"measurement minus predicted field for event {best_event.id} "
                         f"(nodal plane {best_screen.get('nodal_plane', 0)})")

    layers["displacement"] = encode_layer(
        "displacement", best.disp, units="m (LOS, + = toward sensor)",
        colormap="diverging_teal_terracotta",
        note=f"referenced to the far-field median ({best.ref_offset_m * 1000:.1f} mm removed)")
    layers["threshold"] = encode_layer(
        "threshold", best.grids["thresholds"],
        units="m — per-pixel detection threshold max(k·σ, floor, iono)",
        colormap="sequential")
    layers["detected"] = encode_layer(
        "detected", best.grids["detected"],
        units="1 = toward sensor, 2 = away, 0 = not detected",
        colormap="binary", note="detection rule |d| > max(k·σ, 10 mm, iono floor)")

    # residual against the v1 uncatalogued-source fit, when one was attempted — shown
    # for inspection even when the fit did NOT converge (never presented as a candidate)
    if uncatalogued_fit is not None and uncatalogued_fit.get("_prediction") is not None:
        residual = np.where(best.mask, best.disp - uncatalogued_fit["_prediction"], np.nan)
        layers["residual"] = encode_layer(
            "residual", residual,
            units="m (LOS, + = toward sensor; measurement minus fitted-source prediction)",
            colormap="diverging_teal_terracotta",
            note=("residual against the v1 uncatalogued-source fit (vertical strike-slip patch)"
                  + ("" if uncatalogued_fit.get("converged") else " — fit did NOT converge; shown for inspection only")))

    data_used = [
        f"M1 GUNW los/sigma/coherence/conn_comp/iono_floor — pair {best.ref_time}..{best.sec_time} "
        f"(stack {best.stack_key}, granules {', '.join(best.granule_ids)})",
        f"R1 ComCat events ({len(events)} in query window, source {events_res.source})",
    ]
    if best.los_enu is not None:
        data_used.append("geometry los_enu/incidence (radarGrid) — P05")

    # ---- R2 ShakeMap: corroboration note, absence is a note never a failure --
    shakemap = fetch_shakemap_note(extra.get("best_event_id"), providers=providers,
                                   tile_bbox=tuple(bundle.tile.bbox_lonlat))
    if shakemap:
        cov = shakemap.get("coverage") or {}
        if cov.get("covers_tile"):
            cov_text = "tile inside the expected extent"
        elif cov.get("overlaps_tile"):
            cov_text = "tile partially overlaps the expected extent"
        elif cov:
            cov_text = "tile outside the expected extent"
        else:
            cov_text = "no tile-coverage check available"
        data_used.append(f"R2 ShakeMap extent for {shakemap.get('event_id')} "
                         f"(corroboration only; {cov_text})")
        provenance.append(Source(**shakemap["provenance"]) if isinstance(shakemap.get("provenance"), dict)
                          else Source(adapter="r2_shakemap", url_or_id=str(shakemap.get("url") or shakemap.get("event_id")),
                                      retrieved_at=""))

    evidence: list[EvidenceItem] = []
    s = best.summary
    credible = extra.get("credible_area_km2")
    if s.detected_pixels == 0:
        evidence.append(_evidence(
            "detection.null", "null_result",
            (f"No pixel crossed the detection threshold (median threshold "
             f"{s.threshold_median_m * 1000:.1f} mm, largest signal "
             f"{max(abs(s.max_toward_m), abs(s.max_away_m)) * 1000:.1f} mm). This means "
             f"'no detection above threshold' — NOT that the ground did not move."),
            support={"threshold_median_m": round(s.threshold_median_m, 5), "valid_pixels": s.valid_pixels},
            level="decision", source_ref=f"bundle:{best.stack_key}"))
    else:
        evidence.append(_evidence(
            "detection.summary", "detection",
            (f"{s.detected_pixels} pixels above threshold across "
             f"{s.affected_area_km2:.1f} km² (credible area {credible:.2f} km²); largest toward-signal "
             f"{s.max_toward_m * 1000:.1f} mm, away {abs(s.max_away_m) * 1000:.1f} mm; "
             f"{s.clusters['count']} cluster(s) >= {s.clusters['min_pixels']} px."),
            support={"affected_area_km2": round(s.affected_area_km2, 2),
                     "credible_area_km2": credible,
                     "max_toward_mm": round(s.max_toward_m * 1000, 1),
                     "max_away_mm": round(abs(s.max_away_m) * 1000, 1),
                     "clusters": s.clusters["count"]},
            level="decision", source_ref=f"bundle:{best.stack_key}"))
        if s.affected_area_km2 < credible:
            evidence.append(_evidence(
                "detection.sub_credible", "credible_area",
                f"The affected area is below the credible area ({credible:.2f} km² = max(1, 4 pixels)) — "
                "isolated stray pixels are not evidence of deformation.",
                level="caveat"))
    if s.null_screen:
        evidence.append(_evidence("detection.null_screen", "null_screen", s.null_screen.get("note", ""),
                                  support={k: v for k, v in s.null_screen.items() if k != "note"},
                                  level="caveat"))
    for scr in screens:
        if scr.get("skipped"):
            evidence.append(_evidence(f"forward_model.{scr['event_id']}.{scr.get('nodal_plane')}",
                                      "forward_model_skip",
                                      f"event {scr['event_id']} (plane {scr.get('nodal_plane')}): {scr['note']}",
                                      level="caveat"))
        else:
            evidence.append(_evidence(
                f"forward_model.{scr['event_id']}.{scr.get('nodal_plane')}", "forward_model",
                (f"event {scr['event_id']} (plane {scr.get('nodal_plane')}): predicted peak "
                 f"{scr['predicted_peak_m'] * 1000:.1f} mm vs measured "
                 f"{scr['measured_peak_m'] * 1000:.1f} mm (ratio {scr['amplitude_ratio']:.2f}), "
                 f"pattern correlation {scr['pattern_correlation']:.2f}. "
                 + ("Consistent with the model — correlation is not causation."
                    if status == "SUPPORTED" else "Consistency screen did not pass.")),
                support={"amplitude_ratio": round(scr["amplitude_ratio"], 3),
                         "pattern_correlation": round(scr["pattern_correlation"], 3),
                         "predicted_peak_m": round(scr["predicted_peak_m"], 5),
                         "measured_peak_m": round(scr["measured_peak_m"], 5)},
                level="corroboration" if status == "SUPPORTED" else "info",
                source_ref=f"r1_comcat:{scr['event_id']}"))
    if status == "NO_SIGNAL":
        evidence.append(_evidence(
            "signal.none", "null_result",
            (f"No catalog event (M >= 4, within {params.max_event_distance_km:.0f} km, inside the pair "
             "window) and no coherent cluster above the detection threshold — nothing happened "
             "on this tile in this window. This is a quiet outcome, NOT a tested-and-rejected quake."),
            level="decision", source_ref=f"bundle:{best.stack_key}"))
    if rule == "events_unscreenable":
        in_window = ", ".join(f"{ev.id} (M{ev.magnitude if ev.magnitude is not None else '?'})"
                              for ev, _ in selection.ranked[:3])
        evidence.append(_evidence(
            "events.unscreenable", "insufficient",
            (f"{len(selection.ranked)} catalogued event(s) inside the acquisition window "
             f"({in_window}) but none publishes a usable moment tensor — the Okada forward "
             "screen cannot run. This is a data gap in the catalog, NOT evidence about "
             "whether an earthquake occurred; the detection itself is reported as measured."),
            support={"n_events_in_window": len(selection.ranked),
                     "event_ids": [ev.id for ev, _ in selection.ranked[:5]]},
            level="caveat", source_ref="r1_comcat"))
    if status == "UNCATALOGUED_CANDIDATE" and uncatalogued_fit:
        pr = uncatalogued_fit.get("parameters") or {}
        evidence.append(_evidence(
            "uncatalogued.fit", "uncatalogued",
            (f"No catalog event within {params.max_event_distance_km:.0f} km in the window, yet a coherent "
             f"{s.affected_area_km2:.1f} km² signal (credible area {credible:.2f} km²) is explained by a vertical "
             f"{pr.get('sense', '?')} source: strike {pr.get('strike_deg', float('nan')):.0f}°, "
             f"{pr.get('length_m', float('nan')) / 1000:.1f} x {pr.get('width_m', float('nan')) / 1000:.1f} km, centroid depth "
             f"{pr.get('centroid_depth_m', float('nan')) / 1000:.1f} km, slip {abs(pr.get('slip_m', float('nan'))):.2f} m; "
             f"residual RMS {uncatalogued_fit.get('rms_m', float('nan')) * 1000:.1f} mm = "
             f"{uncatalogued_fit.get('rms_ratio', float('nan')):.2f} x field RMS. A candidate, not a detection of an "
             "earthquake: atmosphere, unwrapping errors or non-tectonic deformation can produce the same pattern."),
            support={**{k: pr.get(k) for k in ("strike_deg", "dip_deg", "length_m", "width_m",
                                               "centroid_depth_m", "slip_m", "sense")},
                     "rms_ratio": uncatalogued_fit.get("rms_ratio"),
                     "pattern_correlation": uncatalogued_fit.get("pattern_correlation"),
                     "pinned_parameters": uncatalogued_fit.get("pinned_parameters")},
            level="info"))
        evidence.append(_evidence(
            "uncatalogued.simplifications", "uncatalogued",
            "v1 simplifications: " + "; ".join(uncatalogued_fit.get("simplifications", [])) + ". "
            + "Weakly constrained: " + ", ".join(uncatalogued_fit.get("weakly_constrained", [])) + ".",
            level="caveat"))
    elif rule in ("uncatalogued_fit_not_converged", "no_catalog_event_fit_not_run"):
        reason = uncatalogued_fit.get("reason") if uncatalogued_fit else "the fit was not attempted"
        evidence.append(_evidence(
            "uncatalogued.not_converged", "uncatalogued",
            (f"No catalog event within {params.max_event_distance_km:.0f} km in the window and the "
             f"uncatalogued-source fit did not converge ({reason}). No source candidate is claimed. "
             "The v1 fit only represents a vertical strike-slip patch, so non-strike-slip "
             "deformation also lands here."),
            level="caveat"))
    elif rule == "sub_credible_no_catalog_event":
        evidence.append(_evidence(
            "uncatalogued.sub_credible", "uncatalogued",
            (f"Isolated or sub-credible signal; no catalog event within "
             f"{params.max_event_distance_km:.0f} km in the window."),
            level="caveat"))
    for note in flag_notes:
        # Stable content-derived id (final-prompt fix): Python hash() is salted per
        # process, so ids would differ between runs of the same bundle.
        flag_id = hashlib.sha1(note.encode("utf-8")).hexdigest()[:8]
        evidence.append(_evidence(f"flag.{flag_id}", "flag", note, level="caveat"))

    detail = {
        "params": params.to_dict(),
        "pairs_analysed": pair_records,
        "events_considered": [
            {"id": ev.id, "magnitude": ev.magnitude, "distance_km": round(dist, 1),
             "time": ev.time, "place": ev.place}
            for ev, dist in selection.ranked[: params.forward_top_events]
        ],
        "events_excluded": selection.excluded[:20],
        "forward_screens": [
            {k: v for k, v in scr.items() if k != "_pred_sub"} for scr in screens
        ],
        "decision": {"rule": rule, **extra},
        "uncatalogued": ({k: v for k, v in uncatalogued_fit.items() if k != "_prediction"}
                         if uncatalogued_fit is not None else None),
        "layers": {name: layer for name, layer in layers.items()},
        "narrative": _narrative(status, s, extra, best_screen, params, rule=rule),
    }

    bounds = core_bounds_lonlat(best.grid_x, best.grid_y, bundle.tile.epsg,
                                fallback=tuple(bundle.tile.bbox_lonlat))
    for layer in layers.values():            # plan §6.4: every layer payload carries its own bounds
        layer["bounds_lonlat"] = bounds
    layer_refs = [LayerRef(name=name, kind="raster", crs=f"EPSG:{bundle.tile.epsg}",
                           bounds_lonlat=tuple(bounds)) for name in layers]

    return PipelineResult(
        pipeline="earthquake_coseismic", tile=bundle.tile.id, epoch=bundle.epoch,
        status=status, summary=_headline(status, s, extra, params.detection_floor_m * 1000.0, rule=rule),
        raw_p=raw_p, confidence=None,
        evidence=evidence, layers=layer_refs, provenance=provenance,
        data_used=data_used, references_used=REFERENCES_USED,
        diagnostics={"pairs_analysed": pair_records, "null_screen": s.null_screen,
                     "events_considered": detail["events_considered"]},
        versions=_versions(),
        warnings=_dedupe(flag_notes + warnings),
        min_epochs_met=True, bundle_id=bundle.bundle_id, detail=detail,
    )


def _headline(status: str, s, extra: dict, floor_mm: float = 10.0, rule: str | None = None) -> str:
    largest = max(abs(s.max_toward_m), abs(s.max_away_m)) * 1000.0
    if status == "SUPPORTED":
        return (f"Possible coseismic signal — up to {largest:.0f} mm across {s.affected_area_km2:.1f} km², "
                f"consistent with event {extra.get('best_event_id')}.")
    if status == "UNCATALOGUED_CANDIDATE":
        return (f"Coherent {largest:.0f} mm signal over {s.affected_area_km2:.1f} km² with no catalog "
                "event in the acquisition window — candidate uncatalogued source.")
    if status == "NO_SIGNAL":
        return "Nothing above threshold here: no catalog event in the window and no coherent signal."
    if status == "INSUFFICIENT_EVIDENCE":
        if rule == "events_unscreenable":
            n = extra.get("n_events_in_window") or 0
            return (f"{n} catalogued event(s) in the acquisition window but none publishes a usable "
                    "moment tensor — the coseismic screen cannot run (detection reported as measured).")
        return "Not enough usable data to evaluate coseismic deformation on this tile."
    if s.detected_pixels == 0:
        return (f"No change detected above the {floor_mm:.0f} mm threshold "
                f"(largest signal {largest:.1f} mm).")
    return (f"{s.detected_pixels} pixels above threshold ({largest:.0f} mm max) but the evidence "
            "does not support a coseismic cause.")


def _narrative(status: str, s, extra: dict, best_screen, params: EqParams, rule: str | None = None) -> dict:
    floor_mm = params.detection_floor_m * 1000.0
    largest = max(abs(s.max_toward_m), abs(s.max_away_m)) * 1000.0
    if status == "SUPPORTED":
        headline = (f"Possible coseismic signal — up to {largest:.0f} mm across "
                    f"{s.affected_area_km2:.1f} km², consistent with event {extra.get('best_event_id')}.")
    elif status == "UNCATALOGUED_CANDIDATE":
        headline = (f"Coherent {largest:.0f} mm signal over {s.affected_area_km2:.1f} km² with no "
                    "catalog event in the acquisition window — candidate uncatalogued source.")
    elif status == "NO_SIGNAL":
        headline = "Nothing above threshold here: no catalog event in the window and no coherent signal."
    elif status == "INSUFFICIENT_EVIDENCE":
        if rule == "events_unscreenable":
            n = extra.get("n_events_in_window") or 0
            headline = (f"{n} catalogued event(s) in the acquisition window but none publishes a usable "
                        "moment tensor — the coseismic screen cannot run (detection reported as measured).")
        else:
            headline = "Not enough usable data to evaluate coseismic deformation on this tile."
    elif s.detected_pixels == 0:
        headline = f"No change detected above the {floor_mm:.0f} mm threshold (largest signal {largest:.1f} mm)."
    else:
        headline = (f"{s.detected_pixels} pixels above threshold ({largest:.0f} mm max) but the "
                    "evidence does not support a coseismic cause.")
    null_mean = s.null_screen.get("detected_fraction_mean", float("nan"))
    reliability = (
        f"Threshold max(k·σ, {floor_mm:.0f} mm, iono floor) over {s.valid_pixels} valid pixels "
        f"(valid fraction {s.valid_fraction:.2f}); null screen: "
        f"{s.null_screen.get('scenes', 0)} scenes, mean false-detection fraction {null_mean:.4f} (exploratory)."
    )
    return {
        "headline": headline,
        "reliability": reliability,
        "note": "raw_p is a within-tier rank score, not a calibrated probability (calibration lands with verdict 4.2).",
    }


def pair_note_lines(pair_records: list) -> list[str]:
    return [f"pair {r.get('ref_time')}..{r.get('sec_time')}: detected {r.get('detected_pixels', 0)} px"
            for r in pair_records if not r.get("skipped")]


def _dedupe(items: list) -> list:
    seen, out = set(), []
    for it in items:
        if it and it not in seen:
            seen.add(it)
            out.append(it)
    return out
