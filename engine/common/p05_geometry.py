# P05 — LOS unit vector, incidence, heading, local incidence, layover/shadow mask
"""Observing geometry per stack, on both canonical grids.

From the product's radarGrid cubes (collapsed over height with the DEM when there is one, a fixed fallback height
otherwise):

* `los_enu`  unit vector ground -> sensor in (East, North, Up) of the tile's projected axes; Up = +sqrt(1-x^2-y^2);
* `incidence_deg` from the product, `heading_deg` from the along-track vector, `convergence_deg` of the grid;
* with a DEM: local incidence angle and the slope-based layover / shadow test (distant occlusion is not modelled);
* `consistency_deg`: how far the product's incidence is from arccos(Up). It checks the vector's MAGNITUDE only:
  Up = +sqrt(1-x^2-y^2) is blind to flipping the sign of (x, y), so it cannot detect a wrong
  `radar.los_vector_points`. The sign is tested separately: the along-track vector and the horizontal LOS imply a
  look side (sensor to the left of the flight direction = right-looking). It is always reported (`look_side_implied`);
  with `radar.look_side` set, a mismatch raises `los_convention_suspect`. The test combines two sign conventions
  (LOS direction, along-track direction); flipping both at once is invisible.

Nothing is invented: without LOS components the vectors are simply unavailable (the look side is not guessed from
heading + incidence).
"""
from __future__ import annotations

import math

import numpy as np
from scipy import ndimage as ndi

from . import _terrain as T
from .context import BuildContext
from .frozen import Dual
from .grids import resample
from .scene_bundle import Geometry
from .terrain_set import get_terrain


def collapse_height(cube: np.ndarray, heights: np.ndarray | None, at: np.ndarray | float) -> np.ndarray:
    """Linear interpolation of a (H, ny, nx) cube along its height axis at heights `at` (clamped to the axis)."""
    if cube.ndim == 2:
        return cube
    if heights is None or len(heights) != cube.shape[0]:
        return cube[0]
    h = np.asarray(heights, dtype=np.float64)
    if h.size == 1:
        return cube[0]
    if h[0] > h[-1]:
        h, cube = h[::-1], cube[::-1]
    at = np.broadcast_to(np.asarray(at, dtype=np.float64), cube.shape[1:])
    at = np.where(np.isfinite(at), at, h[0])
    i1 = np.clip(np.searchsorted(h, at, side="right"), 1, len(h) - 1)
    i0 = i1 - 1
    t = np.clip((at - h[i0]) / (h[i1] - h[i0]), 0.0, 1.0)
    r, c = np.indices(cube.shape[1:])
    return ((1 - t) * cube[i0, r, c] + t * cube[i1, r, c]).astype(np.float32)


def convergence_deg(tile) -> float:
    lon, lat = tile.center
    cm = -183.0 + 6.0 * tile.zone
    return float(math.degrees(math.atan(math.tan(math.radians(lon - cm)) * math.sin(math.radians(lat)))))


def _to_g20(ctx: BuildContext, a80: np.ndarray, method: str = "bilinear") -> np.ndarray:
    g80, g20 = ctx.grids.g80, ctx.grids.g20
    lead = a80.shape[:-2]
    out, _ = resample(a80, g80.x, g80.y, g80.epsg, g20, method)
    return out.reshape(lead + g20.shape)


def implied_look_side(los_e, los_n, along_e, along_n):
    """'right' | 'left' | None from the mean ground->sensor horizontal vector and the mean along-track vector.
    Sensor to the LEFT of the flight direction (cross > 0) means the radar looks to the right."""
    me, mn, ae, an = (float(np.nanmean(v)) for v in (los_e, los_n, along_e, along_n))
    if not all(math.isfinite(v) for v in (me, mn, ae, an)):
        return None, float("nan")
    cross = ae * mn - an * me
    return ("right" if cross > 0 else "left"), cross


def _check_look_side(ctx: BuildContext, key: str, x, y, ax, ay) -> None:
    side, cross = implied_look_side(x, y, ax, ay)
    if side is None:
        return
    want = (ctx.cfg.radar.look_side or "").lower() or None
    ctx.flag("look_side_implied", "info", "P05", f"{key}: LOS vector + along-track imply a {side}-looking radar"
             + (f" (config look_side={want})" if want else " (radar.look_side not set: not compared)"), stack=key,
             side=side, cross=cross, expected=want)
    if want and want != side:
        ctx.flag("los_convention_suspect", "error", "P05",
                 f"{key}: LOS vector + along-track imply a {side}-looking radar but radar.look_side={want}: "
                 "radar.los_vector_points (or the along-track direction) is probably wrong for this product",
                 stack=key, side=side, expected=want)


def run(ctx: BuildContext) -> None:
    cfg = ctx.cfg
    ctx.assume(f"LOS unit vector in the product points {cfg.radar.los_vector_points.replace('_', ' ')} "
               "(config radar.los_vector_points; the NISAR L2 GUNW spec says 'from target to sensor'; its horizontal "
               "sign is only tested when radar.look_side is set)")
    ctx.assume("along-track unit vector points along the flight direction (the spec says only 'along ground track')")
    terrain = None
    for key in sorted(ctx.harm_geometry):
        hg = ctx.harm_geometry[key]
        if not hg.layers:
            ctx.geometry[key] = Geometry("none", "none", Dual(), Dual(), None, convergence_deg(ctx.tile), Dual(), Dual(),
                                         Dual(), None, vectors_available=False)
            continue
        if terrain is None:
            terrain = get_terrain(ctx)
            if not terrain.available:
                ctx.flag("no_dem", "warn", "P05", "no DEM: radarGrid collapsed at a fixed height; no local incidence / "
                         "layover / shadow", note=terrain.note)
        if terrain.available:
            at, hsrc = terrain.elevation["G80"], "dem"
        else:
            at, hsrc = cfg.geometry.fallback_height_m, "fallback"
        col = {r: collapse_height(a, hg.heights, at) for r, a in hg.layers.items()}

        inc = col.get("incidence")
        if inc is not None and np.isfinite(inc).any() and float(np.nanmax(inc)) < 1.7:
            ctx.flag("incidence_units_suspect", "warn", "P05", "incidence layer max < 1.7: radians? degrees assumed", stack=key)
        have_vec = "los_x" in col and "los_y" in col
        los80 = None
        up = None
        if have_vec:
            x, y = col["los_x"].astype(np.float64), col["los_y"].astype(np.float64)
            if cfg.radar.los_vector_points == "sensor_to_ground":
                x, y = -x, -y
            up = np.sqrt(np.clip(1.0 - x * x - y * y, 0.0, None))
            los80 = np.stack([x, y, up]).astype(np.float32)
            if inc is None:
                inc = np.degrees(np.arccos(np.clip(up, -1, 1))).astype(np.float32)
        consistency = None
        if have_vec and col.get("incidence") is not None:
            d = np.abs(col["incidence"] - np.degrees(np.arccos(np.clip(up, -1, 1))))
            if np.isfinite(d).any():
                consistency = float(np.nanmedian(d))
                if consistency > cfg.geometry.consistency_tol_deg:
                    ctx.flag("los_convention_suspect", "error", "P05",
                             f"incidence layer and LOS vector disagree by {consistency:.1f} deg (median): the LOS-vector "
                             "convention in config is probably wrong for this product", stack=key, median_deg=consistency)
        heading = None
        if "along_x" in col and "along_y" in col:
            ax, ay = np.nanmean(col["along_x"]), np.nanmean(col["along_y"])
            if np.isfinite(ax) and np.isfinite(ay) and (ax or ay):
                heading = float(math.degrees(math.atan2(ax, ay)) % 360.0)
        if have_vec and "along_x" in col and "along_y" in col:
            _check_look_side(ctx, key, x, y, col["along_x"], col["along_y"])
        if not have_vec:
            ctx.flag("no_los_vectors", "warn", "P05", f"{key}: losUnitVectorX/Y missing; LOS vectors unavailable", stack=key)

        inc20 = _to_g20(ctx, inc) if inc is not None else None
        los20 = _to_g20(ctx, los80) if los80 is not None else None
        loc = {"G80": None, "G20": None}
        lay = {"G80": None, "G20": None}
        sha = {"G80": None, "G20": None}
        if terrain.available and have_vec:
            for gname, los, incg in (("G80", los80, inc), ("G20", los20, inc20)):
                li, lo, sh = T.local_geometry(terrain.dzdx[gname], terrain.dzdn[gname], los.astype(np.float64),
                                              incg.astype(np.float64), cfg.geometry.layover_margin_deg,
                                              cfg.geometry.shadow_margin_deg)
                if cfg.geometry.layover_dilate_px > 0:
                    lo = ndi.binary_dilation(lo, iterations=cfg.geometry.layover_dilate_px)
                    sh = ndi.binary_dilation(sh, iterations=cfg.geometry.layover_dilate_px)
                loc[gname], lay[gname], sha[gname] = li, lo, sh
        ctx.geometry[key] = Geometry(
            hg.source, hsrc, Dual(los80, los20), Dual(inc, inc20), heading, convergence_deg(ctx.tile),
            Dual(loc["G80"], loc["G20"]), Dual(lay["G80"], lay["G20"]), Dual(sha["G80"], sha["G20"]),
            consistency, vectors_available=have_vec)
