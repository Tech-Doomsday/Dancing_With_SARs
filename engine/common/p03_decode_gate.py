"""P03 - decode & gate: fill -> NaN, connected-component >= 1 gate, float32.

Gate policy (deliberate): unwrapped phase is gated by the connected-component
label. Coherence is NOT gated - low coherence over unwrappable-failure areas is
exactly the signal the damage/change pipelines want.
"""
from __future__ import annotations

from typing import Dict, Tuple

import numpy as np

from ._raster import resample
from ._types import Clip, ClipError, Raster
from .config import PreprocConfig
from .state import BundleBuilder

STAGE = "P03"


def _float32(r: Raster, extra_fill=None) -> np.ndarray:
    a = np.asarray(r.data)
    f = a.astype(np.float32, copy=True)
    fills = [r.attrs.get("fill_value"), extra_fill]
    for fv in fills:
        if fv is not None and not (isinstance(fv, float) and np.isnan(fv)):
            f[a == fv] = np.nan
    f[~np.isfinite(f)] = np.nan
    return f


def decode_clip(clip: Clip, cfg: PreprocConfig) -> Tuple[Dict[str, Raster], Dict[str, float]]:
    out: Dict[str, Raster] = {}
    metrics: Dict[str, float] = {}
    clip_fills = clip.attrs.get("fill_values", {}) or {}
    for name, r in clip.layers.items():
        if name == "conncomp":
            a = np.asarray(r.data)
            if a.dtype.kind == "f":
                a = np.where(np.isfinite(a), a, 0)
            cc = a.astype(np.int64)
            cc[np.isin(cc, cfg.conncomp_fill_values) | (cc < 0)] = 0
            out[name] = Raster(cc.astype(np.int32), r.grid, dict(r.attrs))
        elif name in ("mask", "layover_shadow"):
            a = np.asarray(r.data)
            if a.dtype.kind == "f":
                a = np.where(np.isfinite(a), a, 0)
            out[name] = Raster(a.astype(np.uint8), r.grid, dict(r.attrs))
        elif name.startswith("gamma0_"):
            f = _float32(r, clip_fills.get(name))
            with np.errstate(invalid="ignore"):
                f[f <= cfg.gamma0_min_linear] = np.nan
            out[name] = Raster(f, r.grid, dict(r.attrs))
        else:
            f = _float32(r, clip_fills.get(name))
            if name == "coherence":
                with np.errstate(invalid="ignore"):
                    f[(f < 0.0) | (f > 1.0 + 1e-3)] = np.nan
                f = np.minimum(f, 1.0)
            out[name] = Raster(f, r.grid, dict(r.attrs))

    if "unw_phase" in out:
        unw = out["unw_phase"]
        before = int(np.isfinite(unw.data).sum())
        if "conncomp" in out:
            cc = out["conncomp"]
            ccd = cc.data if cc.grid.same_geometry(unw.grid) else resample(cc.data, cc.grid, unw.grid, "nearest", 0)
            unw.data[ccd < 1] = np.nan
        if cfg.product_mask_policy == "zero_is_invalid" and "mask" in out:
            m = out["mask"]
            md = m.data if m.grid.same_geometry(unw.grid) else resample(m.data, m.grid, unw.grid, "nearest", 0)
            unw.data[md == 0] = np.nan
        after = int(np.isfinite(unw.data).sum())
        metrics["unw_valid_before_gate"] = before
        metrics["unw_valid_after_gate"] = after
        metrics["frac_removed_by_gate"] = (before - after) / before if before else 0.0
    return out, metrics


def run(state: BundleBuilder) -> Dict:
    if state.clip is None:
        raise ClipError("P03 ran before P02")
    state.decoded, m = decode_clip(state.clip, state.cfg)
    if state.bs_clip is not None:
        state.decoded_bs, _ = decode_clip(state.bs_clip, state.cfg)
    return m
