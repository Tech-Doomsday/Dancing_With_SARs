"""3.01 earthquake_coseismic — own_prep on top of the SceneBundle.

The engine's P06 already converted phase -> LOS displacement (`los_m`,
+toward sensor, later minus earlier) with Cramér-Rao sigma and the ionosphere
floor; this module does the pipeline-specific work declared in the manifest:

    * pair selection (event mode: pairs spanning the event window; scan mode:
      the newest `scan_max_pairs` pairs of every stack),
    * quality mask (finite LOS, coherence gate, connected-component gate),
    * far-field referencing (12 % margin median; degenerate-MAD fallback),
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


def select_pairs(bundle, params: EqParams) -> tuple[list[tuple[str, int]], list[str]]:
    """-> ([(stack_key, pair index)] to analyse, refusal reasons). Empty list + reasons => INSUFFICIENT.

    EVERY stack that holds interferograms is considered (ascending and descending are analysed
    separately, never merged; the best pair across them wins in verdict.run).
      * event mode: every pair, in any stack, that spans the event window;
      * scan mode (no event window): the newest `params.scan_max_pairs` times-resolved pairs of EACH stack,
        so a signal that appeared a few weeks ago is not hidden behind one quiet newest pair."""
    keys = bundle.interferometric_stack_keys()
    if not keys:
        return [], ["no usable NISAR data (M1 interferograms) for this tile/window"]

    reasons: list[str] = []
    picked: list[tuple[str, int]] = []
    event_window = bundle.request.event_window
    if event_window:
        t0, t1 = event_window
        for key, index, _pair in bundle.pairs_spanning(t0, t1):
            picked.append((key, index))
        if not picked:
            reasons.append(
                f"no interferogram of stack(s) {', '.join(keys)} spans the event window "
                f"({t0} .. {t1}); earthquake_coseismic needs ref <= event start and sec >= event end")
    else:
        for key in keys:
            pairs = bundle.stack(key).interferometric.pairs
            ok = [i for i, p in enumerate(pairs) if p.times_resolved and p.sec_time]
            ok.sort(key=lambda i: (pairs[i].sec_time, pairs[i].ref_time or ""), reverse=True)
            picked.extend((key, i) for i in ok[: max(1, int(params.scan_max_pairs))])
        if not picked:
            reasons.append("no times-resolved pair available for scan-mode analysis")
    # oldest secondary first so "best" tie-breaks are deterministic
    picked.sort(key=lambda ki: (bundle.stack(ki[0]).interferometric.pairs[ki[1]].sec_time or "",
                                bundle.stack(ki[0]).interferometric.pairs[ki[1]].ref_time or "", ki[0]))
    return picked, reasons


def analysed_window(analyses) -> dict:
    """The time span the verdict REALLY looked at: earliest reference .. latest secondary over the analysed pairs.
    -> {"first": iso|None, "last": iso|None, "days": float|None, "n_pairs": int, "longest_pair_days": float|None}."""
    from datetime import datetime

    def _t(v):
        try:
            return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    refs = [_t(a.ref_time) for a in analyses]
    secs = [_t(a.sec_time) for a in analyses]
    refs, secs = [r for r in refs if r], [s for s in secs if s]
    spans = [(_t(a.sec_time) - _t(a.ref_time)).total_seconds() / 86400.0
             for a in analyses if _t(a.ref_time) and _t(a.sec_time)]
    first = min(refs) if refs else None
    last = max(secs) if secs else None
    return {"first": first.strftime("%Y-%m-%d") if first else None,
            "last": last.strftime("%Y-%m-%d") if last else None,
            "days": round((last - first).total_seconds() / 86400.0, 1) if first and last else None,
            "n_pairs": len(analyses),
            "longest_pair_days": round(max(spans), 1) if spans else None}


def window_phrase(win: dict) -> str:
    """Plain words for the analysed window, e.g. '2026-07-25 .. 2026-08-06 (12 days, 1 interferogram)'."""
    if not win.get("first") or not win.get("last"):
        return "the analysed interferogram window (dates unknown)"
    n = win["n_pairs"]
    return (f"{win['first']} .. {win['last']} ({win['days']:g} days, {n} interferogram{'s' if n != 1 else ''})")


def signal_edge_contact(detected, reference_mask, valid_mask, warn_fraction: float, coverage_fraction: float) -> dict:
    """A8: does the detected signal touch the band whose median was subtracted (or fill most of the tile)?
    `detected` (>0 = detected), `reference_mask`, `valid_mask` are same-shape arrays on the core grid.
    -> {"in_reference_fraction", "coverage_fraction", "touches_edge", "warn": bool, "reasons": [str]}."""
    det = np.asarray(detected) > 0
    ref = np.asarray(reference_mask, dtype=bool)
    valid = np.asarray(valid_mask, dtype=bool)
    n_det = int(det.sum())
    out = {"in_reference_fraction": 0.0, "coverage_fraction": 0.0, "touches_edge": False, "warn": False, "reasons": []}
    if n_det == 0:
        return out
    ring = np.zeros_like(det)
    ring[0, :] = ring[-1, :] = ring[:, 0] = ring[:, -1] = True
    out["in_reference_fraction"] = round(float((det & ref).sum()) / n_det, 4)
    out["coverage_fraction"] = round(n_det / max(1, int(valid.sum())), 4)
    out["touches_edge"] = bool((det & ring).any())
    if out["in_reference_fraction"] >= warn_fraction:
        out["reasons"].append(f"{out['in_reference_fraction'] * 100:.0f}% of the detected pixels lie inside the reference band "
                              "(the median of that band was subtracted, so real signal there is removed)")
    if out["coverage_fraction"] >= coverage_fraction:
        out["reasons"].append(f"detections cover {out['coverage_fraction'] * 100:.0f}% of the valid tile, so the referencing "
                              "median itself is probably signal")
    if out["touches_edge"] and not out["reasons"]:
        out["reasons"].append("the detected signal reaches the tile edge, so it may continue beyond the tile and the referencing may have absorbed part of it")
    out["warn"] = bool(out["reasons"])
    return out


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
    if params.reference_strategy == "fullFrameMedian":
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
