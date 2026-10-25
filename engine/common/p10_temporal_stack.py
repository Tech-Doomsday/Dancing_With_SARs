"""P10 - temporal stack: epoch table, per-pixel median/MAD, cold_start flag.

History pairs (and GCOV epochs) go through the SAME decode/harmonize/phase chain
as the current epoch (P02-P04, P06 helpers). A history granule that fails is
recorded in the pair table with its reason - never silently dropped.
Memory: coh_stack float16 + disp_stack float32 = 6 bytes * n_pairs * G80 pixels.
"""
from __future__ import annotations

from datetime import date
from typing import Dict, List, Optional

import numpy as np

from ._types import ClipError, GranuleRecord, InsufficientInputError, PreprocError
from .p02_clip import clip_granule
from .p03_decode_gate import decode_clip
from .p04_grid_harmonization import harmonize
from .p06_phase_to_los import displacement_layers
from .p08_backscatter_prep import to_db
from .scene_bundle import TemporalStack
from .state import F_BETA, F_COLD, F_HISTORY_SKIPPED, BundleBuilder

STAGE = "P10"


def _med_mad(stack: np.ndarray, min_n: int, rows: int = 128):
    """Per-pixel median and MAD over axis 0; NaN where fewer than min_n valid samples.

    Row-chunked so peak memory stays ~ (n * rows * nx) floats instead of several
    full-size copies of the stack (matters at G20 on large tiles).
    """
    import warnings
    n_t, ny, nx = stack.shape
    med = np.full((ny, nx), np.nan, np.float32)
    mad = np.full((ny, nx), np.nan, np.float32)
    cnt = np.zeros((ny, nx), np.uint8)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        for r0 in range(0, ny, rows):
            blk = stack[:, r0:r0 + rows].astype(np.float32)
            n = np.isfinite(blk).sum(axis=0)
            m = np.nanmedian(blk, axis=0)
            d = np.nanmedian(np.abs(blk - m), axis=0)
            ok = n >= min_n
            med[r0:r0 + rows] = np.where(ok, m, np.nan)
            mad[r0:r0 + rows] = np.where(ok, d, np.nan)
            cnt[r0:r0 + rows] = np.minimum(n, 255)
    return med, mad, cnt


def _process_pair(state: BundleBuilder, g: GranuleRecord):
    clip = clip_granule(state.inp.clip_source, g, state.clip_bbox)
    dec, _ = decode_clip(clip, state.cfg)
    h = harmonize(dec, state.grids["G80"])
    for req in ("unw_phase", "coherence"):
        if not np.isfinite(h[req]).any():
            raise InsufficientInputError(f"{req} empty on tile grid")
    wl = float(clip.attrs.get("wavelength_m", state.cfg.wavelength_m))
    disp, _, valid = displacement_layers(h["unw_phase"], h["coherence"], h["conncomp"], wl, state.cfg)
    return h["coherence"], disp, valid


def _process_gcov(state: BundleBuilder, g: GranuleRecord) -> Dict[str, np.ndarray]:
    clip = clip_granule(state.inp.clip_source, g, state.clip_bbox)
    dec, _ = decode_clip(clip, state.cfg)
    h = harmonize(dec, state.grids["G20"])
    return {p: to_db(a, state.cfg).astype(np.float16) for p, a in h.items() if p.startswith("gamma0_")}


def run(state: BundleBuilder) -> Dict:
    cfg, res = state.cfg, state.resolution
    beta_cut = date.fromisoformat(cfg.calibrated_archive_start)
    pair_rows: List[Dict] = []
    cohs: List[np.ndarray] = []
    disps: List[np.ndarray] = []
    skipped = 0

    def row(g: GranuleRecord, **kw) -> Dict:
        return {"granule_id": g.granule_id, "ref": g.ref_time.date().isoformat(),
                "sec": g.epoch_date.isoformat(), "baseline_days": g.temporal_baseline_days,
                "perp_baseline_m": g.perp_baseline_m, "pre_calibration": g.ref_time.date() < beta_cut, **kw}

    for g in res.history:
        try:
            coh, disp, valid = _process_pair(state, g)
        except PreprocError as exc:
            skipped += 1
            pair_rows.append(row(g, processed=False, skip_reason=f"{type(exc).__name__}: {exc}"))
            continue
        pair_rows.append(row(g, processed=True, is_current=False, mean_coherence=float(np.nanmean(coh)),
                             valid_fraction=float(valid.mean())))
        cohs.append(np.minimum(coh, 1.0).astype(np.float16))
        disps.append(disp)
    cur = res.current
    pair_rows.append(row(cur, processed=True, is_current=True,
                         mean_coherence=float(np.nanmean(state.layers["coherence"])),
                         valid_fraction=float(state.layers["valid"].mean())))
    cohs.append(state.layers["coherence"].astype(np.float16))
    disps.append(state.layers["los_disp_m"])
    if skipped:
        state.flag(F_HISTORY_SKIPPED)

    coh_arr = np.stack(cohs)                               # float16, (n, ny, nx)
    med, mad, nv = _med_mad(coh_arr, cfg.stack_min_samples)
    state.put("coh_med", med)
    state.put("coh_mad", mad)
    state.put("n_valid_coh", nv)
    disp_arr = np.stack(disps)

    processed = [r for r in pair_rows if r.get("processed")]
    dates = sorted({date.fromisoformat(r[k]) for r in processed for k in ("ref", "sec")})
    gcov_dates = {g.ref_time.date() for g in ((res.gcov_current,) if res.gcov_current else ()) + res.gcov_history}
    epochs = [{"date": d.isoformat(), "is_current": d == cur.epoch_date, "has_gcov": d in gcov_dates,
               "n_pairs": sum(1 for r in processed if d.isoformat() in (r["ref"], r["sec"]))} for d in dates]
    span = float((dates[-1] - dates[0]).days) if dates else 0.0
    n_pairs = len(processed)
    cold = n_pairs < cfg.stack_min_samples
    if cold:
        state.flag(F_COLD)
    if any(r["pre_calibration"] for r in processed):
        state.flag(F_BETA)

    # ---- backscatter history (optional) ---------------------------------------
    bs_epochs: List[Dict] = []
    bs_stack: Dict[str, np.ndarray] = {}
    cur_pols = [p for p in state.work20 if p.startswith("gamma0_")]
    if cur_pols and res.gcov_current is not None:
        series: Dict[str, List[np.ndarray]] = {p: [] for p in cur_pols}
        for g in res.gcov_history:
            try:
                dbs = _process_gcov(state, g)
            except PreprocError as exc:
                bs_epochs.append({"granule_id": g.granule_id, "date": g.ref_time.date().isoformat(),
                                  "processed": False, "skip_reason": f"{type(exc).__name__}: {exc}"})
                state.flag(F_HISTORY_SKIPPED)
                continue
            bs_epochs.append({"granule_id": g.granule_id, "date": g.ref_time.date().isoformat(), "processed": True})
            for p in cur_pols:
                series[p].append(dbs[p] if p in dbs else np.full(state.grids["G20"].shape, np.nan, np.float16))
        bs_epochs.append({"granule_id": res.gcov_current.granule_id,
                          "date": res.gcov_current.ref_time.date().isoformat(), "processed": True, "is_current": True})
        for p in cur_pols:
            series[p].append(state.layers[f"{p}_db"].astype(np.float16))
            arr = np.stack(series[p])
            series[p] = []
            m, d, n = _med_mad(arr, cfg.stack_min_samples)
            state.put(f"{p}_med_db", m, "G20")
            state.put(f"{p}_mad_db", d, "G20")
            state.put(f"n_valid_{p}", n, "G20")
            bs_stack[p] = arr

    state.stack = TemporalStack(
        epochs=tuple(epochs), pairs=tuple(pair_rows),
        coh_stack=coh_arr if cfg.keep_coh_stack else None,
        disp_stack=disp_arr if cfg.keep_disp_stack else None,
        bs_epochs=tuple(bs_epochs), bs_stack=bs_stack, n_pairs=n_pairs, n_epochs=len(dates),
        span_days=span, cold_start=cold)
    return {"n_pairs": n_pairs, "n_epochs": len(dates), "span_days": span, "cold_start": cold,
            "skipped_history": skipped, "bs_epochs": len(bs_epochs)}
