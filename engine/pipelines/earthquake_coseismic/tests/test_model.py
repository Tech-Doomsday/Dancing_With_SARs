"""T-6/T-7: ported attribution.test.ts behaviors (event ranking, forward screen)."""
import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from engine.pipelines.earthquake_coseismic import model
from engine.pipelines.earthquake_coseismic.types import EqParams, EventRecord

PARAMS = EqParams()


def _ev(eid, mag, t_offset_days, lat, lon, planes=((),)) -> EventRecord:
    t = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc) + timedelta(days=t_offset_days)
    return EventRecord(id=eid, magnitude=mag, place="", time=t.strftime("%Y-%m-%dT%H:%M:%SZ"),
                       lat=lat, lon=lon, depth_km=8.0,
                       nodal_planes=tuple(p for p in planes if p))


REF, SEC = "2026-07-01T00:00:00Z", "2026-07-25T00:00:00Z"
CENTER = (-118.5, 34.0)   # (lon, lat) — the bundle's center_lonlat convention


def test_ranking_largest_in_window_wins_even_when_listed_second():
    small = _ev("small", 4.5, 5, 34.0, -118.5, planes=((0, 30, 90),))
    big = _ev("big", 6.4, 10, 34.0, -118.5, planes=((0, 30, 90),))
    sel = model.rank_events([small, big], REF, SEC, CENTER, PARAMS)
    assert sel.primary.id == "big"


def test_ranking_equal_magnitude_nearer_wins_then_mechanism_then_id():
    near = _ev("b-near", 5.5, 5, 34.05, -118.5)
    far = _ev("a-far", 5.5, 5, 36.4, -118.5)
    sel = model.rank_events([far, near], REF, SEC, CENTER, PARAMS)
    assert sel.primary.id == "b-near"

    eq_d = _ev("x-eqdist", 5.5, 5, 34.05, -118.5)
    mech = _ev("a-mech", 5.5, 5, 34.05, -118.5, planes=((0, 30, 90),))
    sel = model.rank_events([eq_d, mech], REF, SEC, CENTER, PARAMS)
    assert sel.primary.id == "a-mech"  # mechanism beats no-mechanism at equal magnitude+distance


def test_ranking_window_and_distance_exclusions_carry_reasons():
    inside_out = _ev("outside", 7.2, -30, 34.0, -118.5)     # before ref
    far = _ev("far", 7.0, 5, 40.0, -118.5)                  # ~665 km north
    sel = model.rank_events([inside_out, far], REF, SEC, CENTER, PARAMS)
    assert sel.ranked == []
    reasons = {e["id"]: e["reason"] for e in sel.excluded}
    assert "window" in reasons["outside"]
    assert "km from the footprint centroid" in reasons["far"]


def test_ranking_missing_magnitude_ranks_last_but_kept():
    anon = EventRecord(id="zz-anon", magnitude=None, place="", time="2026-07-10T00:00:00Z",
                       lat=34.0, lon=-118.5, depth_km=8.0)
    small = _ev("aa-small", 4.2, 5, 34.0, -118.5)
    sel = model.rank_events([anon, small], REF, SEC, CENTER, PARAMS)
    assert [ev.id for ev, _ in sel.ranked] == ["aa-small", "zz-anon"]


def test_ranking_deterministic_id_tiebreak():
    a = _ev("id-a", 5.0, 5, 34.0, -118.5)
    b = _ev("id-b", 5.0, 5, 34.0, -118.5)
    sel = model.rank_events([b, a], REF, SEC, CENTER, PARAMS)
    assert sel.primary.id == "id-a"


def _analysis_from_truth(pred_field: np.ndarray, mask: np.ndarray) -> "model.PairAnalysis":
    from engine.pipelines.earthquake_coseismic.types import PairAnalysis
    h, w = pred_field.shape
    ax = model.PairAnalysis(stack_key="A005", pair_index=0, granule_ids=("g",), ref_time=REF, sec_time=SEC)
    noise = np.zeros_like(pred_field)
    ax.mask = mask
    ax.disp = (pred_field + noise).astype(np.float32)
    ax.sigma_eff = np.full((h, w), 0.001, dtype=np.float32)
    ax.ref_offset_m = 0.0
    ax.ref_sigma_m = 0.0
    ax.pixel_size = (80.0, 80.0)
    inc, az = math.radians(40.0), math.radians(80.0)
    ux = math.sin(inc) * math.sin(az)
    uy = math.sin(inc) * math.cos(az)
    uz = math.cos(inc)
    ax.los_enu = np.stack([np.full((h, w), ux), np.full((h, w), uy), np.full((h, w), uz)])
    return ax


def test_forward_screen_epicentre_anchor_and_grid_shift_invariance():
    # An Okada truth field generated with the epicentre fixed in ENU space; two
    # grids whose origins differ by 10 px must produce IDENTICAL predictions at
    # the same geographic point (grid-origin invariance, ported test).
    from engine.lib.okada import GeoFault, geo_fault_displacement_enu
    fault = GeoFault(strike_deg=0.0, dip_deg=35.0, rake_deg=90.0, length_m=12000,
                     width_m=8000, top_depth_m=0, slip_m=0.8, centroid_lat=0, centroid_lon=0)
    epi_x, epi_y = 500_000.0, 3_900_000.0
    inc, az = math.radians(40.0), math.radians(80.0)
    los = np.array([math.sin(inc) * math.sin(az), math.sin(inc) * math.cos(az), math.cos(inc)])

    def truth(grid_x, grid_y):
        d_e, d_n, d_u, sing = geo_fault_displacement_enu(fault, 8000.0, grid_x - epi_x, grid_y - epi_y)
        return d_e * los[0] + d_n * los[1] + d_u * los[2]

    px = 80.0
    x0 = np.arange(125) * px + 499_000.0
    y0 = np.arange(125) * px + 3_899_000.0
    X0, Y0 = np.meshgrid(x0, y0)
    x1 = x0 + 10 * px
    y1 = y0 + 10 * px
    X1, Y1 = np.meshgrid(x1, y1)
    t0 = truth(X0, Y0)
    t1 = truth(X1, Y1)
    # same geographic point, offset grids: identical truth values
    assert np.isclose(t0[60, 60], t1[50, 50], rtol=1e-12, atol=1e-15)

    mask = np.isfinite(t0)
    ax0 = _analysis_from_truth(t0, mask)
    ax0.grid_x, ax0.grid_y = x0, y0
    ax1 = _analysis_from_truth(t1, mask)
    ax1.grid_x, ax1.grid_y = x1, y1

    ev = _ev("eq-fm", 6.0, 10, 0, 0, planes=())  # lat/lon replaced below
    from pyproj import Transformer
    tr = Transformer.from_crs(32611, 4326, always_xy=True)
    lon, lat = tr.transform(epi_x, epi_y)
    ev = EventRecord(id="eq-fm", magnitude=6.0, place="", time="2026-07-11T00:00:00Z",
                     lat=lat, lon=lon, depth_km=8.0, nodal_planes=((0.0, 35.0, 90.0),))
    s0 = model.forward_screen(ax0, ev, epsg=32611, params=PARAMS)
    s1 = model.forward_screen(ax1, ev, epsg=32611, params=PARAMS)
    assert not s0["skipped"] and not s1["skipped"]
    # truth vs truth: pattern correlation must be ~1 on both grids (f32 cast
    # of the truth caps it just below 1 — that rounding is the point being pinned)
    assert s0["pattern_correlation"] > 0.995
    assert s1["pattern_correlation"] > 0.995
    # ratio must pass the screen's own band (the datum asymmetry between the
    # raw truth and the referenced prediction keeps it near-but-not-at 1)
    assert 1.0 / 3.0 < s0["amplitude_ratio"] < 3.0
    assert 1.0 / 3.0 < s1["amplitude_ratio"] < 3.0
    # epicentre projection is grid-independent
    assert s0["epicentre"]["projected_x"] == pytest.approx(s1["epicentre"]["projected_x"], abs=1e-6)
    assert s0["epicentre"]["projected_y"] == pytest.approx(s1["epicentre"]["projected_y"], abs=1e-6)

    # EPICENTRE ANCHOR (ported): moving the epicentre ~7 km NE moves the
    # predicted |LOS| argmax by the same amount (within [0.5, 1.5]x).
    from pyproj import Transformer
    tr_inv = Transformer.from_crs(32611, 4326, always_xy=True)
    lon2, lat2 = tr_inv.transform(epi_x + 5000.0, epi_y + 5000.0)
    ev2 = EventRecord(id="eq-fm-2", magnitude=6.0, place="", time="2026-07-11T00:00:00Z",
                      lat=lat2, lon=lon2, depth_km=8.0, nodal_planes=((0.0, 35.0, 90.0),))
    pred1, _ = model.prediction_field(ax0, ev, 32611)
    pred2, _ = model.prediction_field(ax0, ev2, 32611)
    assert pred1 is not None and pred2 is not None
    p1 = np.unravel_index(np.nanargmax(np.abs(pred1)), pred1.shape)
    p2 = np.unravel_index(np.nanargmax(np.abs(pred2)), pred2.shape)
    dx_px = (p2[1] - p1[1]) * 80.0
    dy_px = (p1[0] - p2[0]) * 80.0          # row 0 = north -> row index decreases northward
    shift = math.hypot(dx_px, dy_px)
    expected = math.hypot(5000.0, 5000.0)
    assert 0.5 * expected < shift < 1.5 * expected


def test_forward_screen_skips_without_nodal_plane():
    rng = np.random.default_rng(1)
    ax = _analysis_from_truth(rng.normal(0, 0.001, (64, 64)), np.ones((64, 64), dtype=bool))
    ax.grid_x = np.arange(64) * 80.0 + 500_000.0
    ax.grid_y = np.arange(64) * 80.0 + 3_900_000.0
    ev = EventRecord(id="no-mech", magnitude=6.0, place="", time="2026-07-10T00:00:00Z",
                     lat=34.0, lon=-118.5, depth_km=8.0, nodal_planes=())
    scr = model.forward_screen(ax, ev, epsg=11, params=PARAMS)
    assert scr["skipped"]
    assert "nodal plane" in scr["skip_reason"]
    assert math.isnan(scr["predicted_peak_m"])


def test_forward_screen_skips_non_utm_epsg():
    rng = np.random.default_rng(2)
    ax = _analysis_from_truth(rng.normal(0, 0.001, (32, 32)), np.ones((32, 32), dtype=bool))
    ax.grid_x = np.arange(32) * 80.0
    ax.grid_y = np.arange(32) * 80.0
    ev = _ev("polar", 6.0, 5, 34.0, -118.5, planes=((0, 30, 90),))
    scr = model.forward_screen(ax, ev, epsg=3413, params=PARAMS)
    assert scr["skipped"]
    assert "not WGS84 UTM" in scr["skip_reason"]


def test_within_tier_score_bounds_and_monotonicity():
    s = model.within_tier_score(ratio=1.0, corr=1.0, affected_km2=100, credible_km2=1.0,
                                null_fraction_mean=0.0)
    assert 0.95 <= s <= 1.0
    zero = model.within_tier_score(ratio=0.0, corr=0.0, affected_km2=0, credible_km2=1.0,
                                   null_fraction_mean=0.05)
    assert zero == pytest.approx(0.0, abs=1e-9)
    assert model.within_tier_score(1.0, 0.6, 50, 1.0, 0.0) > model.within_tier_score(1.0, 0.3, 50, 1.0, 0.0)


def test_decide_status_table():
    from engine.pipelines.earthquake_coseismic.types import DetectionSummary

    def _summary(detected, area):
        return DetectionSummary(detected_pixels=detected, affected_area_km2=area,
                                pixel_area_km2=0.0064, threshold_median_m=0.01,
                                null_screen={"detected_fraction_mean": 0.0})

    screens_pass = [{"event_id": "e1", "skipped": False, "amplitude_ratio": 1.2,
                     "pattern_correlation": 0.6}]
    screens_fail = [{"event_id": "e1", "skipped": False, "amplitude_ratio": 20.0,
                     "pattern_correlation": 0.6}]
    status, raw_p, rule, extra = model.decide(_summary(500, 3.2), screens_pass, True, PARAMS)
    assert status == "SUPPORTED" and rule == "screen_pass" and 0.0 < raw_p <= 1.0
    status, raw_p, rule, extra = model.decide(_summary(500, 3.2), screens_fail, True, PARAMS)
    assert status == "NOT_SUPPORTED" and rule == "screen_fail"
    status, raw_p, rule, extra = model.decide(_summary(0, 0.0), screens_pass, True, PARAMS)
    assert status == "NOT_SUPPORTED" and rule == "no_detection" and raw_p is not None
    status, raw_p, rule, extra = model.decide(_summary(5, 0.03), [], True, PARAMS)
    assert status == "NOT_SUPPORTED" and rule == "sub_credible_no_catalog_event"
    status, raw_p, rule, extra = model.decide(_summary(500, 3.2), screens_pass, False, PARAMS)
    assert status == "INSUFFICIENT_EVIDENCE" and rule == "events_unknown"


def test_decide_events_in_window_but_unscreenable_is_insufficient():
    """Final-prompt fix 1: a catalogued Mw 6.4 event with no published nodal planes is a
    DATA GAP (INSUFFICIENT_EVIDENCE / events_unscreenable) — never UNCATALOGUED_CANDIDATE."""
    from engine.pipelines.earthquake_coseismic.types import DetectionSummary

    summary = DetectionSummary(detected_pixels=500, affected_area_km2=3.2,
                               pixel_area_km2=0.0064, threshold_median_m=0.01,
                               null_screen={"detected_fraction_mean": 0.0})
    screens = [{"event_id": "us6000", "skipped": True, "amplitude_ratio": float("nan"),
                "pattern_correlation": float("nan"),
                "note": "no moment-tensor nodal plane published for this event"}]
    status, raw_p, rule, extra = model.decide(summary, screens, True, PARAMS, n_events_in_window=1)
    assert status == "INSUFFICIENT_EVIDENCE" and rule == "events_unscreenable"
    assert raw_p is None and extra["n_events_in_window"] == 1


def test_decide_uncatalogued_requires_converged_fit():
    """Final-prompt fix 1 + plan 8.4 row 4: genuinely no catalog event — the candidate
    status only with a CONVERGED fit; unconverged / absent fit => NOT_SUPPORTED."""
    from engine.pipelines.earthquake_coseismic.types import DetectionSummary

    summary = DetectionSummary(detected_pixels=500, affected_area_km2=3.2,
                               pixel_area_km2=0.0064, threshold_median_m=0.01,
                               null_screen={"detected_fraction_mean": 0.0})
    converged = {"converged": True, "residual_rms_m": 0.004, "field_peak_m": 0.05}
    status, raw_p, rule, _ = model.decide(summary, [], True, PARAMS, uncatalogued=converged)
    assert status == "UNCATALOGUED_CANDIDATE" and rule == "uncatalogued_fit_converged"
    assert raw_p is not None

    not_converged = {"converged": False, "residual_rms_m": 1e9, "field_peak_m": 0.05}
    status, raw_p, rule, _ = model.decide(summary, [], True, PARAMS, uncatalogued=not_converged)
    assert status == "NOT_SUPPORTED" and rule == "uncatalogued_fit_not_converged"

    status, raw_p, rule, _ = model.decide(summary, [], True, PARAMS, uncatalogued=None)
    assert status == "NOT_SUPPORTED" and rule == "no_catalog_event_fit_not_run"


def test_decide_no_signal_for_quiet_scenes():
    """Docx-confirmed sixth status: no detection AND genuinely no catalog event in the
    window is NO_SIGNAL ("nothing happened here"), not a tested-and-rejected quake.
    With events in the window (or events unknown) the answer stays NOT_SUPPORTED."""
    from engine.pipelines.earthquake_coseismic.types import DetectionSummary

    summary = DetectionSummary(detected_pixels=0, affected_area_km2=0.0,
                               pixel_area_km2=0.0064, threshold_median_m=0.01,
                               null_screen={"detected_fraction_mean": 0.0})
    status, raw_p, rule, _ = model.decide(summary, [], True, PARAMS, n_events_in_window=0)
    assert status == "NO_SIGNAL" and rule == "no_event_no_cluster" and raw_p is None
    status, raw_p, rule, _ = model.decide(summary, [], True, PARAMS, n_events_in_window=2)
    assert status == "NOT_SUPPORTED" and rule == "no_detection"
    status, raw_p, rule, _ = model.decide(summary, [], False, PARAMS, n_events_in_window=0)
    assert status == "NOT_SUPPORTED" and rule == "no_detection"
