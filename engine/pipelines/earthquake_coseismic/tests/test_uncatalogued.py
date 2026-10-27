"""M2: the uncatalogued-source branch (docs/IMPLEMENTATION_PLAN.md 8.6, deviations in 8.8).

Truth is a known vertical strike-slip Okada source (fixtures_eq.TRUE_SOURCE), so recovery is
provable independent of the NISAR archive. The two fits below are the slow tests of this folder
(~40 s each on one core); everything else is millisecond-level.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from engine.pipelines.earthquake_coseismic import detection as det
from engine.pipelines.earthquake_coseismic import uncatalogued as unc
from engine.pipelines.earthquake_coseismic.types import EqParams

from .fixtures_eq import TRUE_SOURCE, make_okada_analysis

PARAMS = EqParams()


# --------------------------------------------------------------------------- #
# fast kernels
# --------------------------------------------------------------------------- #
def test_quadtree_conserves_weight_and_refines_where_the_signal_is():
    an, summary, (tx, ty) = make_okada_analysis()
    disp, mask = an.disp.astype(float), an.mask
    rows, cols, vals, wts = unc.quadtree_leaves(disp, mask, max_points=500, noise_m=0.003)
    assert 12 < rows.size <= 520
    assert wts.sum() == pytest.approx(mask.sum())               # every valid pixel is in exactly one leaf
    # leaf density near the source must exceed the same-sized far-field window
    gx, gy = an.grid_x, an.grid_y
    px = gx[0] + cols * (gx[1] - gx[0])
    py = gy[0] + rows * (gy[1] - gy[0])
    near = (np.hypot(px - tx, py - ty) < 3000).sum()
    far = (np.hypot(px - (gx.min() + 1500), py - (gy.min() + 1500)) < 3000).sum()
    assert near > 1.5 * far            # best-first by within-cell variance: signal cells split first


def test_quadtree_all_masked_returns_empty():
    d = np.full((20, 20), np.nan)
    rows, cols, vals, wts = unc.quadtree_leaves(d, np.zeros((20, 20), bool), 100, 0.003)
    assert rows.size == 0


@pytest.mark.parametrize("azimuth_deg", [0.0, 45.0, 90.0, 135.0])
def test_pca_strike_convention_is_azimuth_clockwise_from_north(azimuth_deg):
    """detection's orientation lives in (col,row) space, rows increasing SOUTH: strike = 90 + theta."""
    n = 125
    ii, jj = np.mgrid[0:n, 0:n]
    east, north = (jj - 62.0), -(ii - 62.0)                        # row 0 is north
    a = math.radians(azimuth_deg)
    along = east * math.sin(a) + north * math.cos(a)               # unit vector at that azimuth
    across = east * math.cos(a) - north * math.sin(a)
    disp = (0.05 * np.exp(-(along ** 2 / (2 * 25.0 ** 2) + across ** 2 / (2 * 5.0 ** 2)))).astype(np.float32)
    mask = np.ones((n, n), bool)
    summary, _ = det.detection_pass(disp, np.full((n, n), 0.003, np.float32), mask, np.full((n, n), 0.9),
                                    None, (80.0, -80.0), k=3.0, floor_m=0.01, cluster_min_pixels=4)
    strike = unc.pca_strike_deg(summary.anomaly_orientation_deg)
    diff = abs((strike - azimuth_deg + 90.0) % 180.0 - 90.0)       # circular (180-periodic) distance
    assert diff < 3.0, (azimuth_deg, strike)
    assert unc.pca_strike_deg(None) is None


def test_best_slip_is_least_squares_and_capped_by_stress_drop():
    g = np.array([1.0, 2.0, 3.0])
    w = np.ones(3)
    theta = [0, 0, 3000.0, math.log10(6000.0), math.log10(4000.0), 0.0]     # cap = 5e-4 * 4000 = 2 m
    slip, cap = unc._best_slip(g, 0.3 * g, w, theta)
    assert cap == pytest.approx(2.0) and slip == pytest.approx(0.3)
    slip, _ = unc._best_slip(g, 50.0 * g, w, theta)
    assert slip == pytest.approx(2.0)                                       # clipped, not extrapolated
    slip, _ = unc._best_slip(g, -50.0 * g, w, theta)
    assert slip == pytest.approx(-2.0)
    assert unc._best_slip(np.zeros(3), g, w, theta) is None                 # zero prediction: degenerate


def test_prediction_is_referenced_like_the_measurement():
    """8.7: a constant datum on the model must not change the unit prediction's reference level."""
    an, _, (tx, ty) = make_okada_analysis()
    d = unc._Data(an.disp.astype(float), an.mask, an.grid_x, an.grid_y, an.los_enu, an.reference_mask,
                  0.003, 300)
    theta = [tx, ty, 3000.0, math.log10(6000.0), math.log10(4000.0), 90.0]
    g = unc._predict_unit(theta, d.ref_east, d.ref_north, d.ref_los3, d.ref_east, d.ref_north, d.ref_los3)
    assert np.median(g) == pytest.approx(0.0, abs=1e-12)


def test_fit_refuses_honestly_instead_of_inventing_a_source():
    an, summary, _ = make_okada_analysis()
    out = unc.fit_uncatalogued(an, summary, 3413, PARAMS)                   # polar stereographic
    assert out["attempted"] is False and "not WGS84 UTM" in out["reason"] and not out["converged"]
    an.los_enu = None
    out = unc.fit_uncatalogued(an, summary, 32611, PARAMS)
    assert out["attempted"] is False and "los_enu" in out["reason"]


# --------------------------------------------------------------------------- #
# the fits (slow)
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def recovered():
    an, summary, xy = make_okada_analysis()
    return an, summary, xy, unc.fit_uncatalogued(an, summary, 32611, PARAMS)


def test_fit_recovers_a_known_vertical_strike_slip_source(recovered):
    an, summary, (tx, ty), fit = recovered
    assert fit["attempted"] and fit["converged"], fit["reason"]
    p = fit["parameters"]
    assert math.hypot(p["east_m"] - tx, p["north_m"] - ty) < 500.0
    assert abs((p["strike_deg"] - TRUE_SOURCE["strike"] + 90.0) % 180.0 - 90.0) < 8.0
    assert p["sense"] == "sinistral" and p["slip_m"] > 0                     # rake 0 truth
    assert fit["rms_ratio"] < 0.2                                            # truth-basin level, not 0.7
    assert fit["pinned_parameters"] == []
    assert set(fit["weakly_constrained"]) == {"centroid_depth_m", "width_m", "slip_m"}
    assert fit["simplifications"] and fit["optimizer"]["seed"] == PARAMS.uncat_seed


def test_fit_prediction_layer_is_referenced_and_leaves_small_residual(recovered):
    an, summary, _, fit = recovered
    pred = fit["_prediction"]
    assert pred.shape == an.disp.shape
    assert np.nanmedian(pred[an.reference_mask]) == pytest.approx(0.0, abs=2e-3)
    resid = (an.disp - pred)[an.mask]
    assert float(np.sqrt(np.mean(resid ** 2))) < 0.006                       # ~noise (2 mm) + model error


def test_fit_does_not_claim_a_source_for_a_field_it_cannot_represent():
    """An isotropic uplift bowl is not a vertical strike-slip field: no candidate may be claimed."""
    an, summary, _ = make_okada_analysis(bowl_peak=0.05)
    fit = unc.fit_uncatalogued(an, summary, 32611, PARAMS)
    assert fit["attempted"]
    assert not fit["converged"]
    assert fit["reason"]                                                    # the refusal is explained
