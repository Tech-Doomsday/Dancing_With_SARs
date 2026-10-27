"""Seeded synthetic scenes with known ground truth (also usable as fixtures)."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, Tuple

import numpy as np

from engine.common import (Clip, GranuleRecord, Grid, InMemoryClipSource, PreprocConfig, PreprocInput,
                           Raster, TileSpec)
from engine.common._raster import canonical_grids

TILE = TileSpec("T_test_0001", (90.30, 23.70, 90.45, 23.85))
WL = 0.2384
AMP = 0.05           # metres toward sensor at bowl centre
LOS_X, LOS_Y = 0.6, -0.1


def _native(g80: Grid, spacing: float, pad_m: float = 800.0, shift: float = 0.37) -> Grid:
    """A source grid deliberately NOT aligned with the canonical one."""
    xmin, ymin, xmax, ymax = g80.bounds
    x0 = xmin - pad_m + shift * spacing
    y0 = ymax + pad_m - shift * spacing
    nx = int((xmax - xmin + 2 * pad_m) / spacing) + 2
    ny = int((ymax - ymin + 2 * pad_m) / spacing) + 2
    return Grid("native", g80.crs, x0, y0, spacing, spacing, ny, nx)


def truth_disp(grid: Grid, scale: float = 1.0) -> np.ndarray:
    X, Y = np.meshgrid(grid.x_centers(), grid.y_centers())
    cx, cy = (grid.bounds[0] + grid.bounds[2]) / 2, (grid.bounds[1] + grid.bounds[3]) / 2
    return (scale * AMP * np.exp(-((X - cx) ** 2 + (Y - cy) ** 2) / (2 * 3000.0 ** 2))).astype(np.float32)


def make_gunw_clip(gid: str, native: Grid, scale: float = 1.0, drop=(), extra_attrs=None) -> Clip:
    d = truth_disp(native, scale)
    phase = (d / (-WL / (4 * np.pi))).astype(np.float32)          # range-increase convention
    ny, nx = native.shape
    coh = np.full(native.shape, 0.8, np.float32)
    coh[20:40, 20:40] = 0.2
    cc = np.ones(native.shape, np.uint32)
    cc[20:40, 20:40] = 0                                          # unwrapping failed here
    cc[60:70, 60:70] = 65535                                      # fill
    rr = Raster
    layers: Dict[str, Raster] = {
        "unw_phase": rr(phase, native), "coherence": rr(coh, native), "conncomp": rr(cc, native),
        "iono_phase": rr(np.full(native.shape, 0.5, np.float32), native),
        "los_x": rr(np.full(native.shape, LOS_X, np.float32), native),
        "los_y": rr(np.full(native.shape, LOS_Y, np.float32), native),
        "incidence": rr(np.full(native.shape, np.degrees(np.arccos(np.sqrt(1 - LOS_X**2 - LOS_Y**2))), np.float32), native),
    }
    for k in drop:
        layers.pop(k, None)
    attrs = {"wavelength_m": WL, "corrections_applied": {"ionosphere": False}}
    attrs.update(extra_attrs or {})
    return Clip(gid, layers, attrs)


def make_gcov_clip(gid: str, grid: Grid, hh=0.1, hv=0.02) -> Clip:
    return Clip(gid, {"gamma0_hh": Raster(np.full(grid.shape, hh, np.float32), grid),
                      "gamma0_hv": Raster(np.full(grid.shape, hv, np.float32), grid)}, {})


def make_dem(g80: Grid) -> Raster:
    spacing = 90.0
    n = _native(g80, spacing, 1200.0, 0.2)
    X, Y = np.meshgrid(n.x_centers(), n.y_centers())
    xmin, ymin, xmax, ymax = g80.bounds
    z = 0.02 * (X - xmin) + 300.0 * np.exp(-((X - (xmax - 1500)) ** 2 + (Y - (ymax - 1500)) ** 2) / (2 * 500.0 ** 2))
    return Raster(z.astype(np.float32), n)


def make_water(g80: Grid) -> Raster:
    n = _native(g80, 80.0, 400.0, 0.0)
    w = np.zeros(n.shape, np.uint8)
    w[100:130, 100:130] = 1
    return Raster(w, n)


def gunw_record(gid, sec: datetime, baseline_days=12, version="0.1.0", track=5, frame=100) -> GranuleRecord:
    return GranuleRecord(gid, "GUNW", track, frame, "descending", sec - timedelta(days=baseline_days), sec,
                         processing_version=version)


def gcov_record(gid, t: datetime, version="0.1.0", track=5, frame=100) -> GranuleRecord:
    return GranuleRecord(gid, "GCOV", track, frame, "descending", t, None, processing_version=version)


def make_scene(n_history: int = 6, with_gcov: bool = False, n_gcov_hist: int = 3,
               cfg: PreprocConfig = None, with_dem: bool = True, with_water: bool = True) -> Tuple[PreprocInput, Grid]:
    cfg = cfg or PreprocConfig()
    g = canonical_grids(TILE, cfg)
    native = _native(g["G80"], 80.0)
    cur_sec = datetime(2026, 8, 13)
    clips, cands = {}, []
    cur = gunw_record("G_cur", cur_sec)
    clips["G_cur"] = make_gunw_clip("G_cur", native)
    cands.append(cur)
    for i in range(n_history):
        sec = cur_sec - timedelta(days=12 * (i + 1))
        gid = f"G_h{i}"
        cands.append(gunw_record(gid, sec))
        clips[gid] = make_gunw_clip(gid, native, scale=0.5 + 0.1 * i)
    if with_gcov:
        ng = _native(g["G80"], 30.0)
        for i in range(n_gcov_hist + 1):
            t = cur_sec - timedelta(days=12 * i)
            gid = f"C_{i}"
            cands.append(gcov_record(gid, t))
            clips[gid] = make_gcov_clip(gid, ng)
    anc = {}
    if with_dem:
        anc["dem"] = make_dem(g["G80"])
    if with_water:
        anc["water"] = make_water(g["G80"])
    return PreprocInput(TILE, cands, InMemoryClipSource(clips), None, anc), native
