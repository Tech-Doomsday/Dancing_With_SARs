"""3.01 earthquake_coseismic — forward-model screen, event ranking, status decision.

Attribution is a transparent rule-based screen on top of validated change
measurements and independent evidence (ComCat + Okada forward modelling).
Every status is traceable to an explicit evidence item; agreement classes say
"consistent with", never "caused by". Ported from the app's attribution.ts
with the engine's sign convention and status vocabulary.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from engine.lib.okada import (
    GeoFault,
    geo_fault_displacement_enu,
    geo_fault_displacement_enu_top_edge,
    moment_from_mw,
    rupture_area_from_mw,
    slip_from_moment,
)
from engine.lib.los import los_project

from .lib_local import haversine_km, mad_sigma, pearson, project_epicentre, project_los_enu
from .types import (
    AMPLITUDE_RATIO_FACTOR,
    PATTERN_CORRELATION_MIN,
    EqParams,
    EventRecord,
    PairAnalysis,
)


# --------------------------------------------------------------------------- #
# event ranking (ported rankEventsForPair)
# --------------------------------------------------------------------------- #
@dataclass
class EventSelection:
    ranked: list = field(default_factory=list)          # list[(EventRecord, distance_km)]
    primary: EventRecord | None = None
    alternatives: list = field(default_factory=list)
    excluded: list = field(default_factory=list)        # [{"id", "reason"}]
    criteria: dict = field(default_factory=dict)


def rank_events(events, ref_time: str | None, sec_time: str | None,
                center_lonlat: tuple, params: EqParams) -> EventSelection:
    """Documented ranking — no hidden composite score:

    1. origin time strictly inside (reference, secondary);
    2. within max_event_distance_km of the footprint centroid (tile centre);
    3. order: magnitude desc, distance asc, mechanism availability, id tie-break.
    Events with a missing magnitude are kept but ranked last.
    """
    from datetime import datetime

    def _parse(t: str | None) -> float:
        if not t:
            return math.nan
        try:
            return datetime.fromisoformat(str(t).replace("Z", "+00:00")).timestamp()
        except ValueError:
            return math.nan

    t0, t1 = _parse(ref_time), _parse(sec_time)
    clon, clat = center_lonlat            # bundle convention: center_lonlat = (lon, lat)
    sel = EventSelection(criteria={
        "window": {"start": ref_time, "end": sec_time},
        "max_distance_km": params.max_event_distance_km,
        "distance_to": "tile centre (footprint centroid)",
        "ranking": (
            "in-window candidates ordered by magnitude (descending), then distance to the "
            "footprint centroid (ascending), then moment-tensor availability; ties broken by "
            "event id. This favours the event most likely to produce a measurable signal — "
            "it is a relevance order, not a statement that the selected event caused anything."
        ),
    })

    for ev in events:
        t = _parse(ev.time)
        if math.isnan(t):
            sel.excluded.append({"id": ev.id, "reason": "event time is not parseable"})
            continue
        if not (t > t0 and t < t1):
            sel.excluded.append({
                "id": ev.id,
                "reason": f"origin time outside the acquisition window "
                          f"({str(ref_time)[:10]} .. {str(sec_time)[:10]})",
            })
            continue
        dist = haversine_km(ev.lat, ev.lon, clat, clon)
        if math.isnan(dist):
            sel.excluded.append({"id": ev.id, "reason": "footprint unavailable; cannot check distance"})
            continue
        if dist > params.max_event_distance_km:
            sel.excluded.append({
                "id": ev.id,
                "reason": f"{dist:.0f} km from the footprint centroid (> {params.max_event_distance_km:.0f} km)",
            })
            continue
        sel.ranked.append((ev, dist))

    sel.ranked.sort(key=lambda item: (
        -(item[0].magnitude if item[0].magnitude is not None and math.isfinite(item[0].magnitude) else -math.inf),
        item[1],
        0 if item[0].nodal_planes else 1,
        item[0].id,
    ))
    if sel.ranked:
        sel.primary = sel.ranked[0][0]
        sel.alternatives = [ev for ev, _ in sel.ranked[1:]]
    return sel


# --------------------------------------------------------------------------- #
# fault model from an event (ported faultFromEvent; both nodal planes supported)
# --------------------------------------------------------------------------- #
def fault_from_event(event: EventRecord, plane_index: int = 0):
    """-> (GeoFault, centroid_depth_m) or (None, error string)."""
    if not event.nodal_planes:
        return None, "no moment-tensor nodal plane published for this event"
    if plane_index >= len(event.nodal_planes):
        return None, f"nodal plane {plane_index} not available"
    if event.magnitude is None or not math.isfinite(event.magnitude):
        return None, "event magnitude is missing; scaling relations need it"
    if event.depth_km is None or not math.isfinite(event.depth_km) or event.depth_km < 0:
        return None, "hypocentral depth is missing or negative"

    np_plane = event.nodal_planes[plane_index]
    area = rupture_area_from_mw(event.magnitude)
    moment = moment_from_mw(event.magnitude)
    width = math.sqrt(area / 2.0)          # aspect ratio 2:1 (documented assumption)
    length = 2.0 * width
    slip = slip_from_moment(moment, area)
    centroid_depth = max(4000.0, event.depth_km * 1000.0)
    fault = GeoFault(
        strike_deg=np_plane[0], dip_deg=np_plane[1], rake_deg=np_plane[2],
        length_m=length, width_m=width, top_depth_m=0.0, slip_m=slip,
        centroid_lat=event.lat, centroid_lon=event.lon,
    )
    return (fault, centroid_depth), None


# --------------------------------------------------------------------------- #
# forward-model consistency screen (ported forwardModelCheck)
# --------------------------------------------------------------------------- #
FM_NOTE = (
    "Predicted field uses a uniform-slip rectangular dislocation with the published nodal plane, "
    "anchored at the epicentre projected into the raster CRS (EPSG {epsg}); fault dimensions follow "
    "empirical magnitude-area scaling with substantial scatter, so only order-of-magnitude amplitude "
    "agreement is meaningful. Evaluated on a deterministic subgrid of about {n_points} points "
    "(the model field is smooth at grid scale). Agreement classes are exploratory screening, not causation."
)
FM_SKIP_NOTE = (
    "Forward-model check skipped: {reason}. No fault geometry was invented; "
    "the coseismic hypothesis remains unevaluated quantitatively."
)


def _reference_prediction(pred: np.ndarray, analysis, sub_i=None, sub_j=None) -> np.ndarray:
    """Reference the prediction with the MEASUREMENT's rule.

    The measured field was far-field-referenced in prep (median over the
    reference region removed); the raw Okada field carries the elastic
    model's arbitrary absolute level. Comparing the two unreferenced makes
    the screen measure the model's datum, not the signal (this asymmetry
    exists in the TS original; the port fixes it by referencing BOTH fields
    with the same rule — docs/IMPLEMENTATION_PLAN.md 8.7).
    """
    ref = getattr(analysis, "reference_mask", None)
    if ref is None:
        ref = getattr(analysis, "mask", None)
    if ref is None:
        return pred
    ref = np.asarray(ref, dtype=bool)
    if sub_i is not None:
        ref = ref[sub_i, sub_j]
    vals = pred[ref & np.isfinite(pred)]
    if vals.size == 0:
        return pred
    from .lib_local import median
    offset = median(vals)
    if math.isnan(offset):
        return pred
    return pred - offset


def forward_screen(analysis: PairAnalysis, event: EventRecord, epsg: int, params: EqParams,
                   plane_index: int = 0) -> dict:
    """Okada LOS consistency screen for one event/nodal plane.

    Engine convention: predicted toward-LOS = d_enu . los_enu (ground->sensor),
    matching the bundle's los_m sign (+ = toward).
    """
    base = {"event_id": event.id, "nodal_plane": plane_index}
    fault_res, err = fault_from_event(event, plane_index)
    if err:
        return {**base, "skipped": True, "skip_reason": err, "amplitude_ratio": math.nan,
                "pattern_correlation": math.nan, "predicted_peak_m": math.nan,
                "measured_peak_m": math.nan, "note": FM_SKIP_NOTE.format(reason=err)}

    proj = project_epicentre(event.lat, event.lon, epsg)
    if isinstance(proj, dict) and "error" in proj:
        reason = proj["error"]
        return {**base, "skipped": True, "skip_reason": reason, "amplitude_ratio": math.nan,
                "pattern_correlation": math.nan, "predicted_peak_m": math.nan,
                "measured_peak_m": math.nan, "note": FM_SKIP_NOTE.format(reason=reason)}
    epi_x, epi_y = proj

    (fault, centroid_depth) = fault_res
    disp = np.asarray(analysis.disp, dtype=np.float64)
    mask = np.asarray(analysis.mask, dtype=bool)
    h, w = disp.shape
    n = h * w
    stride = max(1, n // params.forward_subgrid_max_points)

    # deterministic strided subgrid (row-major), offsets RELATIVE TO THE EPICENTRE
    flat_idx = np.arange(0, n, stride)
    sub_i, sub_j = np.divmod(flat_idx, w)
    sub_x = np.asarray(analysis.grid_x)[sub_j] - epi_x
    sub_y = np.asarray(analysis.grid_y)[sub_i] - epi_y

    d_e, d_n, d_u, singular = geo_fault_displacement_enu(fault, centroid_depth, sub_x, sub_y)
    los_enu = analysis.los_enu
    los_sub = np.stack([los_enu[0][sub_i, sub_j], los_enu[1][sub_i, sub_j], los_enu[2][sub_i, sub_j]])
    pred_full = d_e * los_sub[0] + d_n * los_sub[1] + d_u * los_sub[2]   # + = toward (engine sign)
    pred_full = np.where(singular, np.nan, pred_full)
    pred_full = _reference_prediction(pred_full, analysis, sub_i, sub_j)

    pred_peak = float(np.nanmax(np.abs(pred_full))) if np.isfinite(pred_full).any() else 0.0

    meas = disp[sub_i, sub_j]
    prd = pred_full
    ok = mask[sub_i, sub_j] & np.isfinite(meas) & np.isfinite(prd)
    measured_peak = float(np.nanmax(np.abs(meas[ok]))) if ok.any() and np.isfinite(meas[ok]).any() else 0.0

    ratio = (measured_peak / abs(pred_peak)) if abs(pred_peak) > 1e-9 else math.nan
    if math.isnan(ratio):
        ratio = 0.0
    corr = pearson(prd[ok], meas[ok])
    if math.isnan(corr):
        corr = 0.0

    return {**base, "skipped": False, "skip_reason": None,
            "amplitude_ratio": float(ratio), "pattern_correlation": float(corr),
            "predicted_peak_m": float(pred_peak), "measured_peak_m": float(measured_peak),
            "epicentre": {"lat": event.lat, "lon": event.lon,
                          "projected_x": float(epi_x), "projected_y": float(epi_y), "epsg": epsg},
            "note": FM_NOTE.format(epsg=epsg, n_points=n // stride)}


def prediction_field(analysis: PairAnalysis, event: EventRecord, epsg: int, plane_index: int = 0):
    """Full-core-grid predicted toward-LOS field for one event/nodal plane.

    Used for the residual evidence layer (disp − prediction). Returns
    (prediction, screen) where screen is the same dict forward_screen builds
    (recomputed on the full grid — cheap at G80 resolution).
    """
    fault_res, err = fault_from_event(event, plane_index)
    if err:
        return None, None
    proj = project_epicentre(event.lat, event.lon, epsg)
    if isinstance(proj, dict) and "error" in proj:
        return None, None
    epi_x, epi_y = proj
    (fault, centroid_depth) = fault_res

    disp = np.asarray(analysis.disp, dtype=np.float64)
    h, w = disp.shape
    grid_x = np.asarray(analysis.grid_x)[None, :].repeat(h, axis=0)
    grid_y = np.asarray(analysis.grid_y)[:, None].repeat(w, axis=1)
    d_e, d_n, d_u, singular = geo_fault_displacement_enu(fault, centroid_depth, grid_x - epi_x, grid_y - epi_y)
    pred = project_los_enu(d_e, d_n, d_u, analysis.los_enu)
    pred = np.where(singular, np.nan, pred)
    return _reference_prediction(pred, analysis), None


def best_screen(screens: list) -> dict | None:
    """Best screen across events/planes: highest correlation, then ratio closeness to 1."""
    usable = [s for s in screens if not s.get("skipped")]
    if not usable:
        return None
    return max(usable, key=lambda s: (s["pattern_correlation"],
                                      -abs(math.log(max(s["amplitude_ratio"], 1e-9)))))


# --------------------------------------------------------------------------- #
# uncatalogued branch (docs/IMPLEMENTATION_PLAN.md 8.6) — implemented in
# uncatalogued.py (strike scan + least-squares refinement, analytic slip with a
# stress-drop cap, location bounded around the anomaly centroid). model.py keeps
# only the score mapping and the decision table.
# --------------------------------------------------------------------------- #
def uncatalogued_score(fit: dict) -> float | None:
    """Within-tier score from the fit's residual-based equivalent.

    uncatalogued.py reports `quality = 1 - residual RMS / field RMS`; hand-built
    legacy dicts (tests) fall back to 1 - residual_rms_m / field_peak_m.
    """
    if not fit or not fit.get("converged"):
        return None
    q = fit.get("quality")
    if q is not None:
        return clamp01(float(q))
    rms = fit.get("residual_rms_m")
    peak = fit.get("field_peak_m")
    if not rms or not peak or peak <= 0:
        return None
    return clamp01(1.0 - rms / peak)


# --------------------------------------------------------------------------- #
# status decision + within-tier score (docs/IMPLEMENTATION_PLAN.md 8.4-8.5)
# --------------------------------------------------------------------------- #
def clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))


def within_tier_score(ratio: float, corr: float, affected_km2: float, credible_km2: float,
                      null_fraction_mean: float) -> float:
    """raw_p: a within-tier score in [0,1] — NOT a calibrated probability."""
    if ratio and math.isfinite(ratio) and ratio > 0:
        s_amp = clamp01(min(ratio, 1.0 / ratio))
    else:
        s_amp = 0.0
    s_corr = clamp01((corr - PATTERN_CORRELATION_MIN) / (1.0 - PATTERN_CORRELATION_MIN))
    s_area = clamp01((affected_km2 - credible_km2) / (10.0 * max(credible_km2, 1e-9)))
    s_null = 1.0 - clamp01((null_fraction_mean if math.isfinite(null_fraction_mean) else 0.0) / 0.05)
    return 0.40 * s_amp + 0.25 * s_corr + 0.20 * s_area + 0.15 * s_null


def decide(summary, screens: list, events_known: bool, params: EqParams,
           uncatalogued: dict | None = None, n_events_in_window: int = 0) -> tuple[str, float | None, str, dict]:
    """-> (status, raw_p, decision_rule, extra evidence fields).

    Decision table (docs/IMPLEMENTATION_PLAN.md 8.4, with the final-prompt fixes):

    * No detection AND genuinely no catalog event in the window -> `NO_SIGNAL`
      ("nothing happened here" — a distinct quiet outcome, not a tested-and-rejected quake;
      the docx adds NO_SIGNAL to this manifest's status_values for exactly that case).
    * A catalogued event IS in the window but none can be modelled (no published nodal
      planes, or unusable magnitude/depth) -> `INSUFFICIENT_EVIDENCE` (rule
      `events_unscreenable`). This is a data gap, never evidence about the hypothesis —
      and it is NOT "uncatalogued": the catalog event exists.
    * Genuinely no catalog event in the window + credible detection + fit converged
      -> `UNCATALOGUED_CANDIDATE` (raw_p = the fit's residual-based quality).
    * Genuinely no catalog event + fit not converged (or no fit at all) -> `NOT_SUPPORTED`
      (plan 8.4 row 4 — an unconverged fit must not read as a candidate).
    """

    affected = summary.affected_area_km2
    credible = max(1.0, summary.pixel_area_km2 * params.credible_pixel_factor)
    null_frac = summary.null_screen.get("detected_fraction_mean", math.nan)
    best = best_screen(screens)

    if summary.detected_pixels == 0:
        if events_known and n_events_in_window == 0 and best is None:
            # genuinely quiet: no qualifying event (no screens could exist) and no cluster
            return "NO_SIGNAL", None, "no_event_no_cluster", {
                "credible_area_km2": credible, "best_event_id": None}
        score = within_tier_score(0.0, 0.0, affected, credible, null_frac)
        return "NOT_SUPPORTED", score, "no_detection", {
            "credible_area_km2": credible, "best_event_id": best["event_id"] if best else None}

    if not events_known:
        return "INSUFFICIENT_EVIDENCE", None, "events_unknown", {
            "credible_area_km2": credible, "best_event_id": None}

    if best is None:
        # No usable forward screen. Distinguish the two cases the old code conflated:
        if n_events_in_window > 0:
            # Catalog events rank in-window but every screen was skipped (no nodal
            # planes / no magnitude / no depth). The coseismic hypothesis cannot be
            # evaluated quantitatively — report the data gap honestly.
            return "INSUFFICIENT_EVIDENCE", None, "events_unscreenable", {
                "credible_area_km2": credible, "best_event_id": None,
                "n_events_in_window": n_events_in_window}
        # Genuinely no catalog event in the acquisition window.
        if affected >= credible:
            fit_ok = bool(uncatalogued and uncatalogued.get("converged"))
            if fit_ok:
                return "UNCATALOGUED_CANDIDATE", uncatalogued_score(uncatalogued), "uncatalogued_fit_converged", {
                    "credible_area_km2": credible, "best_event_id": None}
            # Unconverged (or not-run) fit -> NOT_SUPPORTED per plan 8.4 row 4;
            # the attempted fit (if any) stays in detail.uncatalogued for review.
            rule = "uncatalogued_fit_not_converged" if uncatalogued else "no_catalog_event_fit_not_run"
            return "NOT_SUPPORTED", within_tier_score(0.0, 0.0, affected, credible, null_frac), rule, {
                "credible_area_km2": credible, "best_event_id": None}
        return "NOT_SUPPORTED", within_tier_score(0.0, 0.0, affected, credible, null_frac), \
            "sub_credible_no_catalog_event", {"credible_area_km2": credible, "best_event_id": None}

    ratio, corr = best["amplitude_ratio"], best["pattern_correlation"]
    amp_ok = (1.0 / AMPLITUDE_RATIO_FACTOR) < ratio < AMPLITUDE_RATIO_FACTOR
    corr_ok = corr > PATTERN_CORRELATION_MIN
    score = within_tier_score(ratio, corr, affected, credible, null_frac)
    if amp_ok and corr_ok:
        return "SUPPORTED", score, "screen_pass", {"credible_area_km2": credible,
                                                   "best_event_id": best["event_id"]}
    failed = []
    if not amp_ok:
        failed.append(f"amplitude ratio {ratio:.2f} outside (1/{AMPLITUDE_RATIO_FACTOR:.0f}, {AMPLITUDE_RATIO_FACTOR:.0f})")
    if not corr_ok:
        failed.append(f"pattern correlation {corr:.2f} <= {PATTERN_CORRELATION_MIN}")
    return "NOT_SUPPORTED", score, "screen_fail", {
        "credible_area_km2": credible, "best_event_id": best["event_id"],
        "failed_criteria": failed}
