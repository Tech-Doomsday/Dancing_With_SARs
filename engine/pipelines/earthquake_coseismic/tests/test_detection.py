"""T-5: ported detection.test.ts behaviors (masking, NaN, clusters, null screen)."""
import math

import numpy as np
import pytest

from engine.pipelines.earthquake_coseismic import detection as dt


def _grid(values, sigma=0.001, mask=None, size=8):
    n = size
    disp = np.full((n, n), np.nan, dtype=np.float64)
    sig = np.full((n, n), np.nan, dtype=np.float64)
    msk = np.ones((n, n), dtype=bool) if mask is None else mask.copy()
    coh = np.full((n, n), 0.8)
    for (i, j), v in values.items():
        disp[i, j] = v
        sig[i, j] = sigma
    return disp, sig, msk, coh


def test_masking_vs_classification_8x8():
    # 3 masked pixels + four 0.5 m positive pixels; sigma=0.001, k=3, floor=0.01
    mask = np.ones((8, 8), dtype=bool)
    mask[0, 0] = mask[3, 3] = mask[7, 7] = False
    vals = {(1, 1): 0.5, (1, 2): 0.5, (2, 1): 0.5, (2, 2): 0.5}
    disp, sig, msk, coh = _grid(vals, mask=mask)
    summary, grids = dt.detection_pass(disp, sig, msk, coh, None, (80.0, 80.0), k=3.0, floor_m=0.01)
    assert summary.valid_pixels == 61
    assert summary.masked_pixel_count == 3
    assert summary.detected_pixels == 4
    assert summary.affected_area_km2 == pytest.approx(4 * 0.0064)
    assert summary.valid_fraction == pytest.approx(61 / 64)
    # ENGINE SIGN: positive displacement = toward = class 1
    assert set(np.unique(grids["detected"][1:3, 1:3])) == {1}


def test_nan_sigma_lets_floor_govern_without_crash():
    disp = np.zeros((8, 8))
    sig = np.full((8, 8), np.nan)
    msk = np.ones((8, 8), dtype=bool)
    coh = np.zeros((8, 8))
    summary, grids = dt.detection_pass(disp, sig, msk, coh, None, (80.0, 80.0), k=3.0, floor_m=0.01)
    assert summary.detected_pixels == 0
    assert summary.threshold_median_m == pytest.approx(0.01)  # NaN sigma -> statistical term 0


def test_all_nan_displacement_yields_no_detection():
    disp = np.full((8, 8), np.nan)
    sig = np.full((8, 8), 0.001)
    msk = np.ones((8, 8), dtype=bool)
    coh = np.zeros((8, 8))
    summary, _ = dt.detection_pass(disp, sig, msk, coh, None, (80.0, 80.0), k=3.0, floor_m=0.01)
    assert summary.detected_pixels == 0


def test_clusters_four_neighbourhood_and_min_pixels():
    det = np.zeros((8, 8), dtype=np.uint8)
    det[1:3, 1:4] = 1          # 6-px cluster (2x3 block)
    det[5, 6] = 1              # stray pixel
    stats = dt.detected_cluster_stats(det, pixel_area_km2=0.01, min_pixels=4)
    assert stats["count"] == 1
    assert stats["largest_km2"] == pytest.approx(0.06)
    assert stats["min_pixels"] == 4
    stats1 = dt.detected_cluster_stats(det, pixel_area_km2=0.01, min_pixels=1)
    assert stats1["count"] == 2

    # diagonal-only pixels are NOT one cluster (4-neighbourhood)
    diag = np.zeros((6, 6), dtype=np.uint8)
    diag[0, 0] = diag[1, 1] = diag[2, 2] = 1
    stats_d = dt.detected_cluster_stats(diag, 0.01, min_pixels=1)
    assert stats_d["count"] == 3


def test_null_screen_deterministic_and_honest():
    rng = np.random.default_rng(5)
    sig = rng.uniform(0.004, 0.006, (32, 32))
    mask = np.ones((32, 32), dtype=bool)
    a = dt.run_null_scene_screen(sig, mask, (80.0, 80.0), k=3.0, floor_m=0.01)
    b = dt.run_null_scene_screen(sig, mask, (80.0, 80.0), k=3.0, floor_m=0.01)
    assert a == b                                  # deterministic (same seed)
    assert a["scenes"] == 8
    assert a["detected_fraction_mean"] < 0.05
    assert "NOT a calibrated" in a["note"]
    c = dt.run_null_scene_screen(sig, mask, (80.0, 80.0), k=3.0, floor_m=0.01,
                                 correlation_length_px=16)
    assert c["correlation_length_px"] == 16
    assert "NOT a calibrated" in c["note"]


def test_null_screen_empty_mask_reports_no_run():
    sig = np.ones((8, 8))
    mask = np.zeros((8, 8), dtype=bool)
    out = dt.run_null_scene_screen(sig, mask, (80.0, 80.0), k=3.0, floor_m=0.01)
    assert out["scenes"] == 0
    assert math.isnan(out["detected_fraction_mean"])
    assert "No valid pixels" in out["note"]
