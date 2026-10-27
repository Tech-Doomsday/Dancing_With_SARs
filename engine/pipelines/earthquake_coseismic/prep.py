"""3.01 earthquake_coseismic — own_prep on top of the SceneBundle.

The engine's P06 already converted phase -> LOS displacement (`los_m`,
+toward sensor, later minus earlier) with Cramér-Rao sigma and the ionosphere
floor; this module does the pipeline-specific work declared in the manifest:

    * pair selection (event mode: pairs spanning the event window; scan mode:
      the newest pair of the primary stack),
    * quality mask (finite LOS, coherence gate, connected-component gate),
    * referencing: robust median over all valid tile pixels (default; the app's 12 % far-field margin
      is opt-in), the same operator applied to the model prediction,
    * cycle-jump suspect screen (diagnostic, never corrective),
    * optional planar detrend (default OFF, flag recorded when applied).
"""
from __future__ import annotations

import math

import numpy as np

from engine.common.config import load_common_config

from .lib_local import mad_sigma, median, planar_fit, std
from .types import EqParams, PairAnalysis


def _core(bundle):
    """(core slice, pixel size) of the canonical G80 grid."""
    g = bundle.grids.g80
    return g.core_slices(), (g.dx, abs(g.dy))


def select_pairs(bundle, params: EqParams) -> tuple[list[int], list[str]]:
    """-> (pair indices to analyse, refusal reasons). Empty indices + reasons => INSUFFICIENT."""
    stack = bundle.stack()
    if stack is None or stack.interferometric is None or not stack.interferometric.pairs:
        return [], ["no usable NISAR data (M1 interferograms) for this tile/window"]

    ifg = stack.interferometric
    reasons: list[str] = []
    indices: list[int] = []

    event_window = bundle.request.event_window
    if event_window:
        t0, t1 = event_window
        for key, index, pair in bundle.pairs_spanning(t0, t1):
            if key != stack.key:
                continue
            indices.append(index)
        if not indices:
            reasons.append(
                f"no interferogram of stack {stack.key} spans the event window "
                f"({t0} .. {t1}); earthquake_coseismic needs ref <= event start and sec >= event end"
            )
        # oldest secondary first so "best" tie-breaks are deterministic
        indices.sort(key=lambda i: (ifg.pairs[i].sec_time or "", ifg.pairs[i].ref_time or ""))
    else:
        # scan mode: the newest times-resolved pair of the primary stack
        best = -1
        for i, pair in enumerate(ifg.pairs):
            if not pair.times_resolved or not pair.sec_time:
                continue
            if best < 0 or (pair.sec_time, pair.ref_time or "") > (ifg.pairs[best].sec_time, ifg.pairs[best].ref_time or ""):
                best = i
        if best < 0:
            reasons.append("no times-resolved pair available for scan-mode analysis")
        else:
            indices = [best]
    return indices, reasons


def analyse_pair(bundle, stack, pair_index: int, params: EqParams) -> tuple[PairAnalysis | None, str | None]:
    """Mask + reference one pair on the G80 core grid.

    Returns (PairAnalysis, None) or (None, refusal reason).
    """
    ifg = stack.interferometric
    pair = ifg.pairs[pair_index]
    sl, (dx, dy) = _core(bundle)

    los = np.asarray(ifg.los_m[pair_index][sl], dtype=np.float64)
    coh = np.asarray(ifg.coherence[pair_index][sl], dtype=np.float64)
    sigma = np.asarray(ifg.sigma_m[pair_index][sl], dtype=np.float64)
    cc = np.asarray(ifg.conn_comp[pair_index][sl])
    iono = (np.asarray(ifg.iono_floor_m[pair_index][sl], dtype=np.float64)
            if ifg.iono_floor_m is not None else None)

    h, w = los.shape
    analysis = PairAnalysis(
        stack_key=stack.key,
        pair_index=pair_index,
        granule_ids=tuple(pair.granule_ids),
        ref_time=pair.ref_time,
        sec_time=pair.sec_time,
    )

    mask = np.isfinite(los) & np.isfinite(coh) & (coh >= params.coherence_threshold) & (cc != 0)
    valid_count = int(mask.sum())
    if valid_count < params.min_valid_pixels:
        return None, (f"only {valid_count} pixels pass the coherence threshold "
                      f"(>= {params.coherence_threshold}); need >= {params.min_valid_pixels}")

    # ---- reference region -------------------------------------------------
    if params.reference_strategy in ("tileMedian", "fullFrameMedian"):
        ref = mask.copy()
    else:
        margin_rows = max(2, round(params.far_field_margin * h))
        margin_cols = max(2, round(params.far_field_margin * w))
        edge = np.zeros((h, w), dtype=bool)
        edge[:margin_rows, :] = True
        edge[h - margin_rows:, :] = True
        edge[:, :margin_cols] = True
        edge[:, w - margin_cols:] = True
        ref = mask & edge

    warnings: list[str] = []
    flags: list[dict] = []

    # ---- cycle-jump suspect screen (diagnostic only) -----------------------
    wavelength_m = float(load_common_config().radar.wavelength_m)
    cycle_limit = params.cycle_jump_fraction * wavelength_m
    cycle_suspect = mask & (np.abs(los) > cycle_limit)
    if cycle_suspect.any():
        n_suspect = int(cycle_suspect.sum())
        flags.append({"code": "cycle_jump_suspect", "n": n_suspect,
                      "message": f"{n_suspect} pixels exceed |LOS| > {cycle_limit:.3f} m "
                                 f"({params.cycle_jump_fraction}·λ) — flagged, excluded from reference statistics, "
                                 "kept in detection"})
        ref = ref & ~cycle_suspect
        if int(ref.sum()) < params.min_reference_pixels:
            ref = mask.copy()
            warnings.append("Reference region too small after the cycle-jump screen; used the full frame")

    ref_count = int(ref.sum())
    if ref_count < params.min_reference_pixels:
        ref = mask.copy()
        ref_count = int(ref.sum())
        warnings.append(f"Reference region is very small (n={ref_count} before fallback) — used the full frame")

    # ---- referencing -------------------------------------------------------
    ref_offset = median(los[ref])
    if math.isnan(ref_offset):
        return None, "reference offset could not be computed (empty reference region)"
    ref_sigma = mad_sigma(los[ref])
    if not math.isfinite(ref_sigma) or ref_sigma <= 0:
        ref_sigma = std(los[ref])
        if math.isfinite(ref_sigma) and ref_sigma > 0:
            warnings.append("MAD is degenerate in the reference region — using the standard deviation")
        else:
            ref_sigma = 0.0
            warnings.append("Reference scatter is degenerate (std fallback failed); sigma_ref = 0")
    ref_scatter = float(ref_sigma)           # diagnostic: robust scatter of the reference region
    if params.reference_strategy == "tileMedian":
        # On a 10 km tile the reference region can contain the deformation itself (a source field is
        # tens of km wide), so its scatter measures SIGNAL, not noise, and would inflate the detection
        # threshold until the signal hides itself (seen on a synthetic strike-slip scene: 22.6 mm
        # "noise" -> 68 mm threshold on a 47 mm field). The noise term therefore stays the bundle's
        # per-pixel sigma_m (+ iono floor + the 10 mm floor); the scatter is reported, never used.
        ref_sigma = 0.0

    disp = np.where(mask, los - ref_offset, np.nan)
    sigma0 = np.where(np.isnan(sigma), 0.0, sigma)
    sigma_eff = np.where(mask, np.sqrt(sigma0 * sigma0 + ref_sigma * ref_sigma), np.nan)

    # ---- optional planar detrend (default OFF; recorded when applied) ------
    if params.detrend:
        ii, jj = np.nonzero(ref)
        fit = planar_fit(zip(ii.tolist(), jj.tolist(), los[ref].tolist()))
        if fit is None:
            warnings.append("Planar fit was degenerate; detrend skipped")
        else:
            grid_i, grid_j = np.meshgrid(np.arange(h), np.arange(w), indexing="ij")
            disp = np.where(mask, disp - (fit["a"] * grid_i + fit["b"] * grid_j + fit["c"]), np.nan)
            warnings.append(f"Planar detrend applied over the reference region "
                            f"(a={fit['a']:.2e}, b={fit['b']:.2e} m/px)")

    analysis.mask = mask
    analysis.disp = disp.astype(np.float32)
    analysis.sigma_eff = sigma_eff.astype(np.float32)
    analysis.ref_offset_m = float(ref_offset)
    analysis.ref_sigma_m = float(ref_sigma)
    analysis.ref_scatter_m = ref_scatter    # type: ignore[attr-defined]
    analysis.warnings = warnings
    analysis.flags = flags
    analysis.iono_floor_m = iono           # type: ignore[attr-defined]
    analysis.los_enu = None                # filled lazily by verdict.py (geometry may be absent)
    analysis.grid_x = None                 # type: ignore[attr-defined]
    analysis.grid_y = None                 # type: ignore[attr-defined]
    analysis.pixel_size = (dx, dy)         # type: ignore[attr-defined]
    analysis.reference_mask = ref          # type: ignore[attr-defined] — region ref_offset came from
    if stack.geometry is not None and stack.geometry.los_enu is not None and stack.geometry.los_enu.g80 is not None:
        analysis.los_enu = np.asarray(stack.geometry.los_enu.g80[:, sl[0], sl[1]], dtype=np.float64)  # type: ignore
    analysis.grid_x = bundle.grids.g80.x[sl[1]]    # type: ignore[attr-defined]
    analysis.grid_y = bundle.grids.g80.y[sl[0]]    # type: ignore[attr-defined]
    return analysis, None
