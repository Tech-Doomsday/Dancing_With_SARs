"""Acceptance tests for engine/lib/tsunami (port spec §14 + module honesty contracts).

These run OFFLINE (synthetic grids and records). The numerics they pin:
  T-S1  Kajiura: long-wave transparency (attenuation ≤ 2 % at k·d ≪ 1); short waves suppressed.
  T-S2  Source: uniform-plane scaling sanity + FFM parsing + slope correction changes the field.
  T-E1  Eikonal: flat-bottom point-seed arrival within 4 % of distance/√(gh).
  T-W1  Lake at rest (incl. a wet/dry step): |u| < 1e-6 m/s, η unchanged (well-balancing).
  T-W2  Dam break (sponge off): relative mass drift < 1e-12; Ritter front speed ∈ [0.6, 1.1]·2√(gh0).
  T-W3  SSP-RK2 √(gh) wave-speed benchmark: a small pulse propagates at √(gh) within 3 %.
  T-G1  De-tide + detection: a synthetic tide + tsunami signal triggers; pure tide does not.
  T-G2  Evidence classification: the states and the correlated-evidence caveats.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from engine.lib.tsunami import (NsweSolver, SimParams, classify_evidence, compute_deformation,
                                detect_tsunami_signal, kajiura_filter, parse_ffm_geojson,
                                screen_seismic, station_consistency, travel_time_at,
                                travel_time_field, uniform_fault_from_scaling)
from engine.lib.tsunami.source import SourceOptions


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def flat_ocean(ny=41, nx=61, depth=4000.0, res=2.0, lat0=36.0, lon0=142.0):
    bed = np.full((ny, nx), -depth, dtype=np.float64)
    return bed, res, lat0, lon0


def run_solver(bed, eta0, params, t_end, lat0=36.0, lon0=142.0, gauge_specs=None):
    solver = NsweSolver(params.res_arcmin, lat0, lon0, bed, eta0, params, gauge_specs)
    while solver.sim_time < t_end:
        solver.advance(200, t_end)
    return solver


# --------------------------------------------------------------------------- #
# T-S1 Kajiura
# --------------------------------------------------------------------------- #
def test_kajiura_long_wave_passes_within_2pct():
    res, depth = 1.0, 4000.0
    bed = np.full((1, 512), -depth)
    x = np.arange(512) * math.radians(res / 60.0) * 6371008.8
    lam = 40.0 * depth                                   # wavelength 40× depth -> k·d ≈ 0.157 -> ≤ 2 %
    dz = np.sin(2 * math.pi * x / lam)[None, :]
    eta = kajiura_filter(res, 0.0, bed, dz)
    ratio = np.max(np.abs(eta[0, 40:460])) / np.max(np.abs(dz[0, 40:460]))
    assert 0.98 <= ratio <= 1.0000001, ratio


def test_kajiura_short_wave_is_suppressed():
    res, depth = 0.25, 4000.0                            # dx ≈ 464 m: λ = 2000 m is resolved
    bed = np.full((1, 2048), -depth)
    x = np.arange(2048) * math.radians(res / 60.0) * 6371008.8
    lam = 0.5 * depth                                    # k·d ≈ 12.6 -> cosh huge -> ~0
    dz = np.sin(2 * math.pi * x / lam)[None, :]
    eta = kajiura_filter(res, 0.0, bed, dz)
    assert np.max(np.abs(eta[0, 200:1800])) < 0.05


# --------------------------------------------------------------------------- #
# T-S2 source
# --------------------------------------------------------------------------- #
def test_uniform_fault_scaling_and_deformation():
    fault, notes = uniform_fault_from_scaling(8.0, 25.0, 38.3, 142.4, (200.0, 15.0, 90.0))
    assert fault.length_m > fault.width_m and fault.slip_m > 0
    assert any("Wells & Coppersmith" in n for n in notes)

    bed = np.full((61, 81), -4000.0)
    bed[30:, :] = 100.0                                  # half land: the slope correction matters
    out = compute_deformation(2.0, 36.0, 142.0, bed, [fault])
    dz = out["dz"]
    assert out["max_abs_dz"] > 0.1                       # an M8 uplift is metres, not centimetres
    assert dz.shape == bed.shape
    assert any("Tanioka" in n for n in out["notes"])

    out_noslope = compute_deformation(2.0, 36.0, 142.0, bed, [fault],
                                      SourceOptions(slope_correction=False, kajiura_filter=False))
    assert not np.allclose(out["dz"], out_noslope["dz"])  # the correction actually did something


def test_ffm_parsing():
    def poly(clon, clat, dlon, dlat, dtop, dbot):
        ring = [[clon - dlon / 2, clat - dlat / 2, dtop],
                [clon + dlon / 2, clat - dlat / 2, dtop],
                [clon + dlon / 2, clat + dlat / 2, dbot],
                [clon - dlon / 2, clat + dlat / 2, dbot],
                [clon - dlon / 2, clat - dlat / 2, dtop]]
        return {"type": "Feature", "geometry": {"coordinates": [ring]},
                "properties": {"slip": 5.0, "rake": 90.0, "sf_moment": 1e20}}

    geo = {"features": [poly(142.4, 38.3, 0.5, 0.2, 5000, 15000),
                        poly(143.0, 38.0, 0.5, 0.2, 5000, 15000)]}
    subs = parse_ffm_geojson(geo)
    assert len(subs) == 2
    s0 = subs[0]
    assert 0 < s0.top_depth_m <= 5000
    assert s0.strike_deg == pytest.approx(90.0, abs=5.0)          # west->east top edge
    assert s0.slip_m == 5.0 and s0.rake_deg == 90.0


# --------------------------------------------------------------------------- #
# T-E1 eikonal
# --------------------------------------------------------------------------- #
def test_eikonal_flat_bottom_point_seed_within_4pct():
    depth = 4000.0
    res, lat0, lon0 = 2.0, 36.0, 142.0
    ny, nx = 61, 81
    bed = np.full((ny, nx), -depth)
    dz = np.zeros((ny, nx))
    c0j, c0i = 30, 40
    dz[c0j - 2:c0j + 3, c0i - 2:c0i + 3] = 1.0           # seed patch
    T = travel_time_field(res, lat0, bed, dz)
    c = math.sqrt(9.81 * depth)
    # T = 0 exactly on the seed patch (rows/cols 28..32 / 38..42 at |dz| >= 10 % of max); sample
    # eastward from the patch EDGE (col 42) along the seed row
    seed_row = lat0 + c0j * res / 60.0
    for d_cells in (10, 20, 30):
        col = c0i + 2 + d_cells
        t_model = travel_time_at(T, lat0, lon0, res, seed_row, lon0 + col * res / 60.0)
        dist_m = d_cells * math.radians(res / 60.0) * 6371008.8 * math.cos(math.radians(seed_row))
        # +-half-cell: the leading edge is quantized to the grid
        assert t_model == pytest.approx(dist_m / c, rel=0.05, abs=0.5 * dist_m / (10 * c)), \
            (d_cells, t_model, dist_m / c)


# --------------------------------------------------------------------------- #
# T-W1/T-W2/T-W3 solver
# --------------------------------------------------------------------------- #
def test_lake_at_rest_with_wet_dry_step_is_exact():
    """Well-balancing incl. a wet/dry front: a resting lake must not move."""
    ny, nx = 33, 49
    bed = np.full((ny, nx), -50.0)
    bed[:, 30:] = 5.0                                    # beach sticking out of the water
    eta = np.zeros((ny, nx))                             # η ≡ 0 everywhere: lake at rest
    params = SimParams(res_arcmin=2, sim_hours=0.2, manning_n=0.0, order=2, sponge_enabled=False)
    solver = run_solver(bed, eta, params, t_end=600.0)
    h = solver.eta - solver.bed
    wet = h > 1e-4
    assert wet.any()
    hh = np.where(wet, h, 1.0)
    max_speed = float(np.max(np.hypot(np.where(wet, solver.hu / hh, 0.0),
                                      np.where(wet, solver.hv / hh, 0.0))))
    assert max_speed < 1e-6, max_speed
    # dried cells sit at η = b; wet cells stay at η = 0
    np.testing.assert_allclose(solver.eta, np.maximum(0.0, bed), atol=1e-9)


def test_dam_break_mass_conserved_and_ritter_front_speed():
    """Sponge off: mass drift < 1e-12; the dry-bed front runs at ≈ 2√(gh0) (Ritter).

    Classic Ritter setup: FLAT bed, surface step (h = h0 west, dry east) — a vertical bed step
    would be a WALL the water cannot cross, not a dam break."""
    ny, nx = 9, 121
    depth0 = 10.0
    bed = np.full((ny, nx), -depth0)                     # flat bed
    eta = np.zeros((ny, nx))
    eta[:, 60:] = -depth0                                # surface at the bed east of x=60: dry
    params = SimParams(res_arcmin=2, sim_hours=0.2, manning_n=0.0, order=2, sponge_enabled=False)
    t_end = 2400.0
    solver = run_solver(bed, eta, params, t_end=t_end)
    diag = solver.finish()["diagnostics"]
    assert diag["mass_drift_relative"] < 1e-12, diag["mass_drift_relative"]
    assert diag["n_neg_clamped"] == 0

    h = solver.eta - solver.bed
    wet_cols = np.where((h > 1e-4).any(axis=0))[0]
    front_col = int(wet_cols.max())
    lat_c = 36.0 + (ny - 1) * (2.0 / 120.0)
    res_m = math.radians(2 / 60.0) * 6371008.8 * math.cos(math.radians(lat_c))
    front_speed = (front_col - 60.0) * res_m / t_end
    expected = 2.0 * math.sqrt(9.81 * depth0)
    assert 0.6 <= front_speed / expected <= 1.1, (front_speed, expected, front_col)


def test_pulse_propagates_at_sqrt_gh():
    """SSP-RK2 wave-speed benchmark (catches the off-by-half-stage Heun combination)."""
    ny, nx = 9, 161
    depth = 1000.0
    bed = np.full((ny, nx), -depth)
    eta = np.zeros((ny, nx))
    c = math.sqrt(9.81 * depth)
    t_end = 100.0
    params = SimParams(res_arcmin=2, sim_hours=0.2, manning_n=0.0, order=2, sponge_enabled=False)
    solver = NsweSolver(params.res_arcmin, 36.0, 142.0, bed, eta, params)
    solver.eta[3:6, 30:34] = 0.05                        # small pulse
    eta0 = solver.eta.copy()
    solver.advance(400, t_end)
    perturbation = solver.eta - eta0                     # the PULSE, not the still-water depth
    profile = perturbation.max(axis=0)
    cols = np.where(profile > 0.4 * profile.max())[0]
    # the box pulse splits into left- and right-going waves; track the RIGHT-going leading edge,
    # which travels at exactly c (long-wave speed)
    right_edge = float(cols.max())
    lat_c = 36.0 + (ny - 1) * (2.0 / 120.0)
    res_m = math.radians(2 / 60.0) * 6371008.8 * math.cos(math.radians(lat_c))
    x_start = 33.5 * res_m                               # right edge of the initial box (col 33)
    travel = (right_edge * res_m - x_start) / t_end
    # tolerance covers the half-cell quantization of the leading edge
    assert travel == pytest.approx(c, rel=0.08), (travel, c, right_edge)


# --------------------------------------------------------------------------- #
# T-G1/T-G2 gauges + evidence
# --------------------------------------------------------------------------- #
def _synthetic_dart_record(trigger_amplitude_m=1.5, tide_amplitude_m=0.8):
    """15-min cadence, ±12 h around the origin; tide + (optional) tsunami at +25 min."""
    t0 = 1_700_000_000_000.0
    cadence = 15 * 60_000.0
    t = t0 + np.arange(96) * cadence                     # 24 h
    origin = t0 + 12 * 3_600_000.0
    tide = tide_amplitude_m * np.sin(2 * math.pi * (t - t0) / (12.4 * 3_600_000.0))
    drift = 0.05 * (t - t0) / (24 * 3_600_000.0)
    v = tide + drift
    if trigger_amplitude_m:
        tau = t - origin
        wave = np.where((tau >= 25 * 60_000.0) & (tau <= 25 * 60_000.0 + 40 * 60_000.0),
                        trigger_amplitude_m * np.sin(2 * math.pi * (tau - 25 * 60_000.0) / (20 * 60_000.0)),
                        0.0)
        v = v + wave
    return {"t": t, "v": v}, origin


def test_detection_triggers_on_wave_not_on_tide():
    series, origin = _synthetic_dart_record(trigger_amplitude_m=1.5)
    det = detect_tsunami_signal("21418", series, origin)
    assert det["availability"]["state"] == "available"
    assert det["trigger_ms"] is not None
    assert det["arrival_min"] == pytest.approx(25.0, abs=16.0)
    assert det["max_amplitude_m"] > 0.5
    assert det["sigma_noise"] < 0.05                     # the tide is gone from the residual

    series_tide, _ = _synthetic_dart_record(trigger_amplitude_m=0.0)
    det2 = detect_tsunami_signal("21418", series_tide, origin)
    assert det2["trigger_ms"] is None
    assert det2["availability"]["state"] == "available"


def test_detection_honest_missing_states():
    series, origin = _synthetic_dart_record()
    short = {"t": series["t"][:30], "v": series["v"][:30]}   # stops before the origin
    det = detect_tsunami_signal("21418", short, origin)
    assert det["availability"]["state"] == "missing"
    assert "does not cover the event window" in det["availability"]["reason"]


def test_screening_and_evidence_levels():
    s = screen_seismic(9.1, 29.0, 90.0)
    assert s["met"] is True
    s2 = screen_seismic(7.7, 12.6, 10.0)                 # near-pure strike-slip: mechanism fails
    assert s2["met"] is False
    assert any("strike-slip" in n for n in s2["notes"])

    available_none = {"station_id": "21418", "availability": {"state": "available"}, "trigger_ms": None}
    lvl = classify_evidence([available_none], None, screening=s)
    assert lvl["level"] == "none-detected"

    triggered = {"station_id": "21418", "availability": {"state": "available"},
                 "trigger_ms": 1.0, "arrival_min": 29.0, "max_amplitude_m": 1.8}
    lvl2 = classify_evidence([triggered], None, screening=s)
    assert lvl2["level"] == "possible"
    assert any("not yet attributed" in c for c in lvl2["caveats"])

    cmp_ok = {"station_id": "21418", "consistent": True, "arrival_residual_min": -6.0, "amplitude_ratio": 1.2}
    lvl3 = classify_evidence([triggered], [cmp_ok], screening=s)
    assert lvl3["level"] == "consistent"
    assert any("correlated evidence" in c for c in lvl3["caveats"])

    lvl4 = classify_evidence([], None, screening=s)
    assert lvl4["level"] == "insufficient"

    assert station_consistency(29.0, 1.8, 31.0, 1.6) is True
    assert station_consistency(29.0, 1.8, 90.0, 1.6) is False
    assert station_consistency(29.0, None, 31.0, 1.6) is None


def test_solver_gauges_and_diagnostics_shapes():
    bed, res, lat0, lon0 = flat_ocean()
    eta = np.zeros_like(bed)
    eta[20, 30] = 0.5
    params = SimParams(res_arcmin=res, sim_hours=0.1, order=2, sponge_enabled=False)
    solver = NsweSolver(res, lat0, lon0, bed, eta, params,
                        gauge_specs=[{"station_id": "21418", "lat": 36.6, "lon": 143.0}])
    solver.advance(200, 300.0)
    out = solver.finish()
    assert out["diagnostics"]["n_steps"] > 0
    g = out["gauges"][0]
    assert g["station_id"] == "21418" and len(g["t_min"]) > 0
    assert out["fields"]["max_eta"].shape == bed.shape
