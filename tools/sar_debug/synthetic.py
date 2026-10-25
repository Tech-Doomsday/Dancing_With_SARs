"""
Synthetic data for --demo and --selftest. Nothing here is real NISAR data.

  build_scene()          a GUNW-like granule as an in-memory tree (MiniFile) - or a real .h5 if h5py exists
  fake_granules()        a search result: one well-populated lane, one cold-start lane
  make_engine_tree()     a tiny fake Dancing_With_SARs checkout (stubs, manifests, verdicts.db, a clip)

MiniFile implements only the slice of the h5py API that sar_debug uses. It exists so the HDF5 code path
can be exercised on machines without h5py; it is never used when a real file is opened.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import date, timedelta

import numpy as np

from . import geo
from .model import Granule

LAMBDA = 299_792_458.0 / 1.2575e9          # NISAR L-band, about 23.84 cm
EPSG = 32611
CENTER = (-118.25, 34.05)
DEMO_BBOX = (-118.45, 33.90, -118.05, 34.20)


# ----------------------------------------------------------------------------- h5py stand-in

class _Node:
    def __init__(self, name, parent):
        self.name, self.parent, self.attrs = name, parent, {}


class MiniDataset(_Node):
    def __init__(self, name, parent, data, attrs=None, chunks=None, compression=None):
        super().__init__(name, parent)
        self._d = np.asarray(data)
        self.shape, self.dtype, self.ndim, self.size = self._d.shape, self._d.dtype, self._d.ndim, self._d.size
        self.chunks, self.compression = chunks, compression
        self.attrs = dict(attrs or {})

    def __getitem__(self, idx):
        return self._d[idx] if idx != () else (self._d[()] if self._d.ndim == 0 else self._d.copy())

    def __len__(self):
        return self.shape[0]


class MiniGroup(_Node):
    def __init__(self, name, parent):
        super().__init__(name, parent)
        self._c = {}

    def _root(self):
        n = self
        while n.parent is not None and n.parent is not n:
            n = n.parent
        return n

    def group(self, key):
        if key not in self._c:
            self._c[key] = MiniGroup(self.name.rstrip("/") + "/" + key, self)
        return self._c[key]

    def dataset(self, key, data, attrs=None, chunks=None, compression=None):
        self._c[key] = MiniDataset(self.name.rstrip("/") + "/" + key, self, data, attrs, chunks, compression)
        return self._c[key]

    def keys(self):
        return list(self._c.keys())

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def __contains__(self, key):
        try:
            self[key]
            return True
        except KeyError:
            return False

    def __getitem__(self, path):
        node = self._root() if path.startswith("/") else self
        for part in [p for p in path.split("/") if p]:
            if not isinstance(node, MiniGroup) or part not in node._c:
                raise KeyError(path)
            node = node._c[part]
        return node

    def __iter__(self):
        return iter(self._c)


class MiniFile(MiniGroup):
    reader = "in-memory stand-in for h5py (h5py is not installed here)"

    def __init__(self):
        super().__init__("/", None)
        self.parent = self


def to_h5py_file(mini: MiniFile, path: str):
    """Write the in-memory tree to a real HDF5 file (only possible when h5py is installed)."""
    import h5py

    def copy(src, dst):
        for k in src.keys():
            n = src[k]
            if isinstance(n, MiniGroup):
                g = dst.create_group(k)
                copy(n, g)
            else:
                kw = {}
                if n.ndim >= 2:
                    kw = {"chunks": tuple(min(s, 128) for s in n.shape), "compression": "gzip", "shuffle": True}
                d = dst.create_dataset(k, data=n[()] if n.ndim else n._d, **kw)
                for a, v in n.attrs.items():
                    d.attrs[a] = v

    with h5py.File(path, "w") as f:
        copy(mini, f)


# ----------------------------------------------------------------------------- synthetic GUNW

def _smooth_noise(rng, ny, nx, scale):
    a = rng.normal(size=(ny, nx))
    fy, fx = np.fft.fftfreq(ny)[:, None], np.fft.fftfreq(nx)[None, :]
    f = np.fft.ifft2(np.fft.fft2(a) * np.exp(-(fx ** 2 + fy ** 2) * (scale ** 2) * 2 * np.pi ** 2)).real
    return f / (f.std() or 1)


def build_scene(seed=7, ny=520, nx=640, spacing=80.0):
    """GUNW-like granule. Contains, on purpose: a deformation lobe, an orbital ramp, a decorrelated lake,
    an island with a 2*pi offset, a block with a cycle slip, a noisy patch and a few non-zero pixels outside the components."""
    rng = np.random.default_rng(seed)
    tr = geo.make_transformer(EPSG)
    cx, cy = tr.fwd(*CENTER)
    xs = cx + (np.arange(nx) - nx / 2 + 0.5) * spacing
    ys = cy + (ny / 2 - np.arange(ny) - 0.5) * spacing                  # north-up: y decreases down the rows
    X, Y = np.meshgrid(np.arange(nx), np.arange(ny))

    def gauss(x0, y0, s):
        return np.exp(-((X - x0) ** 2 + (Y - y0) ** 2) / (2 * s ** 2))

    defo_m = 0.11 * (gauss(300, 250, 55) - 0.8 * gauss(380, 280, 45))     # LOS length change, metres
    ramp = 0.9 * (X / nx - 0.5) + 0.35 * (Y / ny - 0.5)
    iono = 0.35 * _smooth_noise(rng, ny, nx, 40)
    true = 4 * np.pi * defo_m / LAMBDA + ramp + iono

    lake = gauss(150, 330, 40) > 0.35
    island = gauss(150, 330, 40) > 0.9
    coh = np.clip(0.78 + 0.12 * _smooth_noise(rng, ny, nx, 25) - 0.25 * gauss(480, 120, 60), 0.02, 0.99)
    coh[lake] = np.clip(0.12 + 0.05 * rng.normal(size=lake.sum()), 0.02, 0.25)
    coh[island] = np.clip(0.55 + 0.05 * rng.normal(size=island.sum()), 0.3, 0.8)
    noisy = (X > 430) & (X < 560) & (Y > 380) & (Y < 480)
    coh[noisy] = np.clip(0.34 + 0.03 * rng.normal(size=noisy.sum()), 0.3, 0.45)

    noise_sigma = np.sqrt(np.maximum(1 - coh ** 2, 0.01)) / (2 * coh + 1e-3) * 0.5
    noise = rng.normal(size=(ny, nx)) * noise_sigma
    noise[noisy] = rng.normal(size=noisy.sum()) * 2.0
    observed = true + noise

    cc = np.zeros((ny, nx), np.uint32)
    cc[coh >= 0.3] = 1
    cc[island] = 2
    unw = observed.copy()
    unw[(X > 380) & (X < 420) & (Y > 90) & (Y < 102)] += 2 * np.pi        # a cycle slip block
    unw[island] += 4 * np.pi                                               # island unwrapped independently
    unw += rng.normal(size=(ny, nx)) * 0.03                                # not bit-identical to the wrapped phase
    unw[cc == 0] = 0.0
    leak = (cc == 0) & (rng.random((ny, nx)) < 0.003)
    unw[leak] = rng.normal(size=leak.sum())

    wrapped = (0.4 + 0.6 * coh) * np.exp(1j * observed)
    mask = np.zeros((ny, nx), np.uint8)
    mask[lake] = 1
    mask[:6, :] = 2

    f = MiniFile()
    sci = f.group("science").group("LSAR")
    ident = sci.group("identification")
    ident.dataset("orbitPassDirection", np.array(b"Descending"))
    ident.dataset("trackNumber", np.array(13, np.int64))
    ident.dataset("frameNumber", np.array(71, np.int64))
    ident.dataset("lookDirection", np.array(b"Right"))
    ident.dataset("productType", np.array(b"GUNW"))
    gunw = sci.group("GUNW")
    par = gunw.group("metadata").group("processingInformation").group("parameters")
    par.group("unwrapping").dataset("algorithm", np.array(b"snaphu (synthetic)"))
    par.group("interferogram").dataset("numberOfRangeLooks", np.array(4, np.int64))
    par.group("interferogram").dataset("numberOfAzimuthLooks", np.array(2, np.int64))
    fa = gunw.group("grids").group("frequencyA")
    fa.dataset("xCoordinates", xs, {"units": "m"})
    fa.dataset("yCoordinates", ys, {"units": "m"})
    fa.dataset("projection", np.array(EPSG, np.int32), {"epsg_code": EPSG})
    fa.dataset("centerFrequency", np.array(1.2575e9))
    fa.dataset("listOfPolarizations", np.array([b"HH"]))
    unwg = fa.group("unwrappedInterferogram").group("HH")
    ck = {"chunks": (128, 128), "compression": "gzip"}
    unwg.dataset("unwrappedPhase", unw.astype("float32"), {"units": "radians", "_FillValue": np.float32("nan")}, **ck)
    unwg.dataset("coherenceMagnitude", coh.astype("float32"), {"units": "1", "_FillValue": np.float32("nan")}, **ck)
    unwg.dataset("connectedComponents", cc, {"_FillValue": np.uint32(65535)}, **ck)
    unwg.dataset("ionospherePhaseScreen", iono.astype("float32"), {"units": "radians", "_FillValue": np.float32("nan")}, **ck)
    fa.group("wrappedInterferogram").group("HH").dataset("wrappedInterferogram", wrapped.astype("complex64"), {"_FillValue": np.complex64("nan")}, **ck)
    fa.group("wrappedInterferogram").group("HH").dataset("coherenceMagnitude", coh.astype("float32"), {"units": "1"}, **ck)
    fa.dataset("mask", mask, {"_FillValue": np.uint8(255)}, **ck)
    rg = gunw.group("metadata").group("radarGrid")                         # no projection of its own, like the real product
    gx, gy = xs[::26][:25], ys[::26][:20]
    rg.dataset("xCoordinates", gx)
    rg.dataset("yCoordinates", gy)
    rg.dataset("heightAboveEllipsoid", np.array([-500.0, 0.0, 1000.0, 3000.0, 6000.0]))
    gxx, gyy = np.meshgrid(np.arange(gx.size), np.arange(gy.size))
    rg.dataset("hydrostaticTroposphericPhaseScreen", (np.arange(5)[:, None, None] * 0.4 + 0.02 * gxx - 0.01 * gyy).astype("float32"), {"units": "radians"})
    rg.dataset("incidenceAngle", (np.arange(5)[:, None, None] * 0 + 33 + 0.2 * gxx + 0 * gyy).astype("float32"), {"units": "degrees"})
    f.truth = {"deformation_max_m": float(np.abs(defo_m).max()), "ramp_rad": 0.9}
    return f


# ----------------------------------------------------------------------------- synthetic search result

def _gid(track, d, frame, a, b, rc, sc):
    ts = lambda day, off: day.strftime("%Y%m%d") + f"T02{40 + off:02d}38"
    return (f"NISAR_L2_PR_GUNW_{rc:03d}_{track:03d}_{d}_{frame:03d}_{sc:03d}_4000_SH_{ts(a, 0)}_{ts(a, 1)[:-2]}16_{ts(b, 0)}_{ts(b, 1)[:-2]}15_P05023_N_F_J_001")


def fake_granules(bbox=DEMO_BBOX):
    """Lane A: 9 epochs, 12-day repeat, baselines 12/24/36 d. Lane B: 4 epochs, 12 d only (cold start)."""
    w, s, e, n = bbox
    out = []

    def lane(track, d, frame, start, epochs, maxk, fp):
        days = [start + timedelta(days=12 * i) for i in range(epochs)]
        for i in range(epochs):
            for k in range(1, maxk + 1):
                if i + k >= epochs:
                    continue
                a, b = days[i], days[i + k]
                gid = _gid(track, d, frame, a, b, 21 + i, 21 + i + k)
                out.append(Granule(
                    id=gid, name=gid + ".h5", url=f"https://nisar.asf.earthdatacloud.nasa.gov/NISAR/NISAR_L2_GUNW_PROVISIONAL_V1/{gid}/{gid}.h5",
                    level="GUNW", t0=a.strftime("%Y-%m-%dT02:40:38Z"), t1=b.strftime("%Y-%m-%dT02:40:38Z"),
                    ref=a.strftime("%Y-%m-%dT02:40:38Z"), sec=b.strftime("%Y-%m-%dT02:40:38Z"), baseline_days=float((b - a).days),
                    size_mb=1700 + 40 * ((i * 7 + k * 3) % 9), dir=d, track=track, frame=frame, ref_cycle=21 + i, sec_cycle=21 + i + k,
                    pol="HH", maturity="PROVISIONAL", footprint=[fp]))

    lane(13, "D", 71, date(2026, 6, 17), 9, 3, [(-118.95, 33.55), (-117.75, 33.75), (-117.95, 34.55), (-119.15, 34.35), (-118.95, 33.55)])
    lane(78, "A", 120, date(2026, 8, 2), 4, 1, [(-118.35, 33.65), (-117.05, 33.55), (-116.95, 34.55), (-118.25, 34.65), (-118.35, 33.65)])
    return out


# ----------------------------------------------------------------------------- clip + fake engine checkout

CLIP_BASE = "science__LSAR__GUNW__grids__frequencyA"


def write_clip(scene: MiniFile, path: str, bbox=DEMO_BBOX):
    """Write an .npz exactly like nisar_region.py / nisar_fast.py do (keys = HDF5 path with / -> __)."""
    fa = scene["/science/LSAR/GUNW/grids/frequencyA"]
    xs, ys = fa["xCoordinates"][()], fa["yCoordinates"][()]
    x0, x1, y0, y1 = 190, 470, 130, 400
    out = {CLIP_BASE + "__xCoordinates": xs[x0:x1], CLIP_BASE + "__yCoordinates": ys[y0:y1], CLIP_BASE + "__epsg": np.array(EPSG)}
    for sub in ("unwrappedInterferogram/HH/unwrappedPhase", "unwrappedInterferogram/HH/coherenceMagnitude",
                "unwrappedInterferogram/HH/connectedComponents", "wrappedInterferogram/HH/wrappedInterferogram", "mask"):
        out[CLIP_BASE + "__" + sub.replace("/", "__")] = fa[sub][()][y0:y1, x0:x1]
    np.savez_compressed(path, bbox=list(bbox), source="synthetic_demo_granule.h5", **out)


PIPELINES = ["earthquake_coseismic", "land_subsidence", "volcanic_deformation", "landslide", "glacier_ice_flow", "permafrost_thaw",
             "flood_inundation", "deforestation", "wildfire_burn_scar", "river_erosion", "coastal_erosion", "urban_damage",
             "tropical_cyclone", "tsunami_inundation", "oil_spill", "iceberg_calving_drift"]


def make_engine_tree(root: str, scene: MiniFile):
    """A fake checkout: a few implemented-looking files, many stubs, one planted independence violation."""
    from .engine_view import COMMON_CHAIN, EXPECTED_DIRS, VERDICT_STEPS
    for d in list(EXPECTED_DIRS) + ["data/clips", "data/ancillary", "data/reference", "data/bundles"]:
        os.makedirs(os.path.join(root, d), exist_ok=True)
    impl = "\n".join(f"def step_{i}(x):\n    y = x * {i + 2}\n    return y + {i}\n" for i in range(8))
    for i, p in enumerate(PIPELINES):
        d = os.path.join(root, "engine", "pipelines", p)
        os.makedirs(os.path.join(d, "tests"), exist_ok=True)
        open(os.path.join(d, "manifest.yaml"), "w").write(
            f"applies: {{sensor: NISAR, tile_min_quality: 0.4}}\nuses: [M1, M2]\nown_prep: []\nown_fetch: [R{i % 14 + 1}]\nmethod: demo method {i}\noutput: [P, status, evidence]\n")
        open(os.path.join(d, "README.md"), "w").write(f"# {p}\n")
        done = p in ("flood_inundation", "deforestation")
        open(os.path.join(d, "model.py"), "w").write((impl if done else "def run(bundle):\n    raise NotImplementedError\n"))
        open(os.path.join(d, "verdict.py"), "w").write("def verdict(x):\n    raise NotImplementedError\n")
        if p != "oil_spill":
            open(os.path.join(d, "prep.py"), "w").write("def prep(b):\n    pass\n")
    open(os.path.join(root, "engine", "pipelines", "land_subsidence", "model.py"), "a").write(
        "\nfrom engine.pipelines.earthquake_coseismic.model import run as _borrowed   # planted violation for the demo\n")
    for i, n in enumerate(COMMON_CHAIN):
        if i < 5 or n == "scene_bundle":
            open(os.path.join(root, "engine", "common", n + ".py"), "w").write(impl if i < 3 else "def f():\n    raise NotImplementedError\n")
    for n in VERDICT_STEPS[:2]:
        open(os.path.join(root, "engine", "verdict", n + ".py"), "w").write("def f():\n    raise NotImplementedError\n")
    open(os.path.join(root, "config", "tiling.yaml"), "w").write("# demo: the real grid scheme is still an open decision\nscheme: undecided\ncandidates: [utm_tiles_100km, h3_res5]\n")
    write_clip(scene, os.path.join(root, "data", "clips", "demo_clip.npz"))
    db = os.path.join(root, "data", "verdicts.db")
    con = sqlite3.connect(db)
    con.execute("create table verdicts (id integer primary key, tile text, epoch text, pipeline text, p real, status text)")
    con.executemany("insert into verdicts (tile, epoch, pipeline, p, status) values (?,?,?,?,?)",
                    [("T11SLT", "2026-07-05", "flood_inundation", 0.12, "NO_EVENT"), ("T11SLT", "2026-07-17", "deforestation", None, "INSUFFICIENT_EVIDENCE"),
                     ("T11SLT", "2026-07-29", "flood_inundation", 0.71, "EVENT")])
    con.execute("create table events (id integer primary key, kind text, tile text, linked_to integer)")
    con.commit()
    con.close()
    return root
