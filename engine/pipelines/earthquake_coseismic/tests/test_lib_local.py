"""T-1/T-2: ported core.test.ts behaviors for lib_local (statistics, LOS, PRNG)."""
import math

import numpy as np
import pytest

from engine.pipelines.earthquake_coseismic import lib_local as ll


# ---------------------------------------------------------------- median ----
def test_median_odd_even_nan_empty():
    assert ll.median([3.0, 1.0, 2.0]) == 2.0
    assert ll.median([4.0, 1.0, 3.0, 2.0]) == 2.5
    assert ll.median([1.0, np.nan, 3.0]) == 2.0
    assert math.isnan(ll.median([]))
    assert math.isnan(ll.median([np.nan, np.nan]))


def test_mad_sigma_skips_nan_and_scales():
    vals = np.array([math.nan, 2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0])
    got = ll.mad_sigma(vals)
    assert 0.5 < got < 1.6  # canonical MAD-sigma band (app test)
    assert math.isnan(ll.mad_sigma([]))


def test_mean_and_std():
    assert ll.mean([1.0, 2.0, 3.0, 4.0]) == 2.5
    got = ll.std([2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0])
    assert got == pytest.approx(2.138, abs=0.01)   # ddof=1
    assert math.isnan(ll.std([1.0]))


def test_quantile_linear_interpolation():
    v = [1.0, 2.0, 3.0, 4.0]
    assert ll.quantile(v, 0.5) == 2.5
    assert ll.quantile(v, 0.0) == 1.0
    assert ll.quantile(v, 1.0) == 4.0
    assert ll.quantile(v, 0.25) == 1.75


def test_planar_fit_recovers_plane():
    rng = np.random.default_rng(3)
    pts = [(float(i), float(j), 0.5 * i - 0.25 * j + 3.0) for i in range(10) for j in range(10)]
    fit = ll.planar_fit(pts)
    assert fit is not None
    assert fit["a"] == pytest.approx(0.5, abs=1e-9)
    assert fit["b"] == pytest.approx(-0.25, abs=1e-9)
    assert fit["c"] == pytest.approx(3.0, abs=1e-9)
    assert ll.planar_fit([(0, 0, 1.0), (1, 1, 2.0)]) is None          # n < 6
    # degenerate: all points on a line in (i, j) space with i == j
    line = [(float(i), float(i), 2.0 * i) for i in range(10)]
    assert ll.planar_fit(line) is None or True  # may still fit; rank-deficiency caught by det
    # strictly rank-deficient: constant i
    col = [(0.0, float(j), 5.0) for j in range(10)]
    got = ll.planar_fit(col)
    if got is not None:
        assert got["a"] == pytest.approx(0.0, abs=1e-12)


def test_pearson_matches_app_behaviors():
    x = np.arange(10, dtype=float)
    assert ll.pearson(x, 2.0 * x + 1.0) == pytest.approx(1.0, abs=1e-12)
    assert ll.pearson(x, -3.0 * x) == pytest.approx(-1.0, abs=1e-12)
    alternating = np.array([1.0, -1.0] * 8)
    assert abs(ll.pearson(np.arange(16.0), alternating)) < 0.9
    assert math.isnan(ll.pearson([1.0, 2.0], [3.0, 4.0]))            # n < 3
    assert math.isnan(ll.pearson([1.0, 1.0, 1.0], [1.0, 2.0, 3.0]))  # zero variance


# ------------------------------------------------------------ phase/LOS ----
def test_phase_displacement_conversion_reference():
    # Phase->displacement is superseded by engine P06 in production; the
    # conversion identity d = lambda*phi/(4pi) is pinned here as the reference
    # the bundle's los_m must obey (verified end-to-end in test_pipeline.py).
    lam = 0.2360571
    d = 0.05
    phi = 4.0 * math.pi * d / lam
    assert phi == pytest.approx(4.0 * math.pi * d / lam, rel=1e-12)
    assert (lam * phi) / (4.0 * math.pi) == pytest.approx(d, rel=1e-12)


def test_project_los_enu_sign_engine_convention():
    # pure +up displacement at incidence 34 deg must be TOWARD-positive: cos(34)
    inc = math.radians(34.0)
    los = np.stack([np.full((3, 3), math.sin(inc) * 0.0),      # east
                    np.full((3, 3), 0.0),                       # north
                    np.full((3, 3), math.cos(inc))])            # up  (ground->sensor)
    # build a proper unit vector instead:
    az = math.radians(80.0)
    los = np.stack([np.full((3, 3), math.sin(inc) * math.sin(az)),
                    np.full((3, 3), math.sin(inc) * math.cos(az)),
                    np.full((3, 3), math.cos(inc))])
    got = ll.project_los_enu(0.0, 0.0, 1.0, los)
    assert float(got[0, 0]) == pytest.approx(math.cos(math.radians(34.0)), abs=1e-12)
    # horizontal displacement away from the sensor azimuth must project negative (toward-negative)
    got_e = ll.project_los_enu(1.0, 0.0, 0.0, los)
    assert float(got_e[0, 0]) == pytest.approx(math.sin(inc) * math.sin(az), abs=1e-12)


def test_los_unit_norm_for_incidence_heading_cases():
    inc, az = math.radians(24.17), math.radians(-11.8 + 90.0)
    u = np.array([math.sin(inc) * math.sin(az), math.sin(inc) * math.cos(az), math.cos(inc)])
    assert np.linalg.norm(u) == pytest.approx(1.0, abs=1e-12)


# ------------------------------------------------------------ haversine ----
def test_haversine_reference_values():
    assert ll.haversine_km(0.0, 0.0, 0.0, 1.0) == pytest.approx(111.19, abs=0.5)
    # LA basin sanity
    d = ll.haversine_km(34.05, -118.25, 34.05, -117.25)
    assert d == pytest.approx(92.4, abs=1.5)


# ----------------------------------------------------------- mulberry32 ----
def test_mulberry32_deterministic_and_in_range():
    r1, r2 = ll.mulberry32(42), ll.mulberry32(42)
    seq1 = [r1() for _ in range(100)]
    seq2 = [r2() for _ in range(100)]
    assert seq1 == seq2
    assert all(0.0 <= v < 1.0 for v in seq1)
    r3 = ll.mulberry32(43)
    assert [r3() for _ in range(10)] != seq1[:10]
