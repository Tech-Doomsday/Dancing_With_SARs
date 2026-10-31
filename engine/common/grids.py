"""Canonical tile grids (G80, G20) and resampling of native NISAR grids onto them.

Both canonical grids live in the *tile's own UTM CRS* and cover the same extent: the MGRS tile plus a halo of
`halo_px_g80` G80 pixels. Because the tile edge is a multiple of 10 km and NISAR snaps pixel edges to multiples
of its posting, a native grid in the same UTM zone lands exactly on the canonical pixels (an index copy, or an
exact block mean for 20 m -> 80 m). Native grids in a neighbouring zone, or offset by half a pixel, are
resampled (NaN-aware bilinear / nearest / area) instead.

Coordinate convention: x/y arrays are pixel *centres*; x ascends, y descends (row 0 is north), like NISAR.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np
from scipy import ndimage as ndi

from .config import GridsCfg
from .frozen import freeze_array


@dataclass(frozen=True)
class GridSpec:
    name: str                      # "G80" | "G20"
    epsg: int
    dx: float
    dy: float                      # both positive metres
    x: np.ndarray                  # (nx,) pixel-centre eastings, ascending
    y: np.ndarray                  # (ny,) pixel-centre northings, descending
    core: tuple                    # (row0, row1, col0, col1): the tile proper inside the halo

    def __post_init__(self):
        object.__setattr__(self, "x", freeze_array(np.asarray(self.x, dtype=np.float64)))
        object.__setattr__(self, "y", freeze_array(np.asarray(self.y, dtype=np.float64)))

    @property
    def shape(self) -> tuple:
        return (len(self.y), len(self.x))

    @property
    def bounds(self) -> tuple:
        """(xmin, ymin, xmax, ymax) of the pixel edges."""
        return (float(self.x[0] - self.dx / 2), float(self.y[-1] - self.dy / 2),
                float(self.x[-1] + self.dx / 2), float(self.y[0] + self.dy / 2))

    @property
    def transform(self) -> tuple:
        """GDAL-style affine (a, b, c, d, e, f): x = c + a*col, y = f + e*row."""
        xmin, _, _, ymax = self.bounds
        return (self.dx, 0.0, xmin, 0.0, -self.dy, ymax)

    def core_slices(self) -> tuple:
        r0, r1, c0, c1 = self.core
        return slice(r0, r1), slice(c0, c1)


@dataclass(frozen=True)
class GridSet:
    g80: GridSpec
    g20: GridSpec
    ratio: int                     # G80 pixel = ratio x ratio G20 pixels
    index_map_20_to_80: np.ndarray  # (n20, n20) int32: flat G80 index (row * n80 + col) of every G20 pixel

    def __post_init__(self):
        object.__setattr__(self, "index_map_20_to_80", freeze_array(self.index_map_20_to_80))

    def __getitem__(self, name: str) -> GridSpec:
        return {"G80": self.g80, "G20": self.g20, "g80": self.g80, "g20": self.g20}[name]

    def aggregate_20_to_80(self, a20: np.ndarray, how: str = "mean") -> np.ndarray:
        """Aggregate a (n20, n20) array onto G80 through the index map. how: mean | sum | max | any | all."""
        a20 = np.asarray(a20)
        n80 = self.g80.shape[0] * self.g80.shape[1]
        idx = self.index_map_20_to_80.ravel()
        v = a20.ravel()
        if how in ("any", "all"):
            cnt = np.bincount(idx, weights=v.astype(np.float64), minlength=n80)
            tot = np.bincount(idx, minlength=n80)
            out = cnt > 0 if how == "any" else cnt == tot
            return out.reshape(self.g80.shape)
        if how == "max":
            out = np.full(n80, -np.inf)
            np.maximum.at(out, idx, np.where(np.isfinite(v), v, -np.inf))
            out[~np.isfinite(out)] = np.nan
            return out.reshape(self.g80.shape)
        ok = np.isfinite(v)
        s = np.bincount(idx, weights=np.where(ok, v, 0.0), minlength=n80)
        if how == "sum":
            return s.reshape(self.g80.shape)
        c = np.bincount(idx, weights=ok.astype(np.float64), minlength=n80)
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(c > 0, s / c, np.nan).reshape(self.g80.shape)


# ---------------------------------------------------------------------------------------------
# building the canonical grids for a tile
# ---------------------------------------------------------------------------------------------
def make_grid(name: str, epsg: int, e0: float, n0: float, size: float, spacing: float, halo_m: float) -> GridSpec:
    nx = (size + 2 * halo_m) / spacing
    if abs(nx - round(nx)) > 1e-9 or abs(size / spacing - round(size / spacing)) > 1e-9 \
            or abs(halo_m / spacing - round(halo_m / spacing)) > 1e-9:
        raise ValueError(f"tile of {size:g} m with a {halo_m:g} m halo does not divide into {spacing:g} m pixels "
                         "(only tile sizes that are a multiple of the canonical posting are supported)")
    n = int(round(nx))
    xmin, ymax = e0 - halo_m, n0 + size + halo_m
    x = xmin + spacing * (np.arange(n) + 0.5)
    y = ymax - spacing * (np.arange(n) + 0.5)
    h, c = int(round(halo_m / spacing)), int(round(size / spacing))
    return GridSpec(name, int(epsg), float(spacing), float(spacing), x, y, (h, h + c, h, h + c))


def make_tile_grids(tile, cfg: GridsCfg) -> GridSet:
    halo_m = cfg.halo_px_g80 * cfg.g80_m
    ratio = cfg.g80_m / cfg.g20_m
    if abs(ratio - round(ratio)) > 1e-9 or round(ratio) < 1:
        raise ValueError("g80_m must be an integer multiple of g20_m")
    ratio = int(round(ratio))
    g80 = make_grid("G80", tile.epsg, tile.e0, tile.n0, tile.size, cfg.g80_m, halo_m)
    g20 = make_grid("G20", tile.epsg, tile.e0, tile.n0, tile.size, cfg.g20_m, halo_m)
    if g20.shape[0] * g20.shape[1] > cfg.max_g20_pixels:
        raise ValueError(f"canonical G20 grid would have {g20.shape[0] * g20.shape[1]:,} pixels "
                         f"(limit {cfg.max_g20_pixels:,}); use a smaller tile size in config/tiling.yaml")
    n80 = g80.shape[0]
    r = np.arange(g20.shape[0]) // ratio
    c = np.arange(g20.shape[1]) // ratio
    idx = (r[:, None] * n80 + c[None, :]).astype(np.int32)
    return GridSet(g80, g20, ratio, idx)


# ---------------------------------------------------------------------------------------------
# resampling
# ---------------------------------------------------------------------------------------------
@lru_cache(maxsize=32)
def _transformer(src_epsg: int, dst_epsg: int):
    from pyproj import Transformer
    return Transformer.from_crs(src_epsg, dst_epsg, always_xy=True)


def _spacing(axis: np.ndarray, what: str) -> float:
    if len(axis) < 2:
        return 0.0
    s = (float(axis[-1]) - float(axis[0])) / (len(axis) - 1)
    if not np.allclose(np.diff(axis), s, rtol=0, atol=abs(s) * 1e-4):
        raise ValueError(f"{what} axis is not uniformly spaced")
    return s


def _float_nan(a: np.ndarray) -> np.ndarray:
    return a if a.dtype == np.float32 else a.astype(np.float32)


def resample(arr: np.ndarray, src_x: np.ndarray, src_y: np.ndarray, src_epsg: int, dst: GridSpec,
             method: str, *, fill=None, min_weight: float = 0.5, min_valid_fraction: float = 0.5):
    """Resample `arr` (..., ny_src, nx_src) from its native grid onto `dst`.

    method:
      nearest   categorical data (connected components, masks); keeps the integer dtype
      bilinear  continuous data; NaN-aware (weights renormalised, needs `min_weight` of the stencil valid)
      area      power / counts when the destination is coarser: mean of the valid contributing pixels
                (exact block mean when the grids are edge-aligned, box-filtered otherwise);
                falls back to bilinear when the destination is not coarser
    Returns (out, coverage) where coverage is True where the destination pixel centre lies inside the
    source raster. Destination pixels outside the source are NaN (floats) or `fill` (integers).
    """
    arr = np.asarray(arr)
    lead, (sy, sx) = arr.shape[:-2], arr.shape[-2:]
    out_shape = lead + dst.shape
    is_int = arr.dtype.kind in "iu"
    if is_int:
        fill = (np.iinfo(arr.dtype).max if arr.dtype.kind == "u" else np.iinfo(arr.dtype).min) if fill is None else fill
    sdx, sdy = _spacing(np.asarray(src_x), "x"), _spacing(np.asarray(src_y), "y")
    if sx < 2 or sy < 2 or sdx == 0 or sdy == 0:
        out = np.full(out_shape, fill if is_int else np.nan, dtype=arr.dtype if is_int else np.float32)
        return out, np.zeros(dst.shape, bool)

    same_crs = int(src_epsg) == int(dst.epsg)
    if same_crs:
        col1 = (dst.x - src_x[0]) / sdx
        row1 = (dst.y - src_y[0]) / sdy
        col, row = col1[None, :], row1[:, None]
        cov = ((col1 >= -0.5) & (col1 <= sx - 0.5))[None, :] & ((row1 >= -0.5) & (row1 <= sy - 0.5))[:, None]
        cov = np.broadcast_to(cov, dst.shape).copy()
    else:
        xx, yy = np.meshgrid(dst.x, dst.y)
        px, py = _transformer(int(dst.epsg), int(src_epsg)).transform(xx, yy)
        col, row = (np.asarray(px) - src_x[0]) / sdx, (np.asarray(py) - src_y[0]) / sdy
        cov = (col >= -0.5) & (col <= sx - 0.5) & (row >= -0.5) & (row <= sy - 0.5)
        cov &= np.isfinite(col) & np.isfinite(row)

    flat = arr.reshape((-1, sy, sx)) if lead else arr[None]

    if method == "area":
        rx, ry = dst.dx / abs(sdx), dst.dy / abs(sdy)
        if rx > 1.25 and ry > 1.25:
            out = _area(flat, src_x, src_y, sdx, sdy, src_epsg, dst, same_crs, col, row, cov, rx, ry, min_valid_fraction)
            return out.reshape(out_shape), cov
        method = "bilinear"

    if method == "nearest":
        r_i = np.clip(np.rint(row).astype(np.int64), 0, sy - 1)
        c_i = np.clip(np.rint(col).astype(np.int64), 0, sx - 1)
        if same_crs:
            out = flat[:, r_i[:, 0]][:, :, c_i[0]]
        else:
            out = flat[:, r_i, c_i]
        if is_int:
            out = np.where(cov[None], out, np.asarray(fill, dtype=arr.dtype))
        else:
            out = np.where(cov[None], out, np.nan).astype(np.float32)
        return out.reshape(out_shape), cov

    if method != "bilinear":
        raise ValueError(f"unknown resampling method {method!r}")
    if is_int:
        raise ValueError("bilinear resampling of an integer layer: use method='nearest'")
    coords = np.empty((2,) + dst.shape, dtype=np.float64)
    coords[0], coords[1] = np.broadcast_to(row, dst.shape), np.broadcast_to(col, dst.shape)
    res = np.empty((flat.shape[0],) + dst.shape, dtype=np.float32)
    for i in range(flat.shape[0]):
        res[i] = _bilinear_nan(_float_nan(flat[i]), coords, min_weight)
    res[:, ~cov] = np.nan
    return res.reshape(out_shape), cov


def _bilinear_nan(a: np.ndarray, coords: np.ndarray, min_weight: float) -> np.ndarray:
    ok = np.isfinite(a)
    if ok.all():
        w1 = ndi.map_coordinates(np.ones_like(a), coords, order=1, mode="constant", cval=0.0)
        v = ndi.map_coordinates(a, coords, order=1, mode="constant", cval=0.0)
    else:
        a0 = np.where(ok, a, 0.0).astype(np.float32)
        w1 = ndi.map_coordinates(ok.astype(np.float32), coords, order=1, mode="constant", cval=0.0)
        v = ndi.map_coordinates(a0, coords, order=1, mode="constant", cval=0.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        out = v / w1
    out[~(w1 >= min_weight)] = np.nan
    return out.astype(np.float32)


def _area(flat, src_x, src_y, sdx, sdy, src_epsg, dst, same_crs, col, row, cov, rx, ry, min_valid_fraction):
    nyd, nxd = dst.shape
    n, sy, sx = flat.shape
    kx, ky = int(round(rx)), int(round(ry))
    if same_crs and sdx > 0 and sdy < 0 and abs(rx - kx) < 1e-6 and abs(ry - ky) < 1e-6:
        # offset (in source pixels) of the destination's left / top edge relative to the source's
        left = ((dst.x[0] - dst.dx / 2) - (src_x[0] - sdx / 2)) / sdx
        top = ((dst.y[0] + dst.dy / 2) - (src_y[0] - sdy / 2)) / sdy     # sdy < 0 for a north-up source
        if abs(left - round(left)) < 1e-4 and abs(top - round(top)) < 1e-4:
            c0, r0 = int(round(left)), int(round(top))
            big = np.full((n, nyd * ky, nxd * kx), np.nan, dtype=np.float32)
            rs, re_ = max(r0, 0), min(r0 + nyd * ky, sy)
            cs, ce = max(c0, 0), min(c0 + nxd * kx, sx)
            if rs < re_ and cs < ce:
                big[:, rs - r0:re_ - r0, cs - c0:ce - c0] = _float_nan(flat[:, rs:re_, cs:ce])
            blocks = big.reshape(n, nyd, ky, nxd, kx)
            ok = np.isfinite(blocks)
            cnt = ok.sum(axis=(2, 4))
            tot = np.where(ok, blocks, 0.0).sum(axis=(2, 4), dtype=np.float64)
            with np.errstate(invalid="ignore", divide="ignore"):
                out = np.where(cnt >= max(1, min_valid_fraction * ky * kx), tot / cnt, np.nan)
            return out.astype(np.float32)
    # general case: NaN-aware box filter at the destination footprint, then bilinear sampling
    coords = np.empty((2,) + dst.shape, dtype=np.float64)
    coords[0], coords[1] = np.broadcast_to(row, dst.shape), np.broadcast_to(col, dst.shape)
    out = np.empty((n,) + dst.shape, dtype=np.float32)
    size = (max(1, ky), max(1, kx))
    for i in range(n):
        a = _float_nan(flat[i])
        ok = np.isfinite(a)
        s = ndi.uniform_filter(np.where(ok, a, 0.0).astype(np.float64), size=size, mode="constant")
        w = ndi.uniform_filter(ok.astype(np.float64), size=size, mode="constant")
        with np.errstate(invalid="ignore", divide="ignore"):
            sm = np.where(w >= min_valid_fraction, s / w, np.nan).astype(np.float32)
        out[i] = _bilinear_nan(sm, coords, 0.5)
    out[:, ~cov] = np.nan
    return out


def mosaic_first_valid(parts: list, *, fill=np.nan) -> np.ndarray:
    """Combine same-shaped arrays (adjacent frames of one pass): first valid value wins, in the order given."""
    out = np.array(parts[0], copy=True)
    for p in parts[1:]:
        bad = ~np.isfinite(out) if out.dtype.kind == "f" else (out == fill)
        out = np.where(bad, p, out)
    return out


# ---------------------------------------------------------------------------------------------
# pixel-edge geometry for map overlays (engine computes; the UI only places the image)
# ---------------------------------------------------------------------------------------------
def axis_edges(centres: np.ndarray) -> np.ndarray:
    """n pixel-centre coordinates (ascending or descending, evenly spaced) -> n+1 pixel EDGES.
    The first/last edge sit exactly half a pixel beyond the outer centres."""
    c = np.asarray(centres, dtype=np.float64)
    if c.ndim != 1 or c.size < 2:
        raise ValueError("need at least two pixel centres to know the pixel size")
    step = float(np.median(np.diff(c)))
    if step == 0 or not np.isfinite(step):
        raise ValueError("pixel centres are not distinct")
    return np.concatenate([c - step / 2, [c[-1] + step / 2]])


def edge_corners_lonlat(x: np.ndarray, y: np.ndarray, epsg: int) -> tuple:
    """-> (corners, note). corners = [[lon, lat] x4] in the order TL, TR, BR, BL of the IMAGE
    (row 0 = north) taken from the outer pixel EDGES, so a picture of the array lands pixel-exact.
    The quad is NOT axis-aligned in lon/lat (a UTM grid is rotated against the graticule).
    A box that straddles the antimeridian, or one that cannot be projected, answers
    (None, honest reason) instead of a guess."""
    try:
        ex, ey = axis_edges(x), axis_edges(y)
    except ValueError as err:
        return None, f"no corners: {err}"
    west, east = float(min(ex[0], ex[-1])), float(max(ex[0], ex[-1]))
    north, south = float(max(ey[0], ey[-1])), float(min(ey[0], ey[-1]))
    return quad_corners_lonlat(west, east, south, north, epsg)


def extent_corners_lonlat(extent, epsg: int | None) -> tuple:
    """(xmin, xmax, ymin, ymax) PIXEL EDGES in `epsg` -> (corners [TL, TR, BR, BL] as [lon, lat], note).
    Same honest refusals as edge_corners_lonlat (no extent / no epsg / antimeridian -> (None, reason))."""
    if not extent or not epsg:
        return None, "no corners: this array carries no extent / EPSG"
    try:
        x0, x1, y0, y1 = (float(v) for v in extent)
    except (TypeError, ValueError):
        return None, "no corners: the extent is not four numbers"
    return quad_corners_lonlat(min(x0, x1), max(x0, x1), min(y0, y1), max(y0, y1), int(epsg))


def quad_corners_lonlat(west: float, east: float, south: float, north: float, epsg: int) -> tuple:
    from ._geo import transform_xy
    try:
        lon, lat = transform_xy(np.array([west, east, east, west]), np.array([north, north, south, south]),
                                f"EPSG:{int(epsg)}", "EPSG:4326")
    except Exception as err:                         # unsupported CRS etc.: say so
        return None, f"no corners: {type(err).__name__}: {err}"
    lon, lat = np.asarray(lon, float), np.asarray(lat, float)
    if not (np.isfinite(lon).all() and np.isfinite(lat).all()):
        return None, "no corners: projection returned non-finite coordinates"
    if float(lon.max() - lon.min()) > 180.0:
        return None, "no corners: the clip straddles the antimeridian (±180°); the image is not drawn"
    return [[float(a), float(b)] for a, b in zip(lon, lat)], None
