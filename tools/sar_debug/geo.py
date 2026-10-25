"""
Tiny georeferencing helper.

Uses pyproj when it is installed. Without it, a built-in pure-numpy fallback handles the only cases
NISAR gridded products normally need: WGS84 lon/lat (EPSG:4326) and WGS84 UTM (EPSG:326zz / 327zz).
Anything else (polar stereographic, ...) returns None and the caller reports "no georeference".
"""
from __future__ import annotations

import math

import numpy as np

_A = 6378137.0
_F = 1 / 298.257223563
_N = _F / (2 - _F)
_K0 = 0.9996
_AA = _A / (1 + _N) * (1 + _N ** 2 / 4 + _N ** 4 / 64 + _N ** 6 / 256)
_ALPHA = [_N / 2 - 2 * _N ** 2 / 3 + 5 * _N ** 3 / 16 + 41 * _N ** 4 / 180,
          13 * _N ** 2 / 48 - 3 * _N ** 3 / 5 + 557 * _N ** 4 / 1440,
          61 * _N ** 3 / 240 - 103 * _N ** 4 / 140,
          49561 * _N ** 4 / 161280]
_BETA = [_N / 2 - 2 * _N ** 2 / 3 + 37 * _N ** 3 / 96 - _N ** 4 / 360,
         _N ** 2 / 48 + _N ** 3 / 15 - 437 * _N ** 4 / 1440,
         17 * _N ** 3 / 480 - 37 * _N ** 4 / 840,
         4397 * _N ** 4 / 161280]
_DELTA = [2 * _N - 2 * _N ** 2 / 3 - 2 * _N ** 3 + 116 * _N ** 4 / 45,
          7 * _N ** 2 / 3 - 8 * _N ** 3 / 5 - 227 * _N ** 4 / 45,
          56 * _N ** 3 / 15 - 136 * _N ** 4 / 35,
          4279 * _N ** 4 / 630]


class _Utm:
    """WGS84 UTM, Krueger series (accurate to well under a millimetre inside a zone)."""

    def __init__(self, zone: int, south: bool):
        self.lon0 = math.radians((zone - 1) * 6 - 180 + 3)
        self.n0 = 10_000_000.0 if south else 0.0

    def fwd(self, lon, lat):
        lon, lat = np.asarray(lon, float), np.asarray(lat, float)
        phi, dl = np.radians(lat), np.radians(lon) - self.lon0
        e = 2 * math.sqrt(_N) / (1 + _N)
        s = np.sin(phi)
        t = np.sinh(np.arctanh(s) - e * np.arctanh(e * s))
        xi_p = np.arctan2(t, np.cos(dl))
        eta_p = np.arctanh(np.sin(dl) / np.sqrt(1 + t * t))
        xi, eta = xi_p.copy(), eta_p.copy()
        for j, a in enumerate(_ALPHA, 1):
            xi += a * np.sin(2 * j * xi_p) * np.cosh(2 * j * eta_p)
            eta += a * np.cos(2 * j * xi_p) * np.sinh(2 * j * eta_p)
        return 500000.0 + _K0 * _AA * eta, self.n0 + _K0 * _AA * xi

    def inv(self, x, y):
        x, y = np.asarray(x, float), np.asarray(y, float)
        xi, eta = (y - self.n0) / (_K0 * _AA), (x - 500000.0) / (_K0 * _AA)
        xi_p, eta_p = xi.copy(), eta.copy()
        for j, b in enumerate(_BETA, 1):
            xi_p -= b * np.sin(2 * j * xi) * np.cosh(2 * j * eta)
            eta_p -= b * np.cos(2 * j * xi) * np.sinh(2 * j * eta)
        chi = np.arcsin(np.sin(xi_p) / np.cosh(eta_p))
        phi = chi.copy()
        for j, d in enumerate(_DELTA, 1):
            phi += d * np.sin(2 * j * chi)
        lon = self.lon0 + np.arctan2(np.sinh(eta_p), np.cos(xi_p))
        return np.degrees(lon), np.degrees(phi)


class _Identity:
    def fwd(self, lon, lat):
        return np.asarray(lon, float), np.asarray(lat, float)

    inv = fwd


class _Pyproj:
    def __init__(self, epsg):
        from pyproj import Transformer
        self._f = Transformer.from_crs(4326, epsg, always_xy=True)
        self._i = Transformer.from_crs(epsg, 4326, always_xy=True)

    def fwd(self, lon, lat):
        return self._f.transform(lon, lat)

    def inv(self, x, y):
        return self._i.transform(x, y)


def make_transformer(epsg):
    """Object with .fwd(lon, lat) -> (x, y) and .inv(x, y) -> (lon, lat); None if unsupported."""
    try:
        epsg = int(epsg)
    except (TypeError, ValueError):
        return None
    try:
        return _Pyproj(epsg)
    except Exception:
        pass
    if epsg == 4326:
        return _Identity()
    if 32601 <= epsg <= 32660:
        return _Utm(epsg - 32600, False)
    if 32701 <= epsg <= 32760:
        return _Utm(epsg - 32700, True)
    return None


def backend() -> str:
    try:
        import pyproj  # noqa: F401
        return "pyproj " + getattr(pyproj, "__version__", "")
    except Exception:
        return "built-in UTM fallback (pip install pyproj for other projections)"
