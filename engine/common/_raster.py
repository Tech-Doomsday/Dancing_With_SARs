"""Grid construction and resampling (numpy only)."""
from __future__ import annotations

import math
from typing import Dict, Optional, Tuple

import numpy as np

from ._geo import parse_crs, transform_xy, utm_epsg
from ._types import BBox, Grid, PreprocError, TileSpec
from .config import PreprocConfig

CATEGORICAL = frozenset({"conncomp", "mask", "layover_shadow", "water"})


def canonical_grids(tile: TileSpec, cfg: PreprocConfig) -> Dict[str, Grid]:
    """Deterministic G80/G20 for a tile: snapped to spacing multiples so every
    epoch of the same tile lands on identical pixels (required for stacking)."""
    w, s, e, n = tile.bbox
    crs = cfg.target_crs or utm_epsg((w + e) / 2.0, (s + n) / 2.0)
    p = parse_crs(crs)
    if p is not None and p[0] == "geo":
        raise PreprocError("target_crs must be projected (metres), got geographic CRS")
    t = np.linspace(0.0, 1.0, 21)
    lons = np.concatenate([w + (e - w) * t, w + (e - w) * t, np.full(21, w), np.full(21, e)])
    lats = np.concatenate([np.full(21, s), np.full(21, n), s + (n - s) * t, s + (n - s) * t])
    x, y = transform_xy(lons, lats, "EPSG:4326", crs)
    sp = cfg.g80_spacing_m
    x0 = math.floor(float(x.min()) / sp) * sp
    ytop = math.ceil(float(y.max()) / sp) * sp
    nx = int(math.ceil((float(x.max()) - x0) / sp))
    ny = int(math.ceil((ytop - float(y.min())) / sp))
    k = int(round(sp / cfg.g20_spacing_m))
    return {
        "G80": Grid("G80", crs, x0, ytop, sp, sp, ny, nx),
        "G20": Grid("G20", crs, x0, ytop, cfg.g20_spacing_m, cfg.g20_spacing_m, ny * k, nx * k),
    }


def grid_lonlat_bbox(grid: Grid, pad_px: int = 0) -> BBox:
    """WGS84 bounding box of a (padded) grid footprint, with a tiny safety pad."""
    xmin, ymin, xmax, ymax = grid.padded(pad_px).bounds if pad_px else grid.bounds
    t = np.linspace(0.0, 1.0, 21)
    xs = np.concatenate([xmin + (xmax - xmin) * t, xmin + (xmax - xmin) * t, np.full(21, xmin), np.full(21, xmax)])
    ys = np.concatenate([np.full(21, ymin), np.full(21, ymax), ymin + (ymax - ymin) * t, ymin + (ymax - ymin) * t])
    lon, lat = transform_xy(xs, ys, grid.crs, "EPSG:4326")
    eps = 1e-5
    return (float(lon.min()) - eps, float(lat.min()) - eps, float(lon.max()) + eps, float(lat.max()) + eps)


def _int_shift(v: float, tol: float = 1e-6) -> Optional[int]:
    r = round(v)
    return int(r) if abs(v - r) <= tol else None


def _aligned_window(data: np.ndarray, src: Grid, dst: Grid, fill) -> Optional[np.ndarray]:
    if src.crs != dst.crs or abs(src.dx - dst.dx) > 1e-9 or abs(src.dy - dst.dy) > 1e-9:
        return None
    c0 = _int_shift((dst.x0 - src.x0) / src.dx)
    r0 = _int_shift((src.y0 - dst.y0) / src.dy)
    if c0 is None or r0 is None:
        return None
    out = np.full(dst.shape, fill, dtype=data.dtype)
    rs0, rs1 = max(r0, 0), min(r0 + dst.ny, src.ny)
    cs0, cs1 = max(c0, 0), min(c0 + dst.nx, src.nx)
    if rs1 > rs0 and cs1 > cs0:
        out[rs0 - r0:rs1 - r0, cs0 - c0:cs1 - c0] = data[rs0:rs1, cs0:cs1]
    return out


def _block_mean(data: np.ndarray, src: Grid, dst: Grid, min_valid: float = 0.5) -> Optional[np.ndarray]:
    if src.crs != dst.crs:
        return None
    kx, ky = dst.dx / src.dx, dst.dy / src.dy
    ik, jk = _int_shift(kx), _int_shift(ky)
    if ik is None or jk is None or ik < 1 or jk < 1:
        return None
    c0 = _int_shift((dst.x0 - src.x0) / src.dx)
    r0 = _int_shift((src.y0 - dst.y0) / src.dy)
    if c0 is None or r0 is None:
        return None
    big = np.full((dst.ny * jk, dst.nx * ik), np.nan, dtype=np.float32)
    rs0, rs1 = max(r0, 0), min(r0 + dst.ny * jk, src.ny)
    cs0, cs1 = max(c0, 0), min(c0 + dst.nx * ik, src.nx)
    if rs1 > rs0 and cs1 > cs0:
        big[rs0 - r0:rs1 - r0, cs0 - c0:cs1 - c0] = data[rs0:rs1, cs0:cs1]
    blk = big.reshape(dst.ny, jk, dst.nx, ik)
    ok = np.isfinite(blk)
    cnt = ok.sum(axis=(1, 3))
    tot = np.where(ok, blk, 0.0).sum(axis=(1, 3), dtype=np.float64)
    with np.errstate(invalid="ignore", divide="ignore"):
        out = tot / cnt
    out[cnt < max(1.0, min_valid * ik * jk)] = np.nan
    return out.astype(np.float32)


def _inverse_map(data: np.ndarray, src: Grid, dst: Grid, method: str, fill) -> np.ndarray:
    nearest = method == "nearest"
    out_dtype = data.dtype if nearest else np.float32
    out = np.full(dst.shape, fill if nearest else np.nan, dtype=out_dtype)
    xs = dst.x_centers()
    same = src.crs == dst.crs
    src_f = data if nearest else data.astype(np.float32, copy=False)
    chunk = max(1, int(2_000_000 // max(1, dst.nx)))
    for r_start in range(0, dst.ny, chunk):
        r_end = min(dst.ny, r_start + chunk)
        ys = dst.y0 - (np.arange(r_start, r_end) + 0.5) * dst.dy
        X, Y = np.meshgrid(xs, ys)
        if not same:
            X, Y = transform_xy(X, Y, dst.crs, src.crs)
        col = (X - src.x0) / src.dx - 0.5
        row = (src.y0 - Y) / src.dy - 0.5
        inside = (row >= -0.5) & (row <= src.ny - 0.5) & (col >= -0.5) & (col <= src.nx - 0.5)
        if nearest:
            rr = np.clip(np.rint(row).astype(np.int64), 0, src.ny - 1)
            cc = np.clip(np.rint(col).astype(np.int64), 0, src.nx - 1)
            vals = src_f[rr, cc]
            blk = out[r_start:r_end]
            blk[inside] = vals[inside]
        else:
            rc = np.clip(row, 0, src.ny - 1)
            cc = np.clip(col, 0, src.nx - 1)
            r0 = np.minimum(np.floor(rc).astype(np.int64), max(src.ny - 2, 0))
            c0 = np.minimum(np.floor(cc).astype(np.int64), max(src.nx - 2, 0))
            r1 = np.minimum(r0 + 1, src.ny - 1)
            c1 = np.minimum(c0 + 1, src.nx - 1)
            fr = (rc - r0).astype(np.float32)
            fc = (cc - c0).astype(np.float32)
            v = ((1 - fr) * (1 - fc) * src_f[r0, c0] + (1 - fr) * fc * src_f[r0, c1]
                 + fr * (1 - fc) * src_f[r1, c0] + fr * fc * src_f[r1, c1])
            blk = out[r_start:r_end]
            blk[inside] = v[inside]
    return out


def resample(data: np.ndarray, src: Grid, dst: Grid, method: str = "mean", fill=None) -> np.ndarray:
    """Resample ``data`` from ``src`` to ``dst``.

    method: "nearest" (categorical, keeps dtype), "bilinear", or "mean" (area
    average when ``dst`` is an aligned integer multiple of ``src``, else bilinear).
    Pixels outside the source footprint become NaN (float) or ``fill`` (nearest).
    """
    data = np.asarray(data)
    if data.shape != src.shape:
        raise ValueError(f"data shape {data.shape} != src grid {src.shape}")
    if method not in ("nearest", "bilinear", "mean"):
        raise ValueError(f"unknown method {method}")
    if src.same_geometry(dst):
        return data.copy()
    if method == "nearest":
        f = 0 if fill is None else fill
        win = _aligned_window(data, src, dst, f)
        return win if win is not None else _inverse_map(data, src, dst, "nearest", f)
    work = data.astype(np.float32, copy=False)
    win = _aligned_window(work, src, dst, np.nan)
    if win is not None:
        return win
    if method == "mean":
        bm = _block_mean(work, src, dst)
        if bm is not None:
            return bm
    return _inverse_map(work, src, dst, "bilinear", np.nan)


def crop_to(data: np.ndarray, padded: Grid, inner: Grid) -> np.ndarray:
    """Crop an array on ``padded`` back to the aligned ``inner`` grid."""
    out = _aligned_window(np.asarray(data), padded, inner, np.nan if data.dtype.kind == "f" else 0)
    if out is None:
        raise PreprocError("crop_to: grids are not pixel-aligned")
    return out
