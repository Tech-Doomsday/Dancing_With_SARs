"""DEM-derived layers on the canonical grids, computed once per build and shared by P05 and P09.

P05 (geometry) runs before P09 (ancillary alignment) but needs the terrain gradients for local incidence and
layover/shadow, so the DEM is loaded lazily through the context and cached here. Nothing in this module
fabricates data: with no DEM the set is `available=False` and callers fall back / flag.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import _terrain as T
from .context import BuildContext
from .grids import GridSpec, make_grid, resample


@dataclass
class TerrainSet:
    available: bool
    source: str = ""
    version: str | None = None
    note: str = ""
    void_fraction: float = 0.0
    elevation: dict = field(default_factory=dict)       # "G80"/"G20" -> (n, n) float32
    slope: dict = field(default_factory=dict)
    aspect: dict = field(default_factory=dict)
    dzdx: dict = field(default_factory=dict)
    dzdn: dict = field(default_factory=dict)
    curv_profile: dict = field(default_factory=dict)
    curv_plan: dict = field(default_factory=dict)
    hand: dict = field(default_factory=dict)            # G80 (+ G20 resampled)
    hand_valid_fraction: float | None = None
    ext_dem: np.ndarray | None = None                   # DEM on the extended G80 grid (for HAND)
    ext_grid: GridSpec | None = None


def extended_grid(ctx: BuildContext) -> GridSpec:
    cfg = ctx.cfg
    halo = (cfg.grids.halo_px_g80 + cfg.terrain.hand_context_px_g80) * cfg.grids.g80_m
    t = ctx.tile
    return make_grid("G80x", t.epsg, t.e0, t.n0, t.size, cfg.grids.g80_m, halo)


def aligned_raster(ctx: BuildContext, name: str, grid: GridSpec, method: str, *, window_grid: GridSpec | None = None):
    """Provider raster `name` resampled onto `grid`. -> (array, RasterLayer, coverage) or None if unavailable.
    Floating results are NaN where the source has no data; integer results use 0."""
    wg = window_grid or grid
    b = wg.bounds
    pad = 2 * max(wg.dx, wg.dy)
    layer = ctx.ancillary_provider.raster(name, ctx.tile, (b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad), wg.epsg)
    if layer is None:
        return None
    data = np.asarray(layer.data)
    if data.dtype.kind in "iu":
        if layer.nodata is not None and not np.isnan(layer.nodata):
            data = np.where(data == layer.nodata, 0, data)
        out, cov = resample(data, layer.x, layer.y, layer.epsg, grid, "nearest", fill=0)
        return out, layer, cov
    data = data.astype(np.float32, copy=True)
    if layer.nodata is not None and not np.isnan(layer.nodata):
        data[data == np.float32(layer.nodata)] = np.nan
    if method == "nearest":
        out, cov = resample(data, layer.x, layer.y, layer.epsg, grid, "nearest")
    else:
        out, cov = resample(data, layer.x, layer.y, layer.epsg, grid, "bilinear")
    return out, layer, cov


def get_terrain(ctx: BuildContext) -> TerrainSet:
    if ctx._terrain is not None:
        return ctx._terrain
    cfg = ctx.cfg
    ext = extended_grid(ctx)
    try:
        got = aligned_raster(ctx, "dem", ext, "bilinear")
    except Exception as ex:                                  # corrupt / unreadable ancillary file: reported, not fatal
        ctx.flag("ancillary_error", "error", "P09", f"dem: {type(ex).__name__}: {ex}", layer="dem")
        got = None
    if got is None:
        ctx._terrain = TerrainSet(False, note="no DEM from the ancillary provider")
        return ctx._terrain
    ext_dem, layer, cov = got
    ts = TerrainSet(True, layer.source, layer.version, ext_dem=ext_dem, ext_grid=ext)
    if not np.isfinite(ext_dem).any():
        ts.available, ts.note = False, "DEM does not cover the tile"
        ctx._terrain = ts
        return ts
    filled, ts.void_fraction = T.fill_voids(ext_dem)
    # crop the extended grid down to each canonical grid; G80 is an exact crop, G20 is resampled from the DEM itself
    g80 = ctx.grids.g80
    r0 = int(round((ext.y[0] - g80.y[0]) / ext.dy))
    c0 = int(round((g80.x[0] - ext.x[0]) / ext.dx))
    n = g80.shape[0]
    dem80 = filled[r0:r0 + n, c0:c0 + n].astype(np.float32)
    got20 = aligned_raster(ctx, "dem", ctx.grids.g20, "bilinear")
    dem20 = T.fill_voids(got20[0])[0].astype(np.float32) if got20 is not None else \
        resample(dem80, g80.x, g80.y, g80.epsg, ctx.grids.g20, "bilinear")[0]
    for name, grid, dem in (("G80", g80, dem80), ("G20", ctx.grids.g20, dem20)):
        s, a, gx, gn = T.slope_aspect(dem, grid.dx, grid.dy, cfg.terrain.slope_flat_deg)
        pf, pl = T.curvature_zt(dem, grid.dx, grid.dy)
        ts.elevation[name], ts.slope[name], ts.aspect[name] = dem, s, a
        ts.dzdx[name], ts.dzdn[name], ts.curv_profile[name], ts.curv_plan[name] = gx, gn, pf, pl
    if ts.void_fraction > 0:
        ctx.flag("dem_voids", "info", "P09", f"DEM had {ts.void_fraction:.1%} void pixels (filled from nearest valid)",
                 fraction=ts.void_fraction)
    ctx._terrain = ts
    return ts


def ensure_hand(ctx: BuildContext, ts: TerrainSet) -> None:
    """Compute HAND on the extended G80 grid (so drainage outside the tile is seen), crop, resample to G20."""
    if not ts.available or ts.hand:
        return
    cfg = ctx.cfg
    extra = None
    try:
        w = aligned_raster(ctx, "water_occurrence", ts.ext_grid, "bilinear")
    except Exception as ex:
        ctx.flag("ancillary_error", "error", "P09", f"water_occurrence: {type(ex).__name__}: {ex}", layer="water_occurrence")
        w = None
    if w is not None:
        extra = np.nan_to_num(w[0], nan=0.0) >= cfg.terrain.hand_water_occurrence_pct
    h, _ = T.hand(ts.ext_dem, ts.ext_grid.dx, ts.ext_grid.dy, min_drainage_km2=cfg.terrain.hand_min_drainage_km2,
                  extra_drainage=extra, max_m=cfg.terrain.hand_max_m)
    g80, ext = ctx.grids.g80, ts.ext_grid
    r0 = int(round((ext.y[0] - g80.y[0]) / ext.dy))
    c0 = int(round((g80.x[0] - ext.x[0]) / ext.dx))
    n = g80.shape[0]
    h80 = h[r0:r0 + n, c0:c0 + n]
    ts.hand["G80"] = h80
    ts.hand["G20"] = resample(h80, g80.x, g80.y, g80.epsg, ctx.grids.g20, "bilinear")[0]
    ts.hand_valid_fraction = float(np.isfinite(h80).mean())
