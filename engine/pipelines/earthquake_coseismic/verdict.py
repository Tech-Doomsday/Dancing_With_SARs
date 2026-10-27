"""3.01 earthquake_coseismic — registry entry point.

The registry calls exactly this: run(bundle: SceneBundle) -> Mapping.
The pipeline sees ONLY the bundle (no retriever, no other pipeline's output).
Flow and status table: docs/IMPLEMENTATION_PLAN.md 4.2 and 8.4.
"""
from __future__ import annotations

import math

import numpy as np

from engine.common.config import load_common_config

from . import detection as det
from . import model
from . import prep
from . import uncatalogued
from .fetch import fetch_events
from .lib_local import encode_layer
from .types import EqParams

REFERENCES_USED = [
    "Okada (1985, 1992) DC3D elastic dislocation — engine/lib/okada.py",
    "Hanks & Kanamori (1979) moment–magnitude relation",
    "Wells & Coppersmith (1994) rupture-area scaling (all slip types)",
    "Rodriguez & Martin (1992) coherence–phase Cramér-Rao sigma (via P06 sigma_m)",
]


def _insufficient(reasons: list[str], warnings: list[str] | None = None) -> dict:
    return {
        "status": "INSUFFICIENT_EVIDENCE",
        "raw_p": None,
        "evidence": [{"kind": "insufficient", "text": r} for r in reasons],
        "layers": [],
        "data_used": [],
        "references_used": REFERENCES_USED,
        "warnings": warnings or [],
        "detail": {"decision": {"rule": "insufficient_evidence"}, "pairs_analysed": []},
    }


def run(bundle) -> dict:
    params = EqParams.from_triggers(getattr(bundle.request, "triggers", None))
    cfg = load_common_config()
    warnings: list[str] = []

    # engine flags that matter for LOS honesty (surfaced into evidence)
    flag_notes = [
        f"{f.code}: {f.message}" for f in bundle.flags
        if f.code in ("los_convention_suspect", "pairs_flipped", "fetch_error")
    ]

    stack = bundle.stack()
    if stack is None or stack.interferometric is None or not stack.interferometric.pairs:
        out = _insufficient(["no usable NISAR data (M1 interferograms) for this tile/window"], flag_notes)
        out["data_used"] = []
        return out

    # ---- 1. pair selection -------------------------------------------------
    indices, reasons = prep.select_pairs(bundle, params)
    if not indices:
        return _insufficient(reasons, flag_notes)

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
            "ref_scatter_m": getattr(analysis, "ref_scatter_m", None),
            "detected_pixels": summary.detected_pixels,
            "affected_area_km2": summary.affected_area_km2,
            "clusters": summary.clusters, "null_screen": summary.null_screen,
            "flags": analysis.flags,
            "selected": False,
        })

    if not analyses:
        return _insufficient(
            [r.get("reason", "pair analysis failed") for r in pair_records] or ["no pair produced a usable analysis"],
            flag_notes + warnings)

    # best pair = largest affected area (tie-break: more valid pixels), deterministic
    best = max(analyses, key=lambda a: (a.summary.affected_area_km2, a.summary.valid_pixels, -a.pair_index))
    for rec in pair_records:
        rec["selected"] = (rec.get("granule_ids") == list(best.granule_ids))

    # ---- 4. events (fetch failure => INSUFFICIENT_EVIDENCE, never "no event")
    bbox = tuple(bundle.tile.bbox_lonlat)  # (W, S, E, N)
    events_res = fetch_events(bbox, best.ref_time, best.sec_time, params)
    if events_res.error or events_res.degraded:
        reason = events_res.error or "event provider reported degraded results"
        return _insufficient([f"catalog events could not be retrieved: {reason}"],
                             flag_notes + warnings + pair_note_lines(pair_records))
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

    # ---- 5b. uncatalogued-source branch (docs 8.6): only when there is a credible signal
    #          and genuinely NO catalog event in window/range (never when events merely lack a mechanism)
    fit = None
    catalog_events = len(selection.ranked)
    credible_km2 = max(1.0, best.summary.pixel_area_km2 * params.credible_pixel_factor)
    if (best.summary.detected_pixels > 0 and catalog_events == 0
            and model.best_screen(screens) is None
            and best.summary.affected_area_km2 >= credible_km2):
        fit = uncatalogued.fit_uncatalogued(best, best.summary, bundle.tile.epsg, params)
        if not fit.get("attempted"):
            warnings.append(f"uncatalogued-source fit not attempted: {fit.get('reason')}")

    # ---- 6. decision ---------------------------------------------------------
    status, raw_p, rule, extra = model.decide(best.summary, screens, events_known=True, params=params,
                                              catalog_events=catalog_events, fit=fit)

    # ---- 7. evidence + layers ------------------------------------------------
    from .model import prediction_field
    layers: dict[str, dict] = {}
    best_screen = model.best_screen(screens)
    residual_note = ""
    if best_screen is not None and status in ("SUPPORTED", "NOT_SUPPORTED"):
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
                residual_note = f" Residual layer computed against event {best_event.id}."

    if fit is not None and fit.get("attempted") and fit.get("_prediction") is not None:
        residual = np.where(best.mask, best.disp - fit["_prediction"], np.nan)
        layers["residual"] = encode_layer(
            "residual", residual,
            units="m (LOS, + = toward sensor; measurement minus fitted-source prediction)",
            colormap="diverging_teal_terracotta",
            note=("residual against the v1 uncatalogued-source fit (vertical strike-slip patch)"
                  + ("" if fit.get("converged") else " - fit did NOT converge; shown for inspection only")))

    layers["displacement"] = encode_layer(
        "displacement", best.disp, units="m (LOS, + = toward sensor)",
        colormap="diverging_teal_terracotta",
        note=f"referenced to the tile median ({best.ref_offset_m * 1000:.1f} mm removed)")
    layers["threshold"] = encode_layer(
        "threshold", best.grids["thresholds"],
        units="m — per-pixel detection threshold max(k·σ, floor, iono)",
        colormap="sequential")
    layers["detected"] = encode_layer(
        "detected", best.grids["detected"],
        units="1 = toward sensor, 2 = away, 0 = not detected",
        colormap="binary", note="detection rule |d| > max(k·σ, 10 mm, iono floor)")

    data_used = [
        f"M1 GUNW los/sigma/coherence/conn_comp/iono_floor — pair {best.ref_time}..{best.sec_time} "
        f"(stack {best.stack_key}, granules {', '.join(best.granule_ids)})",
        f"R1 ComCat events ({len(events)} in query window, source {events_res.source})",
    ]
    if best.los_enu is not None:
        data_used.append("geometry los_enu/incidence (radarGrid) — P05")

    evidence: list[dict] = []
    s = best.summary
    if s.detected_pixels == 0:
        evidence.append({
            "kind": "null_result",
            "text": (f"No pixel crossed the detection threshold (median threshold "
                     f"{s.threshold_median_m * 1000:.1f} mm, largest signal "
                     f"{max(abs(s.max_toward_m), abs(s.max_away_m)) * 1000:.1f} mm). This means "
                     f"'no detection above threshold' — NOT that the ground did not move."),
        })
    else:
        evidence.append({
            "kind": "detection",
            "text": (f"{s.detected_pixels} pixels above threshold across "
                     f"{s.affected_area_km2:.1f} km² (credible area "
                     f"{extra['credible_area_km2']:.2f} km²); largest toward-signal "
                     f"{s.max_toward_m * 1000:.1f} mm, away {abs(s.max_away_m) * 1000:.1f} mm; "
                     f"{s.clusters['count']} cluster(s) >= {s.clusters['min_pixels']} px."),
        })
        if s.affected_area_km2 < extra["credible_area_km2"]:
            evidence.append({
                "kind": "credible_area",
                "text": f"The affected area is below the credible area "
                        f"({extra['credible_area_km2']:.2f} km² = max(1, 4 pixels)) — "
                        "isolated stray pixels are not evidence of deformation.",
            })
    if s.null_screen:
        evidence.append({"kind": "null_screen", "text": s.null_screen.get("note", "")})
    for scr in screens:
        if scr.get("skipped"):
            evidence.append({"kind": "forward_model_skip",
                             "text": f"event {scr['event_id']} (plane {scr.get('nodal_plane')}): {scr['note']}"})
        else:
            evidence.append({
                "kind": "forward_model",
                "text": (f"event {scr['event_id']} (plane {scr.get('nodal_plane')}): predicted peak "
                         f"{scr['predicted_peak_m'] * 1000:.1f} mm vs measured "
                         f"{scr['measured_peak_m'] * 1000:.1f} mm (ratio {scr['amplitude_ratio']:.2f}), "
                         f"pattern correlation {scr['pattern_correlation']:.2f}. "
                         + ("Consistent with the model — correlation is not causation."
                            if status == "SUPPORTED" else "Consistency screen did not pass.")),
            })
    if status == "NOT_MODELLED":
        evidence.append({
            "kind": "not_modellable",
            "text": (f"{catalog_events} catalog event(s) qualify by window and distance, but none could be "
                     "modelled; see the forward_model_skip items for the reason per event. No fault "
                     "geometry was invented."),
        })
    if status == "NO_SIGNAL":
        evidence.append({
            "kind": "no_signal",
            "text": (f"No catalog event (M>={4}, within {params.max_event_distance_km:.0f} km, in the pair "
                     "window) and no coherent cluster above threshold."),
        })
    for note in flag_notes:
        evidence.append({"kind": "flag", "text": note})
    if status == "UNCATALOGUED_CANDIDATE" and fit is not None and fit.get("converged"):
        pr = fit["parameters"]
        evidence.append({
            "kind": "uncatalogued",
            "text": (f"No catalog event within {params.max_event_distance_km:.0f} km in the window, yet a coherent "
                     f"{s.affected_area_km2:.1f} km² signal (credible area {extra['credible_area_km2']:.2f} km²) is "
                     f"explained by a vertical {pr['sense']} source: strike {pr['strike_deg']:.0f}°, "
                     f"{pr['length_m'] / 1000:.1f} x {pr['width_m'] / 1000:.1f} km, centroid depth "
                     f"{pr['centroid_depth_m'] / 1000:.1f} km, slip {abs(pr['slip_m']):.2f} m; residual RMS "
                     f"{fit['rms_m'] * 1000:.1f} mm = {fit['rms_ratio']:.2f} x field RMS. A candidate, not a "
                     "detection of an earthquake: atmosphere, unwrapping errors or non-tectonic deformation "
                     "can produce the same pattern."),
        })
        evidence.append({"kind": "uncatalogued_simplifications",
                         "text": "v1 simplifications: " + "; ".join(fit["simplifications"]) + "."})
    elif status == "UNCATALOGUED_CANDIDATE":
        evidence.append({
            "kind": "uncatalogued",
            "text": ("A coherent deformation signal above the credible area exists and catalog events were "
                     "present in the window but could not be screened (no usable mechanism); the "
                     "event-to-signal link is unevaluated."),
        })
    elif rule == "uncatalogued_fit_not_converged":
        reason = fit.get("reason") if fit else "fit was not attempted"
        evidence.append({
            "kind": "uncatalogued",
            "text": (f"No catalog event within {params.max_event_distance_km:.0f} km in the window and the "
                     f"uncatalogued-source fit did not converge ({reason}). No source candidate is claimed. "
                     "The v1 fit only represents a vertical strike-slip patch, so non-strike-slip "
                     "deformation also lands here."),
        })
    elif rule == "sub_credible_no_catalog_event":
        evidence.append({"kind": "uncatalogued",
                         "text": (f"Isolated or sub-credible signal; no catalog event within "
                                  f"{params.max_event_distance_km:.0f} km in the window.")})

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
        "uncatalogued": ({k: v for k, v in fit.items() if k != "_prediction"} if fit is not None else None),
        "layers": {name: layer for name, layer in layers.items()},
        "narrative": _narrative(status, s, extra, best_screen, params),
    }

    return {
        "status": status,
        "raw_p": (float(raw_p) if raw_p is not None and math.isfinite(raw_p) else None),
        "evidence": evidence,
        "layers": list(layers.keys()),
        "data_used": data_used,
        "references_used": REFERENCES_USED,
        "warnings": _dedupe(flag_notes + warnings),
        "detail": detail,
    }


def _narrative(status: str, s, extra: dict, best_screen, params: EqParams) -> dict:
    floor_mm = params.detection_floor_m * 1000.0
    largest = max(abs(s.max_toward_m), abs(s.max_away_m)) * 1000.0
    if status == "SUPPORTED":
        headline = (f"Possible coseismic signal — up to {largest:.0f} mm across "
                    f"{s.affected_area_km2:.1f} km², consistent with event {extra.get('best_event_id')}.")
    elif status == "UNCATALOGUED_CANDIDATE":
        headline = (f"Coherent {largest:.0f} mm signal over {s.affected_area_km2:.1f} km² with no "
                    "catalog event in the acquisition window — candidate uncatalogued source.")
    elif status == "INSUFFICIENT_EVIDENCE":
        headline = "Not enough usable data to evaluate coseismic deformation on this tile."
    elif status == "NO_SIGNAL":
        headline = (f"No catalog event in the window and no coherent change above the {floor_mm:.0f} mm "
                    f"threshold (largest signal {largest:.1f} mm). Nothing to test on this tile — this is "
                    "'no detection above threshold', NOT 'the ground did not move'.")
    elif status == "NOT_MODELLED":
        headline = (f"{s.detected_pixels} pixels above threshold ({largest:.0f} mm max) and a catalog event "
                    "in the window, but no event could be modelled (magnitude, depth or mechanism missing) — "
                    "the coseismic link is unevaluated.")
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
        "note": "raw_p is an uncalibrated rank score (Pearson r of the better fault plane), not a probability.",
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
