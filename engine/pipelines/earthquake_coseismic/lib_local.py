"""3.01 earthquake_coseismic — pipeline-only helpers (ported statistics.ts et al.).

Pure NumPy kernels ported from the app's science core; behavior pinned by
test_lib_local.py. Nothing here may import engine.common's chain or another
pipeline — lib_local is this package's private code.
"""
from __future__ import annotations

import base64
import math

import numpy as np

MAD_SIGMA_FACTOR = 1.4826
EARTH_RADIUS_KM = 6371.0088


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
# geometry helpers
# --------------------------------------------------------------------------- #
def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km (spherical Earth, R = 6371.0088)."""
    rad = math.pi / 180.0
    dlat = (lat2 - lat1) * rad
    dlon = (lon2 - lon1) * rad
    a = (math.sin(dlat / 2.0) ** 2
         + math.cos(lat1 * rad) * math.cos(lat2 * rad) * math.sin(dlon / 2.0) ** 2)
    return 2.0 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def project_los_enu(d_east, d_north, d_up, los_enu) -> np.ndarray:
    """LOS displacement, engine convention: + = TOWARD the sensor.

    los_enu is the ground->sensor unit vector (3, n, n) from the bundle
    geometry; predicted toward-LOS = d . u_gs  (no negation — see the sign
    table in docs/IMPLEMENTATION_PLAN.md 3).
    """
    ux, uy, uz = los_enu[0], los_enu[1], los_enu[2]
    return np.asarray(d_east) * ux + np.asarray(d_north) * uy + np.asarray(d_up) * uz


def project_epicentre(lat: float, lon: float, epsg: int) -> tuple[float, float] | dict:
    """lat/lon -> the grid's projected CRS (UTM). Non-UTM EPSG => explicit skip.

    Replaces the app's Snyder-series utm.ts with pyproj (already a retrieval
    dependency); the honesty guard is preserved: polar-stereographic granules
    refuse the forward model rather than misplace it.
    """
    utm = (32601 <= epsg <= 32660) or (32701 <= epsg <= 32760)
    if not utm:
        return {"error": (
            f"grid EPSG {epsg} is not WGS84 UTM; the forward model only supports UTM grids "
            "(polar-stereographic granules would need a different projection — refusing "
            "rather than misplacing the model)")}
    try:
        from pyproj import Transformer
        tr = Transformer.from_crs(4326, epsg, always_xy=True)
        x, y = tr.transform(lon, lat)
        return (float(x), float(y))
    except Exception as ex:  # pragma: no cover - projection should not fail for UTM
        return {"error": f"projection failed: {ex}"}


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
