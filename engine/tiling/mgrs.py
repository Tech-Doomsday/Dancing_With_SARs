"""MGRS tiles without external MGRS libraries (pyproj + shapely only).

A Tile is one square cell of the MGRS grid at a chosen size (100 km / 10 km / 1 km), e.g. "11SLU35"
= zone 11, band S, 100 km square LU, 10 km cell (3, 5). Each tile knows its UTM EPSG and can give a
lon/lat bbox/polygon for `NisarClient.fetch`, and the tiles covering any footprint polygon.

Limits: UTM latitudes only (-80 .. 84); polar UPS zones raise ValueError. Tiles along a zone
boundary exist in both zones (same as Sentinel-2 tiling). Tile polygons crossing +-180 are returned
with longitudes beyond 180; footprint polygons that cross the antimeridian are not supported.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from functools import lru_cache

BANDS = "CDEFGHJKLMNPQRSTUVWX"
COL_SETS = ("ABCDEFGH", "JKLMNPQR", "STUVWXYZ")
ROW_LETTERS = "ABCDEFGHJKLMNPQRSTUV"
SIZES = {100_000: 0, 10_000: 1, 1_000: 2}          # cell size -> digits per axis in the id
_ID = re.compile(r"^(\d{1,2})([C-X])([A-HJ-NP-Z]{2})(\d*)$")


@lru_cache(maxsize=256)
def _tr(src: int, dst: int):
    from pyproj import Transformer
    return Transformer.from_crs(src, dst, always_xy=True)


def band_of(lat: float) -> str:
    if lat < -80.0 or lat > 84.0:
        raise ValueError(f"latitude {lat} is outside the UTM/MGRS band range (-80..84)")
    return BANDS[min(int((lat + 80.0) // 8), 19)]


def band_lat_lo(band: str) -> float:
    return -80.0 + 8.0 * BANDS.index(band)


def utm_zone(lon: float, lat: float) -> int:
    lon = ((lon + 180.0) % 360.0) - 180.0
    z = int((lon + 180.0) // 6) + 1
    if 56 <= lat < 64 and 3 <= lon < 12:
        z = 32
    if 72 <= lat < 84:
        for lo, hi, zz in ((0, 9, 31), (9, 21, 33), (21, 33, 35), (33, 42, 37)):
            if lo <= lon < hi:
                z = zz
    return z


def zone_cm(zone: int) -> float:
    return -183.0 + 6.0 * zone


def utm_epsg(zone: int, north: bool) -> int:
    return (32600 if north else 32700) + zone


@dataclass(frozen=True)
class Tile:
    zone: int
    band: str
    e0: float            # SW corner, UTM metres (southern hemisphere northings include the 10,000 km false northing)
    n0: float
    size: int = 10_000

    # ------------------------------------------------------------------ identity
    @property
    def north(self) -> bool:
        return self.band >= "N"

    @property
    def epsg(self) -> int:
        return utm_epsg(self.zone, self.north)

    @property
    def id(self) -> str:
        e100, n100 = int(self.e0 // 100_000), int(self.n0 // 100_000)
        col = COL_SETS[(self.zone - 1) % 3][e100 - 1]
        row = ROW_LETTERS[(n100 + (5 if self.zone % 2 == 0 else 0)) % 20]
        d = SIZES[self.size]
        digits = ""
        if d:
            ee = int((self.e0 % 100_000) // self.size)
            nn = int((self.n0 % 100_000) // self.size)
            digits = f"{ee:0{d}d}{nn:0{d}d}"
        return f"{self.zone:02d}{self.band}{col}{row}{digits}"

    def __str__(self):
        return self.id

    # ------------------------------------------------------------------ geometry
    def ring_utm(self, margin_m: float = 0.0, per_edge: int = 4):
        s, m = self.size, margin_m
        x0, y0, x1, y1 = self.e0 - m, self.n0 - m, self.e0 + s + m, self.n0 + s + m
        pts = []
        for i in range(per_edge):
            pts.append((x0 + (x1 - x0) * i / per_edge, y0))
        for i in range(per_edge):
            pts.append((x1, y0 + (y1 - y0) * i / per_edge))
        for i in range(per_edge):
            pts.append((x1 - (x1 - x0) * i / per_edge, y1))
        for i in range(per_edge):
            pts.append((x0, y1 - (y1 - y0) * i / per_edge))
        return pts

    def polygon_lonlat(self, margin_m: float = 0.0) -> list[tuple[float, float]]:
        tr = _tr(self.epsg, 4326)
        pts = self.ring_utm(margin_m)
        lon, lat = tr.transform([p[0] for p in pts], [p[1] for p in pts])
        c = self.center[0]                       # unwrap so a tile crossing +-180 stays continuous (lon may exceed 180)
        lon = [c + ((x - c + 180.0) % 360.0) - 180.0 for x in lon]
        ring = list(zip(lon, lat))
        return ring + [ring[0]]

    def bbox(self, margin_m: float = 0.0) -> tuple[float, float, float, float]:
        ring = self.polygon_lonlat(margin_m)
        xs, ys = [p[0] for p in ring], [p[1] for p in ring]
        return (min(xs), min(ys), max(xs), max(ys))

    @property
    def center(self) -> tuple[float, float]:
        lon, lat = _tr(self.epsg, 4326).transform(self.e0 + self.size / 2, self.n0 + self.size / 2)
        return lon, lat

    def pixel_window_hint(self, spacing_m: float) -> int:
        return int(round(self.size / spacing_m))


# ---------------------------------------------------------------------- constructors
def _make(zone: int, north_hint: bool, e0: float, n0: float, size: int) -> Tile | None:
    """Tile from a UTM cell; band comes from the cell centre, clamped to the hemisphere the cell is
    defined in. None if the cell is outside the valid MGRS column range or latitude range."""
    if not 1 <= int(e0 // 100_000) <= 8:
        return None
    lon, lat = _tr(utm_epsg(zone, north_hint), 4326).transform(e0 + size / 2, n0 + size / 2)
    if lat < -80.0 or lat > 84.0:
        return None
    band = band_of(lat)
    if north_hint and band < "N":
        band = "N"
    if not north_hint and band >= "N":
        band = "M"
    return Tile(zone, band, float(e0), float(n0), size)


def tile_at(lon: float, lat: float, size: int = 10_000) -> Tile:
    if size not in SIZES:
        raise ValueError(f"size must be one of {sorted(SIZES)}")
    band_of(lat)                                    # range check
    zone, north = utm_zone(lon, lat), lat >= 0
    e, n = _tr(4326, utm_epsg(zone, north)).transform(lon, lat)
    t = _make(zone, north, math.floor(e / size) * size, math.floor(n / size) * size, size)
    if t is None:
        raise ValueError(f"({lon}, {lat}) is outside the MGRS grid")
    return t


def tile_from_id(tile_id: str) -> Tile:
    m = _ID.match(tile_id.replace(" ", "").upper())
    if not m:
        raise ValueError(f"not an MGRS tile id: {tile_id!r}")
    zone, band, letters, digits = int(m.group(1)), m.group(2), m.group(3), m.group(4)
    if len(digits) % 2 or len(digits) // 2 > 2 or not 1 <= zone <= 60:
        raise ValueError(f"unsupported MGRS precision in {tile_id!r}")
    d = len(digits) // 2
    size = 10 ** (5 - d)
    col_idx = COL_SETS[(zone - 1) % 3].find(letters[0]) + 1
    if col_idx == 0:
        raise ValueError(f"column letter {letters[0]} invalid for zone {zone}")
    row_mod = (ROW_LETTERS.index(letters[1]) - (5 if zone % 2 == 0 else 0)) % 20
    north = band >= "N"
    lat_lo = band_lat_lo(band)
    tr = _tr(4326, utm_epsg(zone, north))
    cm = zone_cm(zone)
    n_min = min(tr.transform(lon, lat_lo)[1] for lon in (cm - 3.0, cm, cm + 3.0))
    floor100 = math.floor(n_min / 100_000)
    n100 = floor100 + ((row_mod - floor100) % 20)
    ee = int(digits[:d]) if d else 0
    nn = int(digits[d:]) if d else 0
    return Tile(zone, band, col_idx * 100_000 + ee * size, n100 * 100_000 + nn * size, size)


# ---------------------------------------------------------------------- coverage
def _zones_for(minlon, maxlon, minlat, maxlat) -> list[int]:
    zlo, zhi = int((minlon + 180) // 6) + 1, int((maxlon + 180) // 6) + 1
    zones = set(range(max(zlo, 1), min(zhi, 60) + 1))
    if maxlat >= 56 and minlon < 42 and maxlon > 0:       # Norway / Svalbard exception zones
        zones |= {31, 32, 33, 35, 37}
    return sorted(zones)


def tiles_for_polygon(ring_lonlat, size: int = 10_000, margin_m: float = 0.0) -> list[Tile]:
    """All tiles whose cell (grown by margin_m) intersects the polygon, in every UTM zone it touches."""
    from shapely.geometry import Polygon, box
    import numpy as np
    import shapely
    from shapely.prepared import prep

    if size not in SIZES:
        raise ValueError(f"size must be one of {sorted(SIZES)}")
    poly = Polygon(ring_lonlat)
    if not poly.is_valid:
        poly = poly.buffer(0)
    minlon, minlat, maxlon, maxlat = poly.bounds
    out: dict[str, Tile] = {}
    for zone in _zones_for(minlon, maxlon, minlat, maxlat):
        strip = box(zone_cm(zone) - 3.0, -80.0, zone_cm(zone) + 3.0, 84.0)
        part = poly.intersection(strip)
        if part.is_empty:
            continue
        for north in (True, False):
            hemi = part.intersection(box(-180, 0, 180, 84) if north else box(-180, -80, 180, 0))
            if hemi.is_empty or hemi.area == 0:
                continue
            tr = _tr(4326, utm_epsg(zone, north))
            g = shapely.transform(hemi, lambda c: np.column_stack(tr.transform(c[:, 0], c[:, 1])))
            if margin_m:
                g = g.buffer(margin_m)
            pg = prep(g)
            x0, y0, x1, y1 = g.bounds
            for i in range(math.floor(x0 / size), math.floor(x1 / size) + 1):
                for j in range(math.floor(y0 / size), math.floor(y1 / size) + 1):
                    cell = box(i * size, j * size, (i + 1) * size, (j + 1) * size)
                    if not pg.intersects(cell):
                        continue
                    t = _make(zone, north, i * size, j * size, size)
                    if t is not None:
                        out[t.id] = t
    return sorted(out.values(), key=lambda t: t.id)


def tiles_for_bbox(bbox, size: int = 10_000, margin_m: float = 0.0) -> list[Tile]:
    w, s, e, n = bbox
    return tiles_for_polygon([(w, s), (e, s), (e, n), (w, n), (w, s)], size, margin_m)
