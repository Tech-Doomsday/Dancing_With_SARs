"""3.01 earthquake_coseismic — detection kernel (ported detection.ts).

Detection rule (documented, transparent — NOT a calibrated multiple-testing
procedure): a valid pixel counts as changed when

    |disp| > max(k * sigma_eff, detection_floor, iono_floor)

SIGN CONVENTION (engine): displacement is + = TOWARD the sensor, so
detected == 1 means toward, 2 means away (the TS app used the opposite
labels; see the sign table in docs/IMPLEMENTATION_PLAN.md 3).

The null-scene screen is exploratory: simulated spatially correlated Gaussian
noise (mulberry32 seeds, exact port) through the same rule. Its note string
says so wherever the numbers appear — honesty is part of the contract.
"""
from __future__ import annotations

import math

import numpy as np

from .lib_local import median, mulberry32
from .types import DetectionSummary

NULL_SCREEN_SCENES = 8
NULL_SCREEN_CORR_LEN_PX = 8
NULL_SCREEN_SEED = 20261021
NULL_SCREEN_NOTE = (
    "Exploratory null-scene screen: simulated spatially correlated Gaussian noise "
    "(correlation length {corr_len} px) scaled by the scene's own sigma map, run through the "
    "same detection rule. It measures rule behaviour under a noise model, NOT a calibrated "
    "false-alarm rate (atmospheric/ionospheric covariances are not modelled)."
)
NO_VALID_PIXELS_NOTE = "No valid pixels; null screen not run."


def detected_cluster_stats(detected: np.ndarray, pixel_area_km2: float, min_pixels: int) -> dict:
    """Connected-component labelling (4-neighbourhood) of detected pixels.

    Regions smaller than min_pixels are ignored — a documented minimum-cluster
    criterion, not a statistical guarantee.
    """
    h, w = detected.shape
    visited = np.zeros((h, w), dtype=bool)
    sizes: list[int] = []
    det = detected.astype(bool)
    for start_i in range(h):
        for start_j in range(w):
            if not det[start_i, start_j] or visited[start_i, start_j]:
                continue
            stack = [(start_i, start_j)]
            visited[start_i, start_j] = True
            size = 0
            while stack:
                i, j = stack.pop()
                size += 1
                for ni, nj in ((i, j - 1), (i, j + 1), (i - 1, j), (i + 1, j)):
                    if 0 <= ni < h and 0 <= nj < w and det[ni, nj] and not visited[ni, nj]:
                        visited[ni, nj] = True
                        stack.append((ni, nj))
            if size >= min_pixels:
                sizes.append(size)
    sizes.sort(reverse=True)
    return {
        "count": len(sizes),
        "largest_km2": sizes[0] * pixel_area_km2 if sizes else 0.0,
        "sizes_km2": [s * pixel_area_km2 for s in sizes],
        "min_pixels": min_pixels,
    }


def detection_pass(
    disp: np.ndarray,
    sigma_eff: np.ndarray,
    mask: np.ndarray,
    coherence: np.ndarray,
    iono_floor_m: np.ndarray | None,
    pixel_size_m: tuple[float, float],
    k: float,
    floor_m: float,
    cluster_min_pixels: int = 4,
) -> tuple[DetectionSummary, dict]:
    """Run the detection rule + summary stats + clusters + null screen.

    Returns (DetectionSummary, grids) where grids carries the per-pixel
    threshold/detected rasters for the evidence layers.
    """
    mask = np.asarray(mask, dtype=bool)
    h, w = disp.shape
    n = h * w

    pixel_area_m2 = abs(pixel_size_m[0] * pixel_size_m[1])
    pixel_area_km2 = pixel_area_m2 / 1e6

    sigma_filled = np.where(np.isnan(sigma_eff), 0.0, sigma_eff)
    if iono_floor_m is not None:
        iono_filled = np.where(np.isnan(iono_floor_m), 0.0, iono_floor_m)
    else:
        iono_filled = np.zeros((h, w), dtype=np.float64)

    thresholds = np.maximum(np.maximum(k * sigma_filled, floor_m), iono_filled)
    thresholds = np.where(mask, thresholds, np.nan)

    with np.errstate(invalid="ignore"):
        detected = np.where(mask & (np.abs(disp) > thresholds),
                            np.where(disp > 0, 1, 2).astype(np.uint8), 0).astype(np.uint8)

    valid = mask
    valid_count = int(valid.sum())
    detected_mask = detected > 0
    detected_count = int(detected_mask.sum())

    d_valid = disp[valid]
    thr_valid = thresholds[valid]
    coh_valid = coherence[valid]
    sig_valid = sigma_eff[valid]

    d_det = disp[detected_mask]
    toward = d_det > 0
    away = d_det < 0

    # |disp|-weighted anomaly moments (ported exactly).
    x_axis = (np.arange(w)[None, :] + 0.0)
    y_axis = (np.arange(h)[:, None] + 0.0)
    wgt = np.where(detected_mask, np.abs(np.nan_to_num(disp, nan=0.0)), 0.0)
    sw = float(wgt.sum())
    if sw > 0:
        cx = float((wgt * x_axis).sum() / sw)
        cy = float((wgt * y_axis).sum() / sw)
        var_x = float((wgt * x_axis * x_axis).sum() / sw) - cx * cx
        var_y = float((wgt * y_axis * y_axis).sum() / sw) - cy * cy
        cov_xy = float((wgt * x_axis * y_axis).sum() / sw) - cx * cy
        trace = var_x + var_y
        diff = var_x - var_y
        disc = math.sqrt(diff * diff + 4.0 * cov_xy * cov_xy)
        lam_max = (trace + disc) / 2.0
        lam_min = (trace - disc) / 2.0
        if lam_max > 0 and lam_min >= 0:
            elongation = math.sqrt(lam_max / lam_min) if lam_min > 0 else math.inf
            orientation = math.degrees(0.5 * math.atan2(2.0 * cov_xy, diff))
        else:
            elongation, orientation = None, None
        centroid = (cx, cy)
    else:
        centroid, elongation, orientation = None, None, None

    clusters = detected_cluster_stats(detected, pixel_area_km2, cluster_min_pixels)

    coh_finite = coh_valid[~np.isnan(coh_valid)]
    summary = DetectionSummary(
        valid_pixels=valid_count,
        masked_pixel_count=n - valid_count,
        detected_pixels=detected_count,
        threshold_median_m=median(thr_valid),
        affected_area_km2=detected_count * pixel_area_km2,
        toward_area_km2=int(toward.sum()) * pixel_area_km2,
        away_area_km2=int(away.sum()) * pixel_area_km2,
        max_toward_m=float(d_det[toward].max()) if toward.any() else 0.0,
        max_away_m=float(d_det[away].min()) if away.any() else 0.0,
        median_sigma_m=median(sig_valid),
        median_sigma_anomaly_m=median(sigma_eff[detected_mask]) if detected_count else math.nan,
        anomaly_centroid=centroid,
        anomaly_orientation_deg=orientation,
        anomaly_elongation=elongation,
        displacement_integral_m_km2=float(np.nansum(np.where(detected_mask, disp, 0.0)) * pixel_area_km2),
        coherence_median=median(coh_valid),
        coherence_mean=float(coh_finite.mean()) if coh_finite.size else math.nan,
        valid_fraction=valid_count / n,
        pixel_area_km2=pixel_area_km2,
        analysed_area_km2=valid_count * pixel_area_km2,
        clusters=clusters,
    )
    grids = {"thresholds": thresholds, "detected": detected}
    return summary, grids


def run_null_scene_screen(
    sigma_eff: np.ndarray,
    mask: np.ndarray,
    pixel_size_m: tuple[float, float],
    k: float,
    floor_m: float,
    scenes: int = NULL_SCREEN_SCENES,
    correlation_length_px: int = NULL_SCREEN_CORR_LEN_PX,
    seed: int = NULL_SCREEN_SEED,
) -> dict:
    """Exploratory null-scene screen (exact port of runNullSceneScreen)."""
    corr_len = max(1, int(correlation_length_px))
    mask = np.asarray(mask, dtype=bool)
    h, w = sigma_eff.shape
    n = h * w
    valid_count = int(mask.sum())
    if valid_count == 0:
        return {
            "scenes": 0, "detected_fraction_mean": math.nan, "detected_fraction_max": math.nan,
            "false_area_km2_mean": math.nan, "scenes_with_clusters": 0,
            "correlation_length_px": corr_len, "seed": seed, "note": NO_VALID_PIXELS_NOTE,
        }

    r = max(1, corr_len // 2)
    kernel_n = (2 * r + 1) * (2 * r + 1)
    kernel_var = 1.0 / kernel_n
    pixel_area_km2 = abs(pixel_size_m[0] * pixel_size_m[1]) / 1e6

    sigma_filled = np.where(np.isnan(sigma_eff), np.nan, sigma_eff)
    detected_fractions: list[float] = []
    false_areas: list[float] = []
    scenes_with_clusters = 0

    for s in range(scenes):
        rng = mulberry32(seed + s * 7919)
        # White noise via Box-Muller with the exact pairing/redraw semantics.
        white = np.empty(n, dtype=np.float64)
        idx = 0
        while idx < n:
            u1 = rng()
            u2 = rng()
            while u1 <= 1e-12:
                u1 = rng()
            mag = math.sqrt(-2.0 * math.log(u1))
            white[idx] = mag * math.cos(2.0 * math.pi * u2)
            if idx + 1 < n:
                white[idx + 1] = mag * math.sin(2.0 * math.pi * u2)
            idx += 2
        white = white.reshape(h, w)

        # Box filter -> correlated noise, renormalised to unit variance
        # (edge pixels average fewer samples; the ~kernelN scaling is kept).
        csum = np.zeros((h, w))
        cnt = np.zeros((h, w))
        for di in range(-r, r + 1):
            i0, i1 = max(0, -di), min(h, h - di)
            for dj in range(-r, r + 1):
                j0, j1 = max(0, -dj), min(w, w - dj)
                csum[i0:i1, j0:j1] += white[i0 + di:i1 + di, j0 + dj:j1 + dj]
                cnt[i0:i1, j0:j1] += 1.0
        corr = (csum / np.maximum(cnt, 1.0)) / math.sqrt(kernel_var)

        # Same detection rule on sigma-scaled correlated noise.
        sg = sigma_filled
        usable = mask & ~np.isnan(sg)
        d = np.where(usable, corr * np.nan_to_num(sg), 0.0)
        thr = np.maximum(k * np.nan_to_num(sg), floor_m)
        det = np.where(usable & (np.abs(d) > thr), 1, 0).astype(np.uint8)
        det_count = int(det.sum())
        detected_fractions.append(det_count / valid_count)
        false_areas.append(det_count * pixel_area_km2)
        if detected_cluster_stats(det, pixel_area_km2, 4)["count"] > 0:
            scenes_with_clusters += 1

    def _mean(vals):
        return sum(vals) / len(vals) if vals else math.nan

    return {
        "scenes": scenes,
        "detected_fraction_mean": _mean(detected_fractions),
        "detected_fraction_max": max(detected_fractions) if detected_fractions else math.nan,
        "false_area_km2_mean": _mean(false_areas),
        "scenes_with_clusters": scenes_with_clusters,
        "correlation_length_px": corr_len,
        "seed": seed,
        "note": NULL_SCREEN_NOTE.format(corr_len=corr_len),
    }
