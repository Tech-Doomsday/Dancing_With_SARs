"""Registry of every array the UI can draw. Pixel probes read the stored array, not a re-computation."""
from __future__ import annotations

import threading
from dataclasses import dataclass

import numpy as np

from . import imaging


@dataclass
class Entry:
    aid: str
    data: np.ndarray                     # 2-D (display view); native resolution for clips, decimated for trace
    label: str
    extent: tuple | None = None          # (xmin, xmax, ymin, ymax) pixel EDGES, in `epsg`
    epsg: int | None = None
    stride: int = 1                      # >1: `data` is a decimation of a larger array
    kind: str = "float"                  # float | int | bool
    note: str = ""


class ArrayStore:
    def __init__(self):
        self._d: dict = {}
        self._lock = threading.Lock()

    def put(self, e: Entry) -> str:
        with self._lock:
            self._d[e.aid] = e
        return e.aid

    def get(self, aid: str) -> Entry | None:
        return self._d.get(aid)

    def __len__(self):
        return len(self._d)

    def png(self, aid: str, cmap: str | None, lo: float | None, hi: float | None, max_px: int = 720) -> tuple:
        e = self.get(aid)
        if e is None:
            raise KeyError(aid)
        a, step = imaging.decimate(e.data, max_px)
        cat = e.kind in ("int", "bool") and cmap in (None, "categorical") and _few_values(e.data)
        cmap = cmap or ("categorical" if cat else "viridis")
        if cmap == "categorical":
            lo = hi = 0.0
        elif lo is None or hi is None:
            lo, hi = imaging.auto_range(a, symmetric=(cmap == "diverging"))
        return imaging.render(a, cmap, lo, hi), {"cmap": cmap, "lo": lo, "hi": hi, "shown_stride": step,
                                                 "w": a.shape[1], "h": a.shape[0]}

    def probe(self, aid: str, fx: float, fy: float) -> dict:
        e = self.get(aid)
        if e is None:
            raise KeyError(aid)
        h, w = e.data.shape
        col = min(w - 1, max(0, int(fx * w)))
        row = min(h - 1, max(0, int(fy * h)))
        v = e.data[row, col]
        out = {"row": row * e.stride, "col": col * e.stride, "stride": e.stride,
               "value": None if not np.isfinite(v) else float(v), "finite": bool(np.isfinite(v))}
        if e.extent and e.epsg:
            x0, x1, y0, y1 = e.extent
            x = x0 + (col + 0.5) / w * (x1 - x0)
            y = y1 - (row + 0.5) / h * (y1 - y0)
            out["x"], out["y"], out["epsg"] = float(x), float(y), e.epsg
            try:
                from engine.common._geo import transform_xy
                lon, lat = transform_xy(np.array([x]), np.array([y]), f"EPSG:{e.epsg}", "EPSG:4326")
                out["lon"], out["lat"] = float(lon[0]), float(lat[0])
            except Exception as ex:                          # reported, not hidden
                out["lonlat_error"] = f"{type(ex).__name__}: {ex}"
        return out


def _few_values(a: np.ndarray, cap: int = 24) -> bool:
    f = a[np.isfinite(a)]
    if f.size == 0:
        return False
    s = f[:: max(1, f.size // 20000)]
    return np.unique(s).size <= cap and bool(np.all(s == np.round(s)))
