"""3.01 earthquake_coseismic — pipeline-only helpers.

The robust statistics kernels stay here (ported from the app's statistics.ts; behavior pinned by
test_lib_local.py). Geometry is delegated to engine/lib: the ONE LOS projection lives in
engine/lib/los.py (sign fixed by gate G0) and epicentre projection / distances in engine/lib/geo.py
— this module re-exports them under their historical names so the pipeline code and tests read the
same. lib_local may not import the chain or another pipeline.
"""
from __future__ import annotations

import base64
import math

import numpy as np

from engine.lib import geo as _geo
from engine.lib.los import los_project, los_project_point, los_flip, los_unit_ok

MAD_SIGMA_FACTOR = 1.4826
EARTH_RADIUS_KM = _geo.EARTH_RADIUS_KM


# --------------------------------------------------------------------------- #
# robust statistics (NaN-skipping, ported from statistics.ts)
# --------------------------------------------------------------------------- #
def median(values) -> float:
    """Median of a numeric array (NaNs ignored). NaN for empty input."""
    arr = np.asarray(values, dtype=np.float64).ravel()
    arr = arr[~np.isnan(arr)]
    if arr.size == 0:
        return math.nan
    return float(np.median(arr))


def mad_sigma(values) -> float:
    """1.4826 * median(|v - median|), NaNs ignored; NaN when the median is NaN."""
    arr = np.asarray(values, dtype=np.float64).ravel()
    arr = arr[~np.isnan(arr)]
    if arr.size == 0:
        return math.nan
    med = float(np.median(arr))
    devs = np.abs(arr - med)
    return MAD_SIGMA_FACTOR * float(np.median(devs))


def mean(values) -> float:
    arr = np.asarray(values, dtype=np.float64).ravel()
    arr = arr[~np.isnan(arr)]
    if arr.size == 0:
        return math.nan
    return float(arr.mean())


def std(values) -> float:
    """Sample standard deviation, ddof=1 (matches the TS source). NaNs ignored."""
    arr = np.asarray(values, dtype=np.float64).ravel()
    arr = arr[~np.isnan(arr)]
    if arr.size < 2:
        return math.nan
    return float(arr.std(ddof=1))


def quantile(values, q: float) -> float:
    """Linear-interpolation quantile (NumPy 'linear' method), NaNs ignored."""
    arr = np.asarray(values, dtype=np.float64).ravel()
    arr = arr[~np.isnan(arr)]
    if arr.size == 0:
        return math.nan
    return float(np.quantile(arr, q, method="linear"))


def planar_fit(points) -> dict | None:
    """LSQ plane v ~ a*i + b*j + c via 3x3 Cramer's rule (ported exactly).

    `points` is an iterable of (i, j, v). None when n < 6 or |det| < 1e-12.
    """
    pts = list(points)
    n = len(pts)
    if n < 6:
        return None
    sii = sjj = sij = si = sj = 0.0
    svi = svj = sv = 0.0
    for i, j, v in pts:
        sii += i * i
        sjj += j * j
        sij += i * j
        si += i
        sj += j
        svi += v * i
        svj += v * j
        sv += v
    m = ((sii, sij, si), (sij, sjj, sj), (si, sj, n))
    rhs = (svi, svj, sv)

    def det3(mat):
        return (mat[0][0] * (mat[1][1] * mat[2][2] - mat[1][2] * mat[2][1])
                - mat[0][1] * (mat[1][0] * mat[2][2] - mat[1][2] * mat[2][0])
                + mat[0][2] * (mat[1][0] * mat[2][1] - mat[1][1] * mat[2][0]))

    d = det3(m)
    if abs(d) < 1e-12:
        return None

    def replace(col):
        mm = [list(row) for row in m]
        for r in range(3):
            mm[r][col] = rhs[r]
        return det3(mm)

    return {"a": replace(0) / d, "b": replace(1) / d, "c": replace(2) / d}


def pearson(x, y) -> float:
    """Pearson correlation over finite pairs (n < 3 or zero variance -> NaN)."""
    xs = np.asarray(x, dtype=np.float64).ravel()
    ys = np.asarray(y, dtype=np.float64).ravel()
    ok = ~np.isnan(xs) & ~np.isnan(ys)
    xs, ys = xs[ok], ys[ok]
    n = xs.size
    if n < 3:
        return math.nan
    mx, my = xs.mean(), ys.mean()
    dx, dy = xs - mx, ys - my
    den = math.sqrt(float((dx * dx).sum()) * float((dy * dy).sum()))
    if den == 0.0:
        return math.nan
    return float((dx * dy).sum() / den)


# --------------------------------------------------------------------------- #
# geometry helpers — delegated to engine/lib (single source of truth for signs)
# --------------------------------------------------------------------------- #
def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km (engine.lib.geo)."""
    return _geo.haversine_km(lat1, lon1, lat2, lon2)


def project_los_enu(d_east, d_north, d_up, los_enu) -> np.ndarray:
    """LOS displacement, engine convention: + = TOWARD the sensor (engine.lib.los.los_project)."""
    return los_project(d_east, d_north, d_up, los_enu)


def project_epicentre(lat: float, lon: float, epsg: int) -> tuple[float, float] | dict:
    """lat/lon -> the grid's projected CRS (UTM). Non-UTM EPSG => explicit skip (engine.lib.geo)."""
    return _geo.project_epicentre(lat, lon, epsg)


en_offsets_m = _geo.en_offsets_m
azimuth_deg = _geo.azimuth_deg
epsg_is_utm = _geo.epsg_is_utm


# --------------------------------------------------------------------------- #
# deterministic PRNG (port of mulberry32; 32-bit semantics preserved)
# --------------------------------------------------------------------------- #
def mulberry32(seed: int):
    """Deterministic 32-bit PRNG, bit-exact port of the JS mulberry32."""
    a = seed & 0xFFFFFFFF

    def rng() -> float:
        nonlocal a
        a = (a + 0x6D2B79F5) & 0xFFFFFFFF
        t = ((a ^ (a >> 15)) * (1 | a)) & 0xFFFFFFFF
        t = (t + (((t ^ (t >> 7)) * (61 | t)) & 0xFFFFFFFF)) & 0xFFFFFFFF
        t = (t ^ (t >> 14)) & 0xFFFFFFFF
        return t / 4294967296.0

    return rng


# --------------------------------------------------------------------------- #
# evidence-layer encoding (LayerPayload transport)
# --------------------------------------------------------------------------- #
def core_bounds_lonlat(grid_x, grid_y, epsg: int, fallback=None) -> list:
    """Core-grid geographic corners [W, S, E, N] (plan §6.4: server-computed once).

    `grid_x`/`grid_y` are the core grid's coordinate arrays (metres, EPSG:epsg).
    Falls back to the tile bbox when pyproj is unavailable or the EPSG is not UTM —
    the fallback is a slightly larger rectangle and is what the UI used before this
    field existed.
    """
    try:
        from pyproj import Transformer
        tr = Transformer.from_crs(epsg, 4326, always_xy=True)
        xs = np.asarray(grid_x, dtype=np.float64)
        ys = np.asarray(grid_y, dtype=np.float64)
        if xs.size == 0 or ys.size == 0 or not np.isfinite(xs).all() or not np.isfinite(ys).all():
            raise ValueError("non-finite grid coordinates")
        (lon0, lat0) = tr.transform(float(xs.min()), float(ys.min()))
        (lon1, lat1) = tr.transform(float(xs.max()), float(ys.max()))
        lons = [lon0, lon1]
        lats = [lat0, lat1]
        if not all(math.isfinite(v) for v in lons + lats):
            raise ValueError("non-finite geographic bounds")
        return [min(lons), min(lats), max(lons), max(lats)]
    except Exception:
        if fallback:
            return [float(v) for v in fallback]
        return []


def encode_layer(name: str, arr: np.ndarray, units: str, colormap: str,
                 vmin: float | None = None, vmax: float | None = None, note: str = "") -> dict:
    """Encode a core-grid raster as a little-endian base64 payload."""
    a = np.asarray(arr)
    if a.dtype == np.bool_ or a.dtype == np.uint8:
        payload = a.astype(np.uint8).astype("<u1", copy=False)
    else:
        payload = a.astype(np.float32).astype("<f4", copy=False)
    if vmin is None:
        finite = payload.astype(np.float64)
        finite = finite[np.isfinite(finite)]
        if finite.size:
            lo, hi = float(finite.min()), float(finite.max())
            if colormap.startswith("diverging"):
                span = max(abs(lo), abs(hi))
                vmin, vmax = -span, span
            else:
                vmin, vmax = lo, hi
    return {
        "name": name,
        "dtype": "<u1" if payload.dtype == np.uint8 else "<f4",
        "shape": list(payload.shape),
        "data_b64": base64.b64encode(payload.tobytes()).decode("ascii"),
        "units": units,
        "colormap": colormap,
        "vmin": vmin,
        "vmax": vmax,
        "note": note,
    }
