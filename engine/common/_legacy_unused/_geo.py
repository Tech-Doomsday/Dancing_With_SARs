"""Minimal CRS support without pyproj: WGS84 geographic and WGS84/UTM (EPSG:326xx/327xx).

Krueger n-series (Karney 2011), sub-millimetre accurate within a UTM zone and
well-behaved across neighbouring zones. Anything else (polar stereographic, ...)
falls back to pyproj if it happens to be installed, else raises a clear error.
"""
from __future__ import annotations

import math
from typing import Optional, Tuple

import numpy as np

from ._types import PreprocError

_A = 6378137.0
_F = 1.0 / 298.257223563
_N = _F / (2.0 - _F)
_K0 = 0.9996
_AA = _A / (1 + _N) * (1 + _N**2 / 4 + _N**4 / 64 + _N**6 / 256)
_ALPHA = (
    _N / 2 - 2 * _N**2 / 3 + 5 * _N**3 / 16 + 41 * _N**4 / 180,
    13 * _N**2 / 48 - 3 * _N**3 / 5 + 557 * _N**4 / 1440,
    61 * _N**3 / 240 - 103 * _N**4 / 140,
    49561 * _N**4 / 161280,
)
_BETA = (
    _N / 2 - 2 * _N**2 / 3 + 37 * _N**3 / 96 - _N**4 / 360,
    _N**2 / 48 + _N**3 / 15 - 437 * _N**4 / 1440,
    17 * _N**3 / 480 - 37 * _N**4 / 840,
    4397 * _N**4 / 161280,
)
_DELTA = (
    2 * _N - 2 * _N**2 / 3 - 2 * _N**3 + 116 * _N**4 / 45,
    7 * _N**2 / 3 - 8 * _N**3 / 5 - 227 * _N**4 / 45,
    56 * _N**3 / 15 - 136 * _N**4 / 35,
    4279 * _N**4 / 630,
)


def utm_zone(lon: float) -> int:
    return int(min(60, max(1, math.floor((lon + 180.0) / 6.0) + 1)))


def utm_epsg(lon: float, lat: float) -> str:
    return f"EPSG:{(32600 if lat >= 0 else 32700) + utm_zone(lon)}"


def parse_crs(crs: str) -> Optional[Tuple]:
    """('geo',) | ('utm', zone, north) | None if unsupported natively."""
    s = str(crs).strip().upper().replace("EPSG::", "EPSG:")
    if s in ("EPSG:4326", "WGS84", "OGC:CRS84"):
        return ("geo",)
    if s.startswith("EPSG:"):
        try:
            code = int(s.split(":")[1])
        except ValueError:
            return None
        if 32601 <= code <= 32660:
            return ("utm", code - 32600, True)
        if 32701 <= code <= 32760:
            return ("utm", code - 32700, False)
    return None


def lonlat_to_utm(lon, lat, zone: int, north: bool = True):
    lon = np.asarray(lon, dtype=float)
    lat = np.asarray(lat, dtype=float)
    lon0 = math.radians((zone - 1) * 6 - 180 + 3)
    phi = np.radians(lat)
    lam = np.radians(lon) - lon0
    lam = (lam + np.pi) % (2 * np.pi) - np.pi
    e = 2 * math.sqrt(_N) / (1 + _N)
    sp = np.sin(phi)
    t = np.sinh(np.arctanh(sp) - e * np.arctanh(e * sp))
    xi_p = np.arctan2(t, np.cos(lam))
    eta_p = np.arctanh(np.sin(lam) / np.sqrt(1 + t * t))
    xi, eta = xi_p.copy(), eta_p.copy()
    for j, a in enumerate(_ALPHA, start=1):
        xi = xi + a * np.sin(2 * j * xi_p) * np.cosh(2 * j * eta_p)
        eta = eta + a * np.cos(2 * j * xi_p) * np.sinh(2 * j * eta_p)
    x = 500000.0 + _K0 * _AA * eta
    y = _K0 * _AA * xi + (0.0 if north else 10000000.0)
    return x, y


def utm_to_lonlat(x, y, zone: int, north: bool = True):
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    eta_p = (x - 500000.0) / (_K0 * _AA)
    xi_p = (y - (0.0 if north else 10000000.0)) / (_K0 * _AA)
    xi, eta = xi_p.copy(), eta_p.copy()
    for j, b in enumerate(_BETA, start=1):
        xi = xi - b * np.sin(2 * j * xi_p) * np.cosh(2 * j * eta_p)
        eta = eta - b * np.cos(2 * j * xi_p) * np.sinh(2 * j * eta_p)
    chi = np.arcsin(np.sin(xi) / np.cosh(eta))
    phi = chi.copy()
    for j, d in enumerate(_DELTA, start=1):
        phi = phi + d * np.sin(2 * j * chi)
    lam = np.arctan2(np.sinh(eta), np.cos(xi))
    lon = np.degrees(lam) + ((zone - 1) * 6 - 180 + 3)
    return lon, np.degrees(phi)


def _to_lonlat(x, y, p):
    if p[0] == "geo":
        return np.asarray(x, float), np.asarray(y, float)
    return utm_to_lonlat(x, y, p[1], p[2])


def _from_lonlat(lon, lat, p):
    if p[0] == "geo":
        return np.asarray(lon, float), np.asarray(lat, float)
    return lonlat_to_utm(lon, lat, p[1], p[2])


def transform_xy(x, y, src_crs: str, dst_crs: str):
    """Transform coordinate arrays between CRSs."""
    if str(src_crs) == str(dst_crs):
        return np.asarray(x, float), np.asarray(y, float)
    ps, pd = parse_crs(src_crs), parse_crs(dst_crs)
    if ps is not None and pd is not None:
        lon, lat = _to_lonlat(x, y, ps)
        return _from_lonlat(lon, lat, pd)
    try:  # optional dependency, only for exotic CRSs
        from pyproj import Transformer  # type: ignore
    except ImportError:
        raise PreprocError(
            f"CRS transform {src_crs} -> {dst_crs} needs pyproj (only WGS84 and UTM are built in)"
        ) from None
    tr = Transformer.from_crs(src_crs, dst_crs, always_xy=True)
    xx, yy = tr.transform(np.asarray(x, float), np.asarray(y, float))
    return np.asarray(xx), np.asarray(yy)
