# P03 — fill values to NaN, connected-component gate (cc>=1), float32 throughout
"""Turn the raw HDF5 values of each clip into clean, typed layers.

* floating layers -> float32, NaN for fill / inf (and for any configured sentinel); coherence outside [0, 1]
  is corrupt and becomes NaN;
* integer layers keep an integer dtype: `connectedComponents` becomes uint16 with its fill (65535) mapped to 0,
  so "cc >= 1" is the one usable-pixel test; product masks stay as they are (opaque, fill = dtype max);
* the connected-component gate: `unwrappedPhase` is NaN wherever cc < `decode.cc_min`. If a clip has phase but no
  cc layer the phase is left ungated and a flag says so;
* one co-polarization per interferogram (HH preferred, then VV), all diagonal polarizations for GCOV.

The retrieval layer does not hand over each dataset's `_FillValue` attribute (see data_retival_bugs.md), so fill is
inferred by convention: NaN for floats, dtype max for unsigned integers. Raw arrays are released once decoded.
"""
from __future__ import annotations

import numpy as np

from .context import BuildContext, DGranule, DLayer
from .layers import ROLES, integer_fill, parse_layer_key
from .p01_granule_resolution import meta_for

_COPOL_PREF = ("HH", "VV")


def _decode(arr: np.ndarray, kind: str, cfg) -> np.ndarray:
    a = np.asarray(arr)
    if kind == "cc":
        if a.dtype.kind in "iu":
            a = np.where(a == integer_fill(a.dtype), 0, a)       # fill -> "no component"
        else:
            a = np.nan_to_num(a, nan=0.0)
        return np.clip(a, 0, np.iinfo(np.uint16).max).astype(np.uint16)
    if kind == "mask":
        return a if a.dtype.kind in "iu" else np.nan_to_num(a, nan=255).astype(np.uint8)
    if a.dtype.kind == "c":
        raise TypeError("complex layer")
    if a.dtype.kind in "iu":
        f = a.astype(np.float32)
        f[a == integer_fill(a.dtype)] = np.nan
    else:
        f = a.astype(np.float32, copy=True)
    f[~np.isfinite(f)] = np.nan
    for s in cfg.decode.float_sentinels:
        f[f == np.float32(s)] = np.nan
    if kind == "coherence":
        f[(f < 0) | (f > cfg.decode.coherence_max)] = np.nan
        np.clip(f, 0.0, 1.0, out=f)
    return f


def _valid_fraction(a: np.ndarray, kind: str) -> float:
    if a.size == 0:
        return 0.0
    if kind == "cc":
        return float((a > 0).mean())
    if kind == "mask":
        return 1.0
    return float(np.isfinite(a).mean())


def run(ctx: BuildContext) -> None:
    metas = {}
    if ctx.resolved:
        for groups in (ctx.resolved.gunw, ctx.resolved.gcov, ctx.resolved.goff):
            for gl in groups.values():
                for g in gl:
                    for m in g.granules:
                        metas[m.ref.granule_id] = m
    for gid, clip in list(ctx.clips.items()):
        meta = metas.get(gid) or meta_for(clip.granule, ctx)
        dg = DGranule(meta, [], clip.source)
        unknown = 0
        for gk, grid in clip.grids.items():
            heights = grid.extras.get("heightAboveEllipsoid")
            for lk, raw in grid.layers.items():
                lid = parse_layer_key(lk)
                if lid.role is None or lid.role not in ROLES:
                    unknown += 1
                    continue
                kind = ROLES[lid.role].kind
                try:
                    data = _decode(raw, kind, ctx.cfg)
                except TypeError:
                    ctx.flag("unsupported_dtype", "warn", "P03", f"{lk}: dtype {raw.dtype} not supported; layer ignored", layer=lk)
                    continue
                dg.layers.append(DLayer(lid.role, lid.pol, lk, int(grid.epsg), np.asarray(grid.x), np.asarray(grid.y), data,
                                        None if heights is None else np.asarray(heights, np.float64),
                                        _valid_fraction(data, kind)))
        if unknown:
            ctx.flag("unrecognised_layers", "info", "P03", f"{gid}: {unknown} layer(s) with no known role ignored", granule=gid)
        _pick_copol(ctx, dg)
        _gate(ctx, dg)
        sci = [l for l in dg.layers if ROLES[l.role].measurement in ("M1", "M2", "M3")
               and ROLES[l.role].kind in ("phase", "power", "float", "coherence", "count")]
        if ctx.cfg.clip.drop_empty_clips and (not dg.layers or (sci and all(l.valid_fraction == 0.0 for l in sci))):
            dg.dropped = True
            ctx.flag("empty_clip", "warn", "P03", f"{gid}: every requested science layer is invalid in this tile; dropped",
                     granule=gid)
        ctx.decoded[gid] = dg
    ctx.clips.clear()


def _pick_copol(ctx: BuildContext, dg: DGranule) -> None:
    """GUNW/GOFF: keep a single co-pol per interferogram so downstream stacks are not mixed."""
    if dg.meta.product not in ("GUNW", "GOFF"):
        return
    pols = {l.pol for l in dg.layers if l.pol and ROLES[l.role].measurement in ("M1", "M3")}
    if len(pols) <= 1:
        return
    keep = next((p for p in _COPOL_PREF if p in pols), sorted(pols)[0])
    dg.layers = [l for l in dg.layers if not (l.pol and ROLES[l.role].measurement in ("M1", "M3") and l.pol != keep)]
    ctx.flag("multiple_copol", "info", "P03", f"{dg.meta.ref.granule_id}: polarizations {sorted(pols)}; {keep} used",
             granule=dg.meta.ref.granule_id)


def _gate(ctx: BuildContext, dg: DGranule) -> None:
    cc_min = ctx.cfg.decode.cc_min
    by = {(l.role, l.pol, l.epsg, l.data.shape[-2:]): l for l in dg.layers}
    for l in dg.layers:
        if l.role != "unw_phase":
            continue
        cc = by.get(("conn_comp", l.pol, l.epsg, l.data.shape[-2:]))
        if cc is None:
            ctx.flag("cc_missing", "warn", "P03", f"{dg.meta.ref.granule_id}: no connectedComponents next to the phase; "
                     "phase is NOT gated", granule=dg.meta.ref.granule_id)
            continue
        l.data = np.where(cc.data >= cc_min, l.data, np.float32(np.nan)).astype(np.float32)
        l.valid_fraction = float(np.isfinite(l.data).mean())
