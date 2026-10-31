"""3.17 tsunami_ocean_evidence — registry entry point (heavy lane).

The registry defers this pipeline to the jobs runner (manifest execution: heavy); the runner calls
run(bundle, providers) with a progress callable in providers, and this module wires
fetch -> prep -> detect -> (simulate) -> classify into one typed PipelineResult
(engine/common/result.py). Every fetched record is cited in provenance; every degraded fetch is an
explicit evidence state — never a silent "no tsunami".
"""
from __future__ import annotations

import math

import numpy as np

from engine.common.result import EvidenceItem, JobCancelled, LayerRef, PipelineResult, Source

from . import model, prep
from .fetch import fetch_ffm_subfaults, fetch_tsunami_catalog
from engine.lib.geo import haversine_km

from .types import TsunamiParams

REFERENCES_USED = [
    "Okada (1985, 1992) DC3D elastic dislocation — engine/lib/okada.py",
    "Tanioka & Seno (1997) bathymetric-slope correction to coseismic vertical displacement",
    "Kajiura (1963) water-column transfer 1/cosh(k·d)",
    "Audusse et al. (2004) hydrostatic reconstruction (well-balanced NSWE); George (2008) dry-front speeds",
    "Wells & Coppersmith (1994) rupture-area scaling (all slip types)",
    "Zhao (2005) fast-sweeping eikonal solver",
    "NOAA NDBC DART bottom-pressure records; NOAA NCEI ETOPO1 Bedrock relief",
]
PIPELINE_VERSION = "1.0.0"


def _versions() -> dict:
    return {"pipeline": PIPELINE_VERSION, "lib.tsunami": "1.0", "lib.okada": "1.0",
            "result_contract": "1.0"}


def _ev(eid, kind, claim, support=None, level="info", source_ref=None) -> EvidenceItem:
    return EvidenceItem(id=eid, kind=kind, claim=claim, support=support, level=level, source_ref=source_ref)


def run(bundle, providers: dict | None = None) -> PipelineResult:
    providers = dict(providers or {})
    params = TsunamiParams.from_triggers(getattr(bundle.request, "triggers", None))
    progress = providers.get("progress")

    def report(stage: str, fraction: float) -> None:
        if progress is not None:
            try:
                progress(stage, fraction)
            except JobCancelled:
                raise            # the heavy-lane deadline/cancel MUST stop the pipeline
            except Exception:
                pass  # a broken progress channel never fails the science

    warnings: list[str] = []
    provenance: list[Source] = []
    evidence: list[EvidenceItem] = []
    tile = bundle.tile.id
    center = tuple(bundle.tile.center_lonlat)

    report("event", 0.02)
    ev_out = prep.pick_event(bundle, params, providers=providers)
    provenance.extend(ev_out.get("provenance") or [])
    if ev_out.get("degraded"):
        reason = ev_out.get("error") or "event provider reported degraded results"
        return _insufficient(f"the triggering earthquake could not be retrieved: {reason}",
                             tile, bundle, provenance, warnings)
    event = ev_out.get("event")
    if event is None:
        note = ev_out.get("note") or "no catalog event in the request window"
        return _insufficient(f"no event to evaluate: {note}", tile, bundle, provenance, warnings)

    origin_ms, origin_iso = prep.origin_of(event, bundle)
    lat, lon = float(event["lat"]), float(event["lon"])
    mag = event.get("magnitude")
    evidence.append(_ev(
        "event", "event",
        f"Evaluating event {event['id']} ({event.get('place') or 'place unknown'}): "
        f"M{mag if mag is not None else '?'} at {origin_iso}, {event.get('depth_km', '?')} km deep, "
        f"{haversine_km(lat, lon, center[1], center[0]):.0f} km from the tile centre.",
        support={"id": event["id"], "magnitude": mag, "origin": origin_iso,
                 "distance_km": round(haversine_km(lat, lon, center[1], center[0]), 1)},
        level="info", source_ref=f"r1_comcat:{event['id']}"))

    # ---- stations ----------------------------------------------------------
    report("stations", 0.06)
    stations = prep.pick_stations(event, params, providers=providers)
    records = prep.fetch_station_records(stations, origin_ms, providers=providers)
    for det in records:
        provenance.extend(getattr(det, "_provenance", []) or [])
    attempted = len(records)
    usable = [d for d in records if d.availability.get("state") == "available"]
    missing = [d for d in records if d.availability.get("state") == "missing"]
    if missing:
        evidence.append(_ev(
            "stations.missing", "insufficient",
            f"{len(missing)} of {attempted} station record(s) could not be retrieved ("
            + "; ".join(f"{d.station_id}: {d.availability.get('reason')}" for d in missing[:3]) + ")",
            level="caveat"))

    # ---- domain (bathymetry) ----------------------------------------------
    report("bathymetry", 0.10)
    domain = prep.build_domain(event, params, providers=providers)
    provenance.extend((domain["bathy"].get("provenance_records") or []))
    for note in domain.get("notes", []):
        warnings.append(note)
    grid = domain["bathy"].get("grid")
    bathy_error = domain["bathy"].get("error")
    if grid is None:
        warnings.append(f"bathymetry unavailable: {bathy_error} — travel-time mask and simulation skipped")

    # ---- source + eikonal (needed for the seismic mask and the sim) --------
    dz = None
    ray_time_min: dict = {}
    src: dict | None = None
    if grid is not None:
        ffm = fetch_ffm_subfaults(str(event["id"]), providers=providers)
        provenance.extend(ffm.get("provenance") or [])
        if ffm.get("error"):
            warnings.append(ffm["error"])
        src = model.source_from_event(event, grid, ffm.get("subfaults") or None, params)
        if src.get("error"):
            warnings.append(src["error"])
            src = None
        else:
            dz = src["dz"]
            report("eikonal", 0.18)
            ray_time_min = model.eikonal_ray_times(grid, lat, lon, dz, stations)
    providers["ray_time_min"] = ray_time_min

    # ---- detection ---------------------------------------------------------
    report("detection", 0.25)
    detections = model.detect_stations(records, origin_ms, params, providers=providers)

    # ---- simulation --------------------------------------------------------
    sim = None
    comparisons = None
    if params.run_simulation and grid is not None and src is not None:
        report("simulation", 0.30)
        try:
            sim = model.simulate(src, grid, stations, params, progress=report)
        except Exception as ex:            # a solver failure is an honest warning, not a crash
            warnings.append(f"simulation failed: {type(ex).__name__}: {ex}")
    if sim is not None and detections:
        comparisons = model.compare_stations(detections, sim, params)

    # ---- classification ----------------------------------------------------
    screening = model.screening_for(event)
    if screening.get("met"):
        evidence.append(_ev("screening", "screening",
                            "Seismic screening met (heuristic): " + "; ".join(screening["reasons"]),
                            level="info"))
    elif screening.get("notes"):
        evidence.append(_ev("screening", "screening",
                            "Seismic screening not met: " + "; ".join(screening["notes"]),
                            level="caveat"))

    evidence_assessment = model.classify(detections, comparisons, screening, attempted)
    status = {"consistent": "SUPPORTED", "possible": "POSSIBLE", "none-detected": "NOT_SUPPORTED",
              "insufficient": "INSUFFICIENT_EVIDENCE"}[evidence_assessment["level"]]
    raw_p = model.rank_score(evidence_assessment, detections, comparisons)
    if status == "INSUFFICIENT_EVIDENCE":
        raw_p = None                     # nothing was measured: no score, honestly

    # the classification itself is a decision evidence item (headline + the criteria that fired)
    evidence.append(_ev(
        "evidence.level", "classification",
        evidence_assessment.get("headline", status)
        + (" Criteria: " + "; ".join(evidence_assessment.get("reasons", [])) if evidence_assessment.get("reasons") else ""),
        support={"level": evidence_assessment["level"], "contributing": evidence_assessment.get("contributing", [])},
        level="decision"))

    # ---- evidence items ----------------------------------------------------
    for d in records:
        if d.availability.get("state") == "available" and d.trigger_ms is not None:
            evidence.append(_ev(
                f"detection.{d.station_id}", "detection",
                (f"DART {d.station_id} ({d.dist_km:.0f} km away, {d.kind}) triggered "
                 f"{d.arrival_min:+.0f} min after the origin with max residual "
                 f"{(d.max_amplitude_m or 0) * 100:.0f} cm (sigma {(d.sigma_noise_m or 0) * 100:.1f} cm, "
                 f"period {d.dominant_period_min and round(d.dominant_period_min) or '—'} min)."),
                support={"station": d.station_id, "arrival_min": d.arrival_min,
                         "max_amplitude_m": d.max_amplitude_m, "sigma_noise_m": d.sigma_noise_m,
                         "dist_km": d.dist_km},
                level="corroboration", source_ref=f"r15_dart:{d.station_id}"))
        elif d.availability.get("state") == "available":
            evidence.append(_ev(
                f"detection.{d.station_id}", "null_result",
                (f"DART {d.station_id} ({d.dist_km:.0f} km away, {d.kind}) shows no anomalous wave "
                 f"above {params.trigger_k:g}·sigma in the event window "
                 f"(sigma {(d.sigma_noise_m or 0) * 100:.1f} cm). This is limited to the sensors, "
                 "cadence and noise actually available — not proof of absence."),
                level="decision", source_ref=f"r15_dart:{d.station_id}"))
    for c in (comparisons or []):
        if c.get("sim_arrival_min") is not None and c.get("obs_arrival_min") is not None:
            evidence.append(_ev(
                f"comparison.{c['station_id']}", "comparison",
                (f"{c['station_id']}: observed {c['obs_arrival_min']:+.0f} min vs simulated "
                 f"{c['sim_arrival_min']:+.0f} min (residual {c['arrival_residual_min']:+.0f} min), "
                 f"amplitude ratio {c.get('amplitude_ratio', float('nan')):.2f} — "
                 + ("consistent within the documented tolerances (±15 min, ×3)."
                    if c.get("consistent") else "outside the documented tolerances.")),
                support={k: c[k] for k in ("arrival_residual_min", "amplitude_ratio", "consistent")},
                level="corroboration" if c.get("consistent") else "caveat"))
    for caveat in evidence_assessment.get("caveats", []):
        evidence.append(_ev(f"caveat.{len(evidence)}", "caveat", caveat, level="caveat"))
    if not params.run_simulation:
        warnings.append("simulation disabled by parameters — the evidence level is capped at 'possible' "
                        "while a trigger exists")

    # ---- corroboration: historical catalog (absence is a note) --------------
    report("catalog", 0.97)
    catalog = fetch_tsunami_catalog((lon - 5, lat - 5, lon + 5, lat + 5), providers=providers)
    if catalog.get("degraded"):
        evidence.append(_ev("catalog", "note",
                            f"Historical tsunami catalog unavailable (corroboration only): {catalog.get('error')}",
                            level="caveat"))
    elif catalog.get("events"):
        n = len(catalog["events"])
        evidence.append(_ev("catalog", "corroboration",
                            f"{n} historical tsunami event(s) within 1000 km of the epicentre in the "
                            "NOAA/WDS catalog (corroboration of regional tsunamigenicity, not of this event).",
                            support={"n_events": n}, level="info"))

    # ---- layers -------------------------------------------------------------
    layers = [LayerRef(name="detections", kind="vector", crs="EPSG:4326")]
    detail: dict = {
        "params": params.to_dict(),
        "event": {"id": event["id"], "magnitude": mag, "time": origin_iso,
                  "lat": lat, "lon": lon, "depth_km": event.get("depth_km"),
                  "place": event.get("place")},
        "screening": screening,
        "stations": [d.to_dict() for d in records],
        "comparisons": comparisons,
        "evidence_level": evidence_assessment,
        "source": ({k: src[k] for k in ("source_kind", "detail", "max_abs_dz", "notes", "subfault_count")}
                   if src else None),
        "simulation": (sim["diagnostics"] if sim else None),
    }
    if sim is not None:
        # D1 fix: canonical LayerPayloads (engine.lib.layer_payload) — b64 grids, flat [W,S,E,N]
        # bounds, vmin/vmax, note. The solver's grids have row 0 = SOUTH (lat0 + j*res); the
        # layer contract is row 0 = NORTH, so rows are flipped. The domain spans
        # 2*domain_half_deg of latitude (10 deg by default): per DEC10 the engine resamples rows
        # to be linear in Web-Mercator Y before encoding (the client never reprojects) and the
        # note says so.
        w, s_, e, n_ = (float(v) for v in domain["bbox"])
        from engine.lib.layer_payload import encode_layer as canonical_layer, resample_mercator_rows

        def canonical(field: str, units: str, colormap: str) -> dict:
            a = np.asarray(sim["fields"][field], dtype=np.float32)[::-1, :]   # row 0 -> north
            a, resample_note = resample_mercator_rows(a, lat_top=n_, lat_bottom=s_)
            finite = a[np.isfinite(a)]
            span = float(np.abs(finite).max()) if finite.size else 1.0
            return canonical_layer(
                field, a, units=units, colormap=colormap,
                vmin=-span if colormap.startswith("diverging") else 0.0,
                vmax=span,
                bounds_lonlat=[w, s_, e, n_],
                note=(f"{field} over the simulation domain"
                      + (f"; {resample_note}" if resample_note else "")),
            )

        detail["layers"] = {
            "max_eta": canonical("max_eta", "m (max |eta| over the run)", "diverging_teal_terracotta"),
            "arrival": canonical("arrival", "s (first arrival of |eta| > threshold)", "sequential"),
        }
        layers.append(LayerRef(name="max_eta", kind="raster", crs="EPSG:4326"))
        layers.append(LayerRef(name="arrival", kind="raster", crs="EPSG:4326"))

    summary = (f"{evidence_assessment['headline']}"
               if evidence_assessment.get("headline") else f"tsunami_ocean_evidence: {status.lower()}")

    return PipelineResult(
        pipeline="tsunami_ocean_evidence", tile=tile, epoch=bundle.epoch,
        status=status, summary=summary, raw_p=raw_p, confidence=None,
        evidence=evidence, layers=layers, provenance=provenance,
        data_used=[f"R1 ComCat event {event['id']}"] +
                  [f"R15 DART {d.station_id} ({d.kind})" for d in records
                   if d.availability.get("state") == "available"] +
                  ([f"R16 ETOPO1 {grid['nx']}x{grid['ny']} @ {grid['res_arcmin']}'"] if grid else []),
        references_used=REFERENCES_USED,
        diagnostics={"stations": detail["stations"], "screening": screening,
                     "simulation": detail["simulation"]},
        versions=_versions(),
        warnings=warnings, min_epochs_met=True, bundle_id=bundle.bundle_id, detail=detail,
    )


def _insufficient(reason: str, tile: str, bundle, provenance: list, warnings: list) -> PipelineResult:
    return PipelineResult(
        pipeline="tsunami_ocean_evidence", tile=tile, epoch=bundle.epoch,
        status="INSUFFICIENT_EVIDENCE", summary=reason, raw_p=None, confidence=None,
        evidence=[_ev("insufficient", "insufficient", reason, level="caveat")],
        layers=[], provenance=provenance, data_used=[], references_used=REFERENCES_USED,
        diagnostics={}, versions=_versions(), warnings=warnings,
        min_epochs_met=True, bundle_id=bundle.bundle_id,
        detail={"decision": {"rule": "insufficient_evidence"}})
