"""Where P09/P12 get DEM, land cover, water occurrence and the other S-layers from.

`engine/retrieval/sources/` and `clients/adapters/` are still empty, so nothing writes `data/ancillary/` yet.
Rather than invent a downloader, engine/common depends on a two-method protocol. Whatever retrieval ends up
producing only has to satisfy `AncillaryProvider`; until then:

* `NullAncillary`      - nothing available. Every layer is reported *missing* (never silently invented) and the
                         pipelines that need it are routed as `unknown`, not `no`.
* `DirectoryAncillary` - reference implementation reading `<root>/<name>/*.npz` (or GeoTIFF when rasterio is
                         installed) plus optional `<root>/facts.json` and `<root>/S7_volcanoes.geojson`.

Raster names and units (the contract a provider must meet):
    dem                   metres (orthometric is fine)             landcover      ESA WorldCover class codes
    water_occurrence      JRC GSW occurrence, 0-100 %  (S3)        tree_cover     tree cover, 0-100 %      (S4)
    builtup               built-up surface, 0-100 %    (S9)        permafrost     0 = none, >0 = zone      (S12)
    glacier               0/1 glacier or ice-sheet     (S8)        river_buffer   0/1 river corridor       (S11)
    shoreline_distance_m  metres to the shoreline (S10), + over land, - over sea
Scalar facts: `volcano_distance_km` (nearest S7 volcano to the tile centre), plus anything a provider adds.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable

import numpy as np

RASTER_NAMES = ("dem", "landcover", "water_occurrence", "tree_cover", "builtup", "permafrost", "glacier",
                "river_buffer", "shoreline_distance_m")

# ESA WorldCover codes (the assumed coding of the `landcover` raster)
WC_TREE, WC_SHRUB, WC_GRASS, WC_CROP, WC_BUILT, WC_BARE, WC_SNOWICE, WC_WATER, WC_WETLAND, WC_MANGROVE, WC_MOSS = \
    10, 20, 30, 40, 50, 60, 70, 80, 90, 95, 100


@dataclass
class RasterLayer:
    name: str
    data: np.ndarray                 # (ny, nx), native values
    x: np.ndarray                    # pixel-centre x, ascending
    y: np.ndarray                    # pixel-centre y, descending
    epsg: int
    source: str = ""
    version: str | None = None
    nodata: float | None = None      # equal to this -> NaN
    note: str = ""


@runtime_checkable
class AncillaryProvider(Protocol):
    def raster(self, name: str, tile, bounds: tuple, epsg: int) -> RasterLayer | None:
        """Raster covering at least `bounds` = (xmin, ymin, xmax, ymax) given in `epsg`; None if not available.
        The raster may be in any CRS / resolution: the caller resamples."""

    def facts(self, tile) -> Mapping[str, Any]:
        """Scalar tile-level facts (see module docstring). Missing key = unknown."""


class NullAncillary:
    """Nothing available."""
    name = "none"

    def raster(self, name, tile, bounds, epsg):
        return None

    def facts(self, tile):
        return {}


def write_raster_npz(path, data, x, y, epsg: int, *, nodata=None, source: str = "", version: str | None = None):
    """Write a raster in the format DirectoryAncillary reads (also used by the tests and by tools)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(path, data=np.asarray(data), x=np.asarray(x, np.float64), y=np.asarray(y, np.float64),
             epsg=np.int64(epsg), nodata=np.float64(np.nan if nodata is None else nodata),
             source=np.str_(source), version=np.str_(version or ""))
    return path


class DirectoryAncillary:
    """`<root>/<name>/<anything>.npz` rasters (x, y centres + epsg inside) or GeoTIFFs; the first file that
    fully covers the requested window wins, otherwise the one overlapping it most."""

    name = "directory"

    def __init__(self, root: str | Path):
        self.root = Path(root)

    # -- rasters -------------------------------------------------------------------------------
    def raster(self, name: str, tile, bounds: tuple, epsg: int) -> RasterLayer | None:
        d = self.root / name
        if not d.is_dir():
            return None
        best, best_cov = None, 0.0
        for f in sorted(d.iterdir()):
            if f.suffix.lower() not in (".npz", ".tif", ".tiff"):
                continue
            b = self._file_bounds(f, epsg)
            if b is None:
                continue
            cov = _overlap_fraction(b, bounds)
            if cov > best_cov + 1e-9:
                best, best_cov = f, cov
            if best_cov >= 0.999:
                break
        if best is None:
            return None
        return self._load(name, best, bounds, epsg)

    def _file_bounds(self, f: Path, epsg: int):
        if f.suffix.lower() == ".npz":
            with np.load(f) as z:
                x, y, e = z["x"], z["y"], int(z["epsg"])
            b = (float(x.min()), float(y.min()), float(x.max()), float(y.max()))
            return _bounds_in(b, e, epsg)
        rio = _rasterio()
        with rio.open(f) as ds:
            b = (ds.bounds.left, ds.bounds.bottom, ds.bounds.right, ds.bounds.top)
            return _bounds_in(b, int(ds.crs.to_epsg()), epsg)

    def _load(self, name: str, f: Path, bounds: tuple, epsg: int) -> RasterLayer:
        if f.suffix.lower() == ".npz":
            with np.load(f) as z:
                nd = float(z["nodata"]) if "nodata" in z.files else float("nan")
                return RasterLayer(name, z["data"], z["x"], z["y"], int(z["epsg"]),
                                   str(z["source"]) if "source" in z.files else f.name,
                                   (str(z["version"]) or None) if "version" in z.files else None,
                                   None if math.isnan(nd) else nd)
        rio = _rasterio()
        from rasterio.windows import from_bounds
        with rio.open(f) as ds:
            src_epsg = int(ds.crs.to_epsg())
            sb = _bounds_in(bounds, epsg, src_epsg)
            pad = max(abs(ds.res[0]), abs(ds.res[1])) * 2
            win = from_bounds(sb[0] - pad, sb[1] - pad, sb[2] + pad, sb[3] + pad, ds.transform)
            win = win.intersection(rio.windows.Window(0, 0, ds.width, ds.height)).round_offsets().round_lengths()
            data = ds.read(1, window=win)
            t = ds.window_transform(win)
            x = t.c + t.a * (np.arange(data.shape[1]) + 0.5)
            y = t.f + t.e * (np.arange(data.shape[0]) + 0.5)
            return RasterLayer(name, data, x, y, src_epsg, f.name, None, ds.nodata)

    # -- facts ---------------------------------------------------------------------------------
    def facts(self, tile) -> Mapping[str, Any]:
        out: dict[str, Any] = {}
        for p in (self.root / "facts.json", self.root / "facts" / f"{tile.id}.json"):
            if p.exists():
                out.update(json.loads(p.read_text()))
        vol = self.root / "S7_volcanoes.geojson"
        if vol.exists() and "volcano_distance_km" not in out:
            lon, lat = tile.center
            best = math.inf
            for feat in json.loads(vol.read_text()).get("features", []):
                g = feat.get("geometry") or {}
                if g.get("type") == "Point":
                    best = min(best, _haversine_km(lon, lat, g["coordinates"][0], g["coordinates"][1]))
            if math.isfinite(best):
                out["volcano_distance_km"] = best
        return out


# ---------------------------------------------------------------------------------------------
def _rasterio():
    try:
        import rasterio
        import rasterio.windows  # noqa: F401
        return rasterio
    except ImportError as ex:  # pragma: no cover
        raise ImportError("GeoTIFF ancillary layers need rasterio (pip install rasterio); .npz layers work without it") from ex


def _bounds_in(b: tuple, src_epsg: int, dst_epsg: int) -> tuple:
    if int(src_epsg) == int(dst_epsg):
        return tuple(float(v) for v in b)
    from pyproj import Transformer
    tr = Transformer.from_crs(src_epsg, dst_epsg, always_xy=True)
    xs = np.linspace(b[0], b[2], 9)
    ys = np.linspace(b[1], b[3], 9)
    gx, gy = np.meshgrid(xs, ys)
    px, py = tr.transform(gx.ravel(), gy.ravel())
    px, py = np.asarray(px), np.asarray(py)
    ok = np.isfinite(px) & np.isfinite(py)
    return (float(px[ok].min()), float(py[ok].min()), float(px[ok].max()), float(py[ok].max()))


def _overlap_fraction(have: tuple, want: tuple) -> float:
    w = max(0.0, min(have[2], want[2]) - max(have[0], want[0]))
    h = max(0.0, min(have[3], want[3]) - max(have[1], want[1]))
    area = (want[2] - want[0]) * (want[3] - want[1])
    return (w * h) / area if area > 0 else 0.0


def _haversine_km(lon1, lat1, lon2, lat2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl, dp = math.radians(lon2 - lon1), p2 - p1
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0088 * 2 * math.asin(min(1.0, math.sqrt(a)))
