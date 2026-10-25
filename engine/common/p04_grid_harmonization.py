# P04 — canonical grids G80 (GUNW/GOFF) and G20 (GCOV), index map G20->G80
"""Put every decoded layer onto the tile's canonical grids.

* G80 (80 m): interferometric layers, offsets, radar-geometry cubes, corrections.
* G20 (20 m): GCOV backscatter and its masks / looks / gamma-to-sigma factor.
* `GridSet.index_map_20_to_80` ties the two together (each G20 pixel -> its G80 pixel).

Resampling is exact where it can be (same UTM zone, edge-aligned: index copy for 80 m, block mean for 20 -> 80 m)
and NaN-aware bilinear / nearest / area otherwise (see grids.resample). Adjacent frames of one pass are mosaicked
(first valid pixel wins, overlap counted). Each stack gets one radar-geometry source: the newest GUNW pair, else
the newest GCOV epoch. After this step the native arrays are released.
"""
from __future__ import annotations

import numpy as np

from .context import BuildContext, DGranule, HarmGeometry, HarmUnit
from .grids import mosaic_first_valid, resample
from .layers import ROLES

GEOMETRY_ROLES = ("incidence", "los_x", "los_y", "along_x", "along_y", "elevation_angle", "bperp", "bpar")


def _target(ctx: BuildContext, grid: str):
    return ctx.grids.g80 if grid == "G80" else ctx.grids.g20


def _harmonize_group(ctx: BuildContext, stack_key: str, kind: str, dgs: list) -> HarmUnit | None:
    """All layers of one pair / epoch (possibly several frames) on the canonical grids."""
    parts: dict = {}       # (role, pol) -> [array]
    cov: dict = {}
    heights = None
    for dg in dgs:
        for l in dg.layers:
            info = ROLES[l.role]
            tgt = _target(ctx, info.grid)
            try:
                arr, c = resample(l.data, l.x, l.y, l.epsg, tgt, info.method,
                                  min_valid_fraction=0.5)
            except ValueError as ex:
                ctx.flag("resample_failed", "error", "P04", f"{l.key}: {ex}", layer=l.key)
                continue
            parts.setdefault((l.role, l.pol), []).append(arr)
            cov[info.grid] = c if info.grid not in cov else (cov[info.grid] | c)
            if l.heights is not None and heights is None:
                heights = l.heights
    if not parts:
        return None
    unit = HarmUnit(stack_key, kind, tuple(dg.meta.ref.granule_id for dg in dgs), heights=heights)
    overlap = 0
    for key, arrs in parts.items():
        a0 = arrs[0]
        if len(arrs) > 1 and all(a.shape == a0.shape for a in arrs):
            if a0.dtype.kind == "f":
                overlap = max(overlap, int((np.sum([np.isfinite(a) for a in arrs], axis=0) > 1).sum()))
                a0 = mosaic_first_valid(arrs)
            else:
                fill = 0 if key[0] == "conn_comp" else np.iinfo(a0.dtype).max
                a0 = mosaic_first_valid(arrs, fill=fill)
        unit.layers[key] = a0
    unit.n_overlap = overlap
    unit.coverage = cov
    if overlap:
        ctx.flag("frame_overlap", "info", "P04", f"{stack_key}: {overlap} pixel(s) delivered by more than one frame; first used",
                 stack=stack_key, pixels=overlap)
    bp = unit.layers.get(("bperp", None))
    if bp is not None and np.isfinite(bp).any():
        unit.scalars["bperp_m"] = float(np.nanmean(bp))
    return unit


def run(ctx: BuildContext) -> None:
    res = ctx.resolved
    if res is None:
        return

    def dgs_for(group) -> list:
        out = []
        for m in group.granules:
            dg: DGranule | None = ctx.decoded.get(m.ref.granule_id)
            if dg is not None and not dg.dropped and dg.layers:
                out.append(dg)
        out.sort(key=lambda d: (d.meta.times[0].timestamp() if d.meta.times else 0.0, d.meta.ref.granule_id))
        return out

    for key, groups in res.gunw.items():
        units = []
        for g in groups:
            dgs = dgs_for(g)
            if not dgs:
                ctx.flag("pair_without_data", "warn", "P04", f"{key}: pair {g.granules[0].ref.granule_id} has no usable clip",
                         stack=key)
                continue
            u = _harmonize_group(ctx, key, "pair", dgs)
            if u is not None:
                u.group = g
                units.append(u)
        if units:
            ctx.harm_pairs[key] = units
    for key, groups in res.gcov.items():
        units = []
        for g in groups:
            dgs = dgs_for(g)
            if not dgs:
                ctx.flag("epoch_without_data", "warn", "P04", f"{key}: epoch {g.time} has no usable clip", stack=key)
                continue
            u = _harmonize_group(ctx, key, "epoch", dgs)
            if u is not None:
                u.group = g
                units.append(u)
        if units:
            ctx.harm_epochs[key] = units

    # one radar-geometry source per stack: newest pair, else newest epoch
    for key in set(ctx.harm_pairs) | set(ctx.harm_epochs):
        hg = HarmGeometry()
        for kind, store in (("GUNW", ctx.harm_pairs), ("GCOV", ctx.harm_epochs)):
            for u in reversed(store.get(key, [])):
                found = {r: u.layers[(r, None)] for r in GEOMETRY_ROLES if (r, None) in u.layers}
                if "incidence" in found or ("los_x" in found and "los_y" in found):
                    hg.layers, hg.heights, hg.source = found, u.heights, f"radarGrid:{kind}"
                    break
            if hg.layers:
                break
        ctx.harm_geometry[key] = hg
        if not hg.layers:
            ctx.flag("no_radar_geometry", "warn", "P04", f"{key}: no radarGrid layers in any clip; geometry unavailable", stack=key)
        for store in (ctx.harm_pairs, ctx.harm_epochs):
            for u in store.get(key, []):
                for r in GEOMETRY_ROLES:
                    u.layers.pop((r, None), None)
    ctx.decoded.clear()
