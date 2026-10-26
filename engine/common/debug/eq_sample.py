"""Serve a REAL extracted NISAR-format GUNW through the same two calls the chain uses on the retrieval client.

<<<<<<< HEAD
The source is the `data/derived/<slug>/` folder written by dancing-with-sars-eq's `tools/extract_gunw.py`:
`meta.json` + raw `phase.f32` / `coherence.f32` / `components.u16` (row-major, north-up, pixel-centre axes).

    r = EqSampleRetriever("../dancing-with-sars-eq/data/derived/nisar-la-2008")
=======
The source is the `app/data/derived/<slug>/` folder written by the web application's
`app/tools/extract_gunw.py` (originally authored in the dancing-with-sars-eq project,
now integrated in this repository): `meta.json` + raw `phase.f32` / `coherence.f32` /
`components.u16` (row-major, north-up, pixel-centre axes).

    r = EqSampleRetriever("app/data/derived/nisar-la-2008")
>>>>>>> 8200fa0 (Integrate MOTUS earthquake analysis into Dancing With SARs)
    tile = r.best_tile()                       # a 10 km tile fully inside the product with the most valid phase
    bundle = build_scene_bundle(tile, r, as_of=r.as_of, cfg=r.config())

Nothing under engine/retrieval is used or changed except its dataclasses (GranuleRef / GridClip / ...). What the
real product does NOT give us is made visible instead of invented:

* only the three science layers exist on disk -> no iono / tropo screens, no pixel offsets, no mask layer;
* the radarGrid cubes are built from the *scalar* incidence + LOS vector in meta.json (flat, `synthetic_geometry`).
  Both facts are listed in `r.limitations` and printed by the debug report.
"""
from __future__ import annotations

import dataclasses
import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from engine.common.config import CommonConfig, load_common_config
from engine.retrieval.clients.types import FetchResult, GranuleClip, GranuleRef, GridClip
from engine.tiling.mgrs import tile_at

HEIGHTS = np.array([-500.0, 1000.0, 3000.0])
SEC = "science__LSAR__GUNW__grids__frequencyA__unwrappedInterferogram"
RADAR = "science__LSAR__GUNW__metadata__radarGrid"


class EqSampleRetriever:
    """search() / fetch_tile() over one real extracted granule."""

    class _Cfg:
        tiling = {"margin_m": 300.0}

    cfg = _Cfg()

    def __init__(self, folder: str | Path):
        self.folder = Path(folder)
        self.meta = json.loads((self.folder / "meta.json").read_text())
        g = self.meta["grid"]
        self.W, self.H, self.epsg = int(g["width"]), int(g["height"]), int(g["epsg"])
        self.dx = float(g["xSpacing"])
        self.x = float(g["xStart"]) + self.dx * np.arange(self.W)               # pixel centres, ascending
        self.y = float(g["yStart"]) + float(g["ySpacing"]) * np.arange(self.H)   # pixel centres, descending
        self.phase = self._raw("phase.f32", "<f4")
        self.coh = self._raw("coherence.f32", "<f4")
        self.cc = self._raw("components.u16", "<u2")
        ident = self.meta["identification"]
        self.granule_id = ident["granuleId"]
        self.ref = GranuleRef(
            self.granule_id, f"file://{self.folder.resolve()}", "GUNW",
            self._z(ident["referenceZeroDopplerStartTime"]), self._z(ident["secondaryZeroDopplerEndTime"]),
            int(ident["trackNumber"]), int(ident["frameNumber"]), str(self.meta["radar"]["passDirection"]).upper(),
            sum(p.stat().st_size for p in self.folder.iterdir() if p.is_file()),
            [list(p) for p in ident.get("boundingPolygon", [])] or None)
        sec = self._dt(ident["secondaryZeroDopplerEndTime"])
        self.as_of = sec + timedelta(days=4)                                    # a few days after the pair closes
        self.fetches: list = []
        self.limitations = [
            "only unwrappedPhase / coherenceMagnitude / connectedComponents are on disk: no ionosphere or "
            "troposphere screens, no pixelOffsets, no mask layer -> P07 and the offset path have nothing to audit",
            "radarGrid cubes are flat, built from the scalar incidence angle and LOS vector in meta.json "
            "(real numbers, but no spatial variation and no per-height slices)",
            "single granule -> no pair network, no temporal baseline, no GCOV: P08/P10/P11 are skipped honestly",
        ]

    # ------------------------------------------------------------------ helpers
    def _raw(self, name: str, dtype: str) -> np.ndarray:
        a = np.fromfile(self.folder / name, dtype)
        if a.size != self.W * self.H:
            raise ValueError(f"{name}: {a.size} values, expected {self.W}x{self.H}")
        return a.reshape(self.H, self.W)

    @staticmethod
    def _dt(s: str) -> datetime:
        return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)

    @classmethod
    def _z(cls, s: str) -> str:
        return cls._dt(s).strftime("%Y-%m-%dT%H:%M:%SZ")

    # ------------------------------------------------------------------ what a debug run needs
    def config(self, base: CommonConfig | None = None) -> CommonConfig:
        """Common config with the radar scalars this product actually reports (recorded as assumptions by P06)."""
        base = base or load_common_config()
        r = self.meta["radar"]
        radar = dataclasses.replace(base.radar, wavelength_m=float(r["wavelength"]),
                                    gunw_effective_looks=float(r["numberOfLooks"]),
                                    look_side=str(r["lookDirection"]).lower())
        return dataclasses.replace(base, radar=radar)

    def tile_candidates(self, size: int = 10_000) -> list:
        """[(tile, valid_fraction)] for every 10 km tile (+300 m margin) lying fully inside the product."""
        from pyproj import Transformer
        m = 300.0
        out = []
        e_lo, e_hi = self.x[0] - self.dx / 2, self.x[-1] + self.dx / 2
        n_lo, n_hi = self.y[-1] - self.dx / 2, self.y[0] + self.dx / 2
        tr = Transformer.from_crs(self.epsg, 4326, always_xy=True)
        for e0 in np.arange(math.ceil((e_lo + m) / size) * size, e_hi - size - m + 1, size):
            for n0 in np.arange(math.ceil((n_lo + m) / size) * size, n_hi - size - m + 1, size):
                sx = slice(*np.searchsorted(self.x, [e0, e0 + size]))
                sy = slice(*self._rows(n0, n0 + size))
                cc = self.cc[sy, sx]
                ok = np.isfinite(self.phase[sy, sx]) & (cc >= 1) & (cc != 65535)
                if not ok.size:
                    continue
                lon, lat = tr.transform(e0 + size / 2, n0 + size / 2)
                t = tile_at(lon, lat, size)
                if t.epsg == self.epsg and (t.e0, t.n0) == (e0, n0):
                    out.append((t, float(ok.mean())))
        return out

    def best_tile(self, size: int = 10_000, mode: str = "mixed"):
        """mode "best": most valid phase. "mixed": valid fraction closest to 60 % (exercises the P03 gate on real
        NaN / fill / component-0 pixels). "worst": least valid (> 0)."""
        c = [x for x in self.tile_candidates(size) if x[1] > 0]
        if not c:
            raise ValueError("no 10 km tile fits inside this product")
        key = {"best": lambda x: -x[1], "worst": lambda x: x[1], "mixed": lambda x: abs(x[1] - 0.6)}[mode]
        return min(c, key=key)[0]

    def _rows(self, n_lo: float, n_hi: float) -> tuple:
        ys = self.y[::-1]
        i0, i1 = np.searchsorted(ys, [n_lo, n_hi])
        return self.H - i1, self.H - i0

    # ------------------------------------------------------------------ retriever contract
    def search(self, bbox, t0=None, t1=None, product=None, maturity=None, fresh=False, max_results=None):
        if product != "GUNW":
            return [], False
        return [self.ref], False

    def fetch_tile(self, tile, t0=None, t1=None, product=None, layers=None, maturity=None, granules=None, margin_m=None):
        self.fetches.append({"product": product, "layers": layers, "granules": [g.granule_id for g in granules or []]})
        res = FetchResult([], {}, {}, degraded=False)
        for g in granules or []:
            if g.granule_id != self.granule_id:
                res.errors[g.granule_id] = "not served by EqSampleRetriever"
                continue
            clip = self._clip(tile, layers, margin_m if margin_m is not None else 300.0)
            if clip is None:
                res.skipped[g.granule_id] = "bbox does not overlap this granule's grids"
            else:
                res.clips.append(clip)
        return res

    def _clip(self, tile, layers_re, margin):
        if tile.epsg != self.epsg:
            return None
        pat = re.compile(layers_re) if layers_re else None
        x0, x1, y0, y1 = tile.e0 - margin, tile.e0 + tile.size + margin, tile.n0 - margin, tile.n0 + tile.size + margin
        cx = slice(*np.searchsorted(self.x, [x0, x1]))
        cy = slice(*self._rows(y0, y1))
        if cx.stop <= cx.start or cy.stop <= cy.start:
            return None
        x, y = self.x[cx], self.y[cy]
        grids: dict[str, GridClip] = {}
        lay = {"HH/unwrappedPhase": self.phase, "HH/coherenceMagnitude": self.coh, "HH/connectedComponents": self.cc}
        out = {}
        for name, full in lay.items():
            path = f"/{SEC.replace('__', '/')}/{name}"
            if pat is None or pat.search(path):
                out[path.strip("/").replace("/", "__")] = np.ascontiguousarray(full[cy, cx])
        if out:
            grids[SEC] = GridClip(SEC, self.epsg, x, y, {}, out)
        rl = self._radar_cubes(tile, x0, x1, y0, y1, pat)
        if rl is not None:
            grids[RADAR] = rl
        if not grids:
            return None
        return GranuleClip(self.ref, tuple(tile.bbox(300.0)), grids, "network",
                           {"origin": "eq-sample", "synthetic_geometry": True})

    def _radar_cubes(self, tile, x0, x1, y0, y1, pat):
        r = self.meta["radar"]
        step, mg = 2000.0, 4000.0
        ex0, ey1 = math.floor((x0 - mg) / step) * step, math.ceil((y1 + mg) / step) * step
        nx, ny = int(math.ceil((x1 + mg - ex0) / step)), int(math.ceil((ey1 - (y0 - mg)) / step))
        rx, ry = ex0 + step * (np.arange(nx) + 0.5), ey1 - step * (np.arange(ny) + 0.5)
        shape = (len(HEIGHTS), ny, nx)
        hd = math.radians(float(r["headingDeg"]))
        cubes = {"incidenceAngle": np.full(shape, r["incidenceAngleDeg"], np.float32),
                 "losUnitVectorX": np.full(shape, r["losUnitVectorProductX"], np.float32),
                 "losUnitVectorY": np.full(shape, r["losUnitVectorProductY"], np.float32),
                 "alongTrackUnitVectorX": np.full(shape, math.sin(hd), np.float32),
                 "alongTrackUnitVectorY": np.full(shape, math.cos(hd), np.float32)}
        rl = {}
        for name, arr in cubes.items():
            path = f"/{RADAR.replace('__', '/')}/{name}"
            if pat is None or pat.search(path):
                rl[path.strip("/").replace("/", "__")] = arr
        return GridClip(RADAR, self.epsg, rx, ry, {"heightAboveEllipsoid": HEIGHTS.copy()}, rl) if rl else None
