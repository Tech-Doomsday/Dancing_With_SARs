"""Shared geography helpers: epicentre projection into a tile's CRS, great-circle distances.

pyproj replaces the app's Snyder-series utm.ts (already a retrieval dependency). Two behaviours are
kept from the port spec and are honesty guards, not conveniences:
  * epicentres near a UTM zone edge are still projected into the TILE's zone (the tile fixes the CRS);
  * a non-UTM grid EPSG (polar-stereographic granules) REFUSES the forward model with an explicit
    reason rather than misplacing it.
"""
from __future__ import annotations

import math

import numpy as np

EARTH_RADIUS_KM = 6371.0088

_UTM_RANGES = ((32601, 32660), (32701, 32760))       # WGS84 north / south zones


def epsg_is_utm(epsg: int) -> bool:
    try:
        e = int(epsg)
    except (TypeError, ValueError):
        return False
    return any(lo <= e <= hi for lo, hi in _UTM_RANGES)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km (spherical Earth, R = 6371.0088)."""
    rad = math.pi / 180.0
    dlat = (lat2 - lat1) * rad
    dlon = (lon2 - lon1) * rad
    a = (math.sin(dlat / 2.0) ** 2
         + math.cos(lat1 * rad) * math.cos(lat2 * rad) * math.sin(dlon / 2.0) ** 2)
    return 2.0 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def project_epicentre(lat: float, lon: float, epsg: int) -> tuple[float, float] | dict:
    """lat/lon (WGS84) -> the grid's projected CRS, metres.

    Returns (x, y) or {"error": reason}. A non-UTM EPSG is an explicit skip, never a wrong answer:
    the forward model would place the fault kilometres away and the consistency screen would measure
    the misplacement, not the signal.
    """
    if not epsg_is_utm(epsg):
        return {"error": (
            f"grid EPSG {epsg} is not WGS84 UTM; the forward model only supports UTM grids "
            "(polar-stereographic granules would need a different projection — refusing "
            "rather than misplacing the model)")}
    try:
        from pyproj import Transformer
        tr = Transformer.from_crs(4326, int(epsg), always_xy=True)
        x, y = tr.transform(float(lon), float(lat))
        return (float(x), float(y))
    except Exception as ex:  # pragma: no cover - projection should not fail for UTM
        return {"error": f"projection failed: {ex}"}


def en_offsets_m(ref_lat, ref_lon, lat, lon):
    """Local-metre (east, north) offsets of lat/lon point(s) relative to a reference (spherical).

    Accepts scalars or arrays (numpy-vectorized); returns matching (east, north)."""
    mean_lat = np.radians((np.asarray(ref_lat, dtype=np.float64) + np.asarray(lat, dtype=np.float64)) / 2.0)
    east = np.radians(np.asarray(lon, dtype=np.float64) - np.asarray(ref_lon, dtype=np.float64)) \
        * EARTH_RADIUS_KM * 1000.0 * np.cos(mean_lat)
    north = np.radians(np.asarray(lat, dtype=np.float64) - np.asarray(ref_lat, dtype=np.float64)) \
        * EARTH_RADIUS_KM * 1000.0
    if east.ndim == 0:
        return float(east), float(north)
    return east, north


def azimuth_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Azimuth (degrees clockwise from north) from point A to point B on the sphere."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return math.degrees(math.atan2(y, x))


__all__ = ["epsg_is_utm", "haversine_km", "project_epicentre", "en_offsets_m", "azimuth_deg",
           "EARTH_RADIUS_KM"]
