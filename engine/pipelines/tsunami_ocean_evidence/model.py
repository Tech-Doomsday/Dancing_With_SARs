"""3.17 tsunami_ocean_evidence — the science: screening, detection, source, simulation, evidence.

Every step is a deterministic, seeded, unit-testable function of its inputs (no network, no
filesystem). The heavy part — the NSWE solver — runs in chunks so the caller can report progress
(port design decision 9).
"""
from __future__ import annotations

import math

import numpy as np

from engine.lib.tsunami import (DetectOptions, NsweSolver, SimParams, classify_evidence,
                                compute_deformation, detect_tsunami_signal, kajiura_filter,
                                screen_seismic, station_consistency, travel_time_field,
                                travel_time_at, uniform_fault_from_scaling)
from engine.lib.tsunami.source import SourceOptions, Subfault, build_source_field

from .types import StationDetection, TsunamiParams


def screening_for(event: dict) -> dict:
    """Seismic screening heuristic from the event's magnitude/depth/mechanism."""
    planes = event.get("nodal_planes") or []
    rake = None
    if planes:
        try:
            rake = float(planes[0][2])
        except (TypeError, ValueError, IndexError):
            rake = None
    mag = event.get("magnitude")
    depth = event.get("depth_km")
    if mag is None or depth is None:
        return {"met": False, "reasons": [], "notes": ["event magnitude or depth missing — screening not evaluable"]}
    return screen_seismic(float(mag), float(depth), rake)


def detect_stations(stations: list, origin_ms: float, params: TsunamiParams,
                    providers: dict | None = None) -> list:
    """Per-station detection on de-tided records; the seismic mask is extended by the eikonal ray
    time to the station (a water wave cannot outrun ~85 % of it — documented mask)."""
    ray_time_min = (providers or {}).get("ray_time_min") or {}
    detections = []
    for det in stations:
        if det.availability.get("state") != "available":
            continue
        series = getattr(det, "_series", None)
        if not series or len(series.get("t", [])) == 0:
            det.availability = {"state": "missing", "reason": "empty series"}
            continue
        mask_until = ray_time_min.get(det.station_id)
        opts = DetectOptions(k=params.trigger_k, detide_half_window_h=params.detide_half_window_h,
                             seismic_mask_min=params.seismic_mask_min, mask_until_min=mask_until)
        out = detect_tsunami_signal(det.station_id, series, origin_ms, provenance=None, opts=opts)
        det.availability = out["availability"]
        det.trigger_ms = out["trigger_ms"]
        det.arrival_min = out["arrival_min"]
        det.sigma_noise_m = None if math.isnan(out["sigma_noise"]) else out["sigma_noise"]
        det.max_amplitude_m = out["max_amplitude_m"]
        det.dominant_period_min = out["dominant_period_min"]
        det.pre_origin_excursion = out["pre_origin_excursion"]
        det.caveats = out["caveats"]
        det.n_samples = out["n_samples"]
        detections.append(out)
    return detections


def eikonal_ray_times(grid: dict, origin_lat: float, origin_lon: float, dz: np.ndarray | None,
                      stations: list) -> dict:
    """Eikonal travel times from the deformed region -> {station_id: minutes} (NaN skipped)."""
    bed = np.asarray(grid["bed"], dtype=np.float64)
    seed = dz if dz is not None else np.ones_like(bed)
    T = travel_time_field(grid["res_arcmin"], grid["lat0"], bed, seed)
    out = {}
    for st in stations:
        t = travel_time_at(T, grid["lat0"], grid["lon0"], grid["res_arcmin"], st["lat"], st["lon"])
        if math.isfinite(t):
            out[st["id"]] = t / 60.0
    return out


def source_from_event(event: dict, grid: dict, ffm_subfaults: list | None,
                      params: TsunamiParams) -> dict:
    """Source field: FFM subfaults when published, else the MT plane + WC94 scaling (labelled)."""
    if ffm_subfaults:
        subs = ffm_subfaults
        kind, detail = "usgs-finite-fault", f"{len(subs)} FFM subfaults (USGS finite-fault product)"
    else:
        planes = event.get("nodal_planes") or []
        if not planes:
            return {"error": "no moment-tensor nodal plane published and no finite-fault model — "
                             "no source can be constructed without inventing one"}
        plane = planes[0]
        fault, notes = uniform_fault_from_scaling(float(event["magnitude"]),
                                                  float(event.get("depth_km") or 10.0),
                                                  float(event["lat"]), float(event["lon"]),
                                                  tuple(float(c) for c in plane))
        subs = [fault]
        kind, detail = "usgs-mt-uniform", "single uniform plane from the MT nodal plane + WC94 scaling"
    bed = np.asarray(grid["bed"], dtype=np.float64)
    src = build_source_field(grid["res_arcmin"], grid["lat0"], grid["lon0"], bed, subs,
                             kind, SourceOptions(slope_correction=True, kajiura_filter=True),
                             detail=detail)
    src["subfault_count"] = len(subs)
    return src


def simulate(src: dict, grid: dict, stations: list, params: TsunamiParams,
             progress=None) -> dict:
    """NSWE run on the ETOPO1 domain with gauges at the station positions. Chunked advance so the
    runner can report progress (the heavy-lane progress file)."""
    bed = np.asarray(grid["bed"], dtype=np.float64)
    eta0 = np.asarray(src["eta0"], dtype=np.float64)
    sim_params = SimParams(res_arcmin=grid["res_arcmin"], sim_hours=params.sim_hours,
                           manning_n=params.manning_n, order=params.order,
                           arrival_threshold_m=params.arrival_threshold_m,
                           domain_half_deg=params.domain_half_deg, sponge_enabled=params.sponge_enabled)
    gauge_specs = [{"station_id": st["id"], "lat": st["lat"], "lon": st["lon"]} for st in stations]
    solver = NsweSolver(grid["res_arcmin"], grid["lat0"], grid["lon0"], bed, eta0, sim_params,
                        gauge_specs)
    t_end = params.sim_hours * 3600.0
    while solver.sim_time < t_end:
        solver.advance(100, t_end)
        if progress is not None:
            progress("simulation", solver.progress_info["fraction"])
    out = solver.finish()
    out["diagnostics"]["grid"] = {"nx": grid["nx"], "ny": grid["ny"], "res_arcmin": grid["res_arcmin"],
                                  "lat0": grid["lat0"], "lon0": grid["lon0"]}
    return out


def compare_stations(detections: list, sim: dict | None, params: TsunamiParams) -> list:
    """Per-station obs-vs-sim comparisons (arrivals from the gauge series, ±tol)."""
    if sim is None:
        return None
    gauges = {g["station_id"]: g for g in sim.get("gauges", [])}
    comparisons = []
    for det in detections:
        st_id = det["station_id"]
        g = gauges.get(st_id)
        sim_arrival = None
        sim_amp = None
        note = ""
        if g is None:
            note = "station outside the simulation domain"
        else:
            t_min = np.asarray(g["t_min"], dtype=np.float64)
            eta = np.asarray(g["eta"], dtype=np.float64)
            above = np.where(np.abs(eta) > params.arrival_threshold_m)[0]
            if above.size:
                sim_arrival = float(t_min[above[0]])
                sim_amp = float(np.max(np.abs(eta)))
            else:
                note = "no simulated signal above the model arrival threshold at this station"
        obs_arrival = det.get("arrival_min")
        obs_amp = det.get("max_amplitude_m")
        consistent = station_consistency(obs_arrival, obs_amp, sim_arrival, sim_amp) \
            if sim_arrival is not None else None
        residual = (obs_arrival - sim_arrival) if (obs_arrival is not None and sim_arrival is not None) else None
        ratio = (obs_amp / sim_amp) if (obs_amp is not None and sim_amp) else None
        if note == "" and consistent is False:
            if residual is not None and abs(residual) > params.arrival_tol_min:
                note = f"arrival residual {residual:+.0f} min exceeds ±{params.arrival_tol_min:g} min"
            elif ratio is not None:
                note = f"amplitude ratio {ratio:.2f} outside ×{params.amplitude_factor:g}"
        comparisons.append({"station_id": st_id, "obs_arrival_min": obs_arrival,
                            "obs_max_amplitude_m": obs_amp, "sim_arrival_min": sim_arrival,
                            "sim_max_amplitude_m": sim_amp, "arrival_residual_min": residual,
                            "amplitude_ratio": ratio, "consistent": consistent, "note": note})
    return comparisons


def classify(detections: list, comparisons: list | None, screening: dict,
             n_attempted: int) -> dict:
    """Five-level evidence classification (engine.lib.tsunami.classify_evidence)."""
    usable = [d for d in detections if d["availability"]["state"] == "available"]
    return classify_evidence(usable, comparisons, screening=screening, n_detections_attempted=n_attempted)


def rank_score(evidence: dict, detections: list, comparisons: list | None) -> float | None:
    """Uncalibrated within-tier rank score in [0, 1] — NOT a probability.

    s_obs: fraction of available stations that triggered; s_sim: best comparison agreement
    (arrival within tol and amplitude within factor); s_screen: screening met."""
    usable = [d for d in detections if d["availability"]["state"] == "available"]
    s_obs = (sum(1 for d in usable if d.get("trigger_ms") is not None) / len(usable)) if usable else 0.0
    s_sim = 0.0
    if comparisons:
        ok = [c for c in comparisons if c.get("consistent") is True]
        if ok:
            ratios = [min(1.0, max(0.0, 1.0 - abs(c.get("arrival_residual_min") or 0.0) / 60.0)) for c in ok]
            s_sim = max(ratios)
    s_screen = 1.0 if evidence.get("contributing") and "seismic-screen" in evidence.get("contributing", []) else 0.0
    return round(0.5 * s_obs + 0.35 * s_sim + 0.15 * s_screen, 4)


__all__ = ["screening_for", "detect_stations", "eikonal_ray_times", "source_from_event",
           "simulate", "compare_stations", "classify", "rank_score"]
