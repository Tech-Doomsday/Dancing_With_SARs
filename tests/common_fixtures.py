"""Synthetic NISAR-shaped inputs for engine/common tests: no network, no HDF5, known ground truth.

`FakeRetriever` implements the two retriever methods engine/common uses (`search`, `fetch_tile`) and returns the
same dataclasses NisarClient does (GranuleRef / GranuleClip / GridClip / FetchResult) with the real flat layer
keys and the real catalog layout (one grid per GUNW section, a coarse radarGrid cube with a height axis, 20 m GCOV).
It honours the `layers` regex, so P02's request is tested too.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import numpy as np

from engine.retrieval.clients.types import FetchResult, GranuleClip, GranuleRef, GridClip
from engine.tiling.mgrs import tile_at

LAMBDA = 0.2384
HEIGHTS = np.array([-500.0, 1000.0, 3000.0])


def default_tile():
    return tile_at(-118.5, 34.0)           # 11SLT66, UTM zone 11


def dt(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def stamp(d: datetime, extra_s: int = 0) -> str:
    return (d + timedelta(seconds=extra_s)).strftime("%Y%m%dT%H%M%S")


def gunw_id(ref: datetime, sec: datetime, track=5, direction="A", frame=219, version=5010, cycle=1) -> str:
    return (f"NISAR_L2_PR_GUNW_{cycle:03d}_{track:03d}_{direction}_{frame:03d}_4005_SHNA_{stamp(ref)}_{stamp(ref, 30)}_"
            f"{stamp(sec)}_{stamp(sec, 30)}_X{version:05d}_N_F_J_001")


def gcov_id(t: datetime, track=5, direction="A", frame=219, version=5010, cycle=1) -> str:
    return (f"NISAR_L2_PR_GCOV_{cycle:03d}_{track:03d}_{direction}_{frame:03d}_4005_SHNA_A_{stamp(t)}_{stamp(t, 30)}_"
            f"X{version:05d}_N_F_J_001")


# ---------------------------------------------------------------------------------------------
@dataclass
class GeomSpec:
    incidence_deg: float = 40.0
    heading_deg: float = -10.0      # flight direction, clockwise from grid north
    look: str = "left"              # side the radar looks to
    los_points: str = "ground_to_sensor"
    incidence_gradient_deg_per_km: float = 0.0   # varies along the east axis, like near->far range

    def az_ground_to_sensor(self) -> float:
        return (self.heading_deg + (90.0 if self.look == "left" else -90.0)) % 360.0


@dataclass
class GranuleSpec:
    ref: GranuleRef
    kind: str                        # GUNW | GCOV
    ref_time: datetime | None = None
    sec_time: datetime | None = None
    time: datetime | None = None
    geom: GeomSpec = field(default_factory=GeomSpec)
    dlos: float = 0.0                # truth: peak toward-sensor displacement (m) of the bowl
    amp_db: float = 0.0              # GCOV: change (dB) applied to the "event" patch (forest-loss like)
    crs: str = "tile"                # tile | neighbor (UTM zone + 1)
    frame_half: str | None = None    # None | "north" | "south"
    fills: dict = field(default_factory=dict)
    drop_layers: tuple = ()
    coherence: float = 0.8
    seed: int = 0
    sign_flip: bool = False          # pair stored sec-before-ref (the id already encodes the order)


def bowl(x, y, cx, cy, sigma):
    return np.exp(-(((x - cx) ** 2 + (y - cy) ** 2) / (2 * sigma ** 2)))


def los_truth(tile, x, y, peak):
    """Truth toward-sensor displacement field (metres) in tile UTM coordinates."""
    return (peak * bowl(x, y, tile.e0 + 4000, tile.n0 + 5500, 1500.0)).astype(np.float32)


def event_patch(tile, x, y):
    return ((x > tile.e0 + 6000) & (x < tile.e0 + 8000) & (y > tile.n0 + 2000) & (y < tile.n0 + 4000))


def _axes(tile, epsg_target: int, dx: float, margin: float = 700.0):
    """Native pixel-centre axes covering the tile + margin, edge-aligned to multiples of dx (like NISAR)."""
    from pyproj import Transformer
    b = tile.bbox(0.0)
    if epsg_target == tile.epsg:
        x0, x1, y0, y1 = tile.e0 - margin, tile.e0 + tile.size + margin, tile.n0 - margin, tile.n0 + tile.size + margin
    else:
        tr = Transformer.from_crs(4326, epsg_target, always_xy=True)
        xs, ys = tr.transform([b[0], b[2], b[0], b[2]], [b[1], b[1], b[3], b[3]])
        x0, x1, y0, y1 = min(xs) - margin, max(xs) + margin, min(ys) - margin, max(ys) + margin
    ex0, ey1 = math.floor(x0 / dx) * dx, math.ceil(y1 / dx) * dx
    nx, ny = int(math.ceil((x1 - ex0) / dx)), int(math.ceil((ey1 - y0) / dx))
    x = ex0 + dx * (np.arange(nx) + 0.5)
    y = ey1 - dx * (np.arange(ny) + 0.5)
    return x, y


def _tile_xy(tile, epsg, x, y):
    """Tile-CRS coordinates of every native pixel centre (so truth fields are defined once, in tile coords)."""
    X, Y = np.meshgrid(x, y)
    if epsg == tile.epsg:
        return X, Y
    from pyproj import Transformer
    tx, ty = Transformer.from_crs(epsg, tile.epsg, always_xy=True).transform(X, Y)
    return np.asarray(tx), np.asarray(ty)


def _geometry_cubes(tile, g: GeomSpec, x, y, epsg):
    """radarGrid-like cubes (H, ny, nx), mutually consistent: los vector <-> incidence <-> along-track."""
    X, Y = _tile_xy(tile, epsg, x, y)
    az = math.radians(g.az_ground_to_sensor())
    inc0 = g.incidence_deg + g.incidence_gradient_deg_per_km * (X - tile.e0 - 5000) / 1000.0
    inc = np.stack([inc0 + 0.0005 * h for h in HEIGHTS]).astype(np.float32)
    s, c = np.sin(np.radians(inc)), np.cos(np.radians(inc))
    gx, gy = s * math.sin(az), s * math.cos(az)                # ground -> sensor
    if g.los_points == "sensor_to_ground":
        gx, gy = -gx, -gy
    hd = math.radians(g.heading_deg)
    ax = np.full_like(inc, math.sin(hd))
    ay = np.full_like(inc, math.cos(hd))
    return {"incidenceAngle": inc, "losUnitVectorX": gx.astype(np.float32), "losUnitVectorY": gy.astype(np.float32),
            "alongTrackUnitVectorX": ax, "alongTrackUnitVectorY": ay,
            "elevationAngle": (inc - 3.0).astype(np.float32),
            "perpendicularBaseline": np.full_like(inc, 120.0), "parallelBaseline": np.full_like(inc, 5.0)}


def _speckle(rng, shape, looks):
    return rng.gamma(looks, 1.0 / looks, size=shape).astype(np.float32)


def build_clip(tile, spec: GranuleSpec, layers_re: str | None, margin: float = 700.0) -> GranuleClip:
    rng = np.random.default_rng(spec.seed)
    pat = re.compile(layers_re) if layers_re else None
    epsg = tile.epsg if spec.crs == "tile" else tile.epsg + 1
    prod = spec.kind
    grids: dict[str, GridClip] = {}

    def add(grid_key, x, y, layers: dict, extras=None, path_prefix=""):
        out = {}
        for name, arr in layers.items():
            path = f"/{grid_key.replace('__', '/')}/{name}" if path_prefix == "" else f"/{path_prefix}/{name}"
            if name in spec.drop_layers:
                continue
            if pat is None or pat.search(path):
                out[path.strip("/").replace("/", "__")] = arr
        if out:
            grids[grid_key] = GridClip(grid_key, epsg, x, y, extras or {}, out)

    def crop_frame(x, y):
        if spec.frame_half is None:
            return x, y
        ny = len(y)
        h = ny // 2
        return (x, y[:h + 12]) if spec.frame_half == "north" else (x, y[h - 12:])

    # coarse radarGrid
    rx, ry = _axes(tile, epsg, 2000.0, margin + 2000)
    rx, ry = crop_frame(rx, ry)
    cubes = _geometry_cubes(tile, spec.geom, rx, ry, epsg)
    base = f"science__LSAR__{prod}__metadata__radarGrid"

    if prod == "GUNW":
        dx = 80.0
        x, y = _axes(tile, epsg, dx, margin)
        x, y = crop_frame(x, y)
        X, Y = _tile_xy(tile, epsg, x, y)
        truth = los_truth(tile, X, Y, spec.dlos)
        sign = -1.0 if spec.sign_flip else 1.0
        phase = (sign * truth * (-4 * math.pi / LAMBDA)).astype(np.float32)      # inverse of phase_sign=-1
        phase += (0.02 * rng.standard_normal(phase.shape)).astype(np.float32)
        coh = np.clip(spec.coherence + 0.05 * rng.standard_normal(phase.shape), 0.05, 0.99).astype(np.float32)
        cc = np.ones(phase.shape, np.uint16)
        bad = (X > tile.e0 + 1000) & (X < tile.e0 + 2000) & (Y > tile.n0 + 7000) & (Y < tile.n0 + 8500)
        cc[bad] = 0
        phase[bad] = rng.uniform(-30, 30, bad.sum()).astype(np.float32)       # garbage the gate must remove
        coh[bad] = 0.1
        iono_sigma = np.full(phase.shape, 0.05, np.float32)
        iono = (0.3 * np.sin(X / 3000.0)).astype(np.float32)
        sec = f"science__LSAR__GUNW__grids__frequencyA__unwrappedInterferogram"
        lay = {"HH/unwrappedPhase": phase, "HH/coherenceMagnitude": coh, "HH/connectedComponents": cc,
               "HH/ionospherePhaseScreen": iono, "HH/ionospherePhaseScreenUncertainty": iono_sigma,
               "mask": np.zeros(phase.shape, np.uint8)}
        for k, v in spec.fills.items():
            if k in lay:
                lay[k] = v(lay[k])
        glayers = {}
        for name, arr in lay.items():
            path = f"/{sec.replace('__', '/')}/{name}"
            if name.split("/")[-1] in spec.drop_layers:
                continue
            if pat is None or pat.search(path):
                glayers[path.strip("/").replace("/", "__")] = arr
        if glayers:
            grids[sec] = GridClip(sec, epsg, x, y, {}, glayers)
        # pixel offsets
        off = {"HH/slantRangeOffset": (truth * 0.5).astype(np.float32),
               "HH/alongTrackOffset": np.zeros_like(truth), "HH/correlationSurfacePeak": np.full_like(truth, 0.7),
               "mask": np.zeros(truth.shape, np.uint8)}
        ok = f"science__LSAR__GUNW__grids__frequencyA__pixelOffsets"
        ol = {}
        for name, arr in off.items():
            path = f"/{ok.replace('__', '/')}/{name}"
            if pat is None or pat.search(path):
                ol[path.strip("/").replace("/", "__")] = arr
        if ol:
            grids[ok] = GridClip(ok, epsg, x, y, {}, ol)
        # correction screens (H, y, x) in the radarGrid
        Xr, Yr = _tile_xy(tile, epsg, rx, ry)
        ramp = ((Xr - tile.e0) / 10000.0).astype(np.float32)
        for nm, amp in (("hydrostaticTroposphericPhaseScreen", 1.0), ("wetTroposphericPhaseScreen", 0.4),
                        ("slantRangeSolidEarthTidesPhase", 0.2)):
            cubes[nm] = np.stack([amp * ramp * (1 + 0.1 * i) for i in range(len(HEIGHTS))]).astype(np.float32)
    else:
        dx = 20.0
        x, y = _axes(tile, epsg, dx, margin)
        x, y = crop_frame(x, y)
        X, Y = _tile_xy(tile, epsg, x, y)
        ev = event_patch(tile, X, Y)
        forest = (X < tile.e0 + 5000)
        hh_db = np.where(forest, -9.0, -13.0)
        hv_db = np.where(forest, -15.0, -22.0)
        hh = 10 ** (hh_db / 10) * _speckle(rng, X.shape, 25)
        hv = 10 ** (hv_db / 10) * _speckle(rng, X.shape, 25)
        if spec.amp_db:
            hv = np.where(ev, hv * 10 ** (spec.amp_db / 10), hv)
            hh = np.where(ev, hh * 10 ** (0.4 * spec.amp_db / 10), hh)
        cov = {"HHHH": hh.astype(np.float32), "HVHV": hv.astype(np.float32),
               "mask": np.zeros(X.shape, np.uint8), "numberOfLooks": np.full(X.shape, 25.0, np.float32),
               "rtcGammaToSigmaFactor": np.full(X.shape, 0.7, np.float32)}
        gk = "science__LSAR__GCOV__grids__frequencyA"
        gl = {}
        for name, arr in cov.items():
            path = f"/{gk.replace('__', '/')}/{name}"
            if name in spec.drop_layers:
                continue
            if pat is None or pat.search(path):
                gl[path.strip("/").replace("/", "__")] = arr
        if gl:
            grids[gk] = GridClip(gk, epsg, x, y, {}, gl)
        cubes = {k: v for k, v in cubes.items() if k in ("incidenceAngle", "losUnitVectorX", "losUnitVectorY",
                                                          "alongTrackUnitVectorX", "alongTrackUnitVectorY")}

    rl = {}
    for name, arr in cubes.items():
        if name in spec.drop_layers:
            continue
        path = f"/{base.replace('__', '/')}/{name}"
        if pat is None or pat.search(path):
            rl[path.strip("/").replace("/", "__")] = arr
    if rl:
        grids[base] = GridClip(base, epsg, rx, ry, {"heightAboveEllipsoid": HEIGHTS.copy()}, rl)
    return GranuleClip(spec.ref, tuple(tile.bbox(300.0)), grids, "network")


class FakeRetriever:
    """search() / fetch_tile() over a list of GranuleSpecs. Counts calls so tests can assert on P02's requests."""

    class _Cfg:
        tiling = {"margin_m": 300.0}

    cfg = _Cfg()

    def __init__(self, specs: list[GranuleSpec], degraded: bool = False, fail_search: str | None = None,
                 fail_ids: tuple = ()):
        self.specs = {s.ref.granule_id: s for s in specs}
        self.degraded, self.fail_search, self.fail_ids = degraded, fail_search, fail_ids
        self.searches: list = []
        self.fetches: list = []

    def search(self, bbox, t0=None, t1=None, product=None, maturity=None, fresh=False, max_results=None):
        self.searches.append((tuple(bbox), t0, t1, product))
        if self.fail_search == product:
            raise ConnectionError("ASF unreachable")
        refs = [s.ref for s in self.specs.values() if s.ref.product == product]
        return refs, self.degraded

    def fetch_tile(self, tile, t0=None, t1=None, product=None, layers=None, maturity=None, granules=None, margin_m=None):
        self.fetches.append({"product": product, "layers": layers, "granules": [g.granule_id for g in granules or []]})
        res = FetchResult([], {}, {}, degraded=self.degraded)
        for g in granules or []:
            if g.granule_id in self.fail_ids:
                res.errors[g.granule_id] = "RangeError: gave up on bytes 0-1 after retries"
                continue
            clip = build_clip(tile, self.specs[g.granule_id], layers)
            if not clip.grids:
                res.skipped[g.granule_id] = "bbox does not overlap this granule's grids"
            else:
                res.clips.append(clip)
        return res


def ref_for(gid: str, product: str, start: datetime, track=5, direction="ASCENDING", frame=219) -> GranuleRef:
    return GranuleRef(gid, f"https://example.invalid/{gid}.h5", product, start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                      (start + timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%SZ"), track, frame, direction)


def make_scene(tile=None, *, n_epochs=6, event=True, pairs=((0, 1), (1, 2), (2, 3)), start="2026-07-01",
               repeat_days=12, track=5, direction="A", geom: GeomSpec | None = None, crs="tile",
               dlos=-0.05, loss_db=-6.0, split_frames=False, extra: list | None = None) -> list[GranuleSpec]:
    """A believable stack: `n_epochs` GCOV passes 12 days apart (the last one has a forest-loss-like HV drop when
    `event`), and GUNW pairs between the listed epoch indices (the pair that spans the last epoch carries `dlos`)."""
    tile = tile or default_tile()
    geom = geom or GeomSpec()
    t0 = dt(start)
    days = [t0 + timedelta(days=repeat_days * i) for i in range(n_epochs)]
    direction_asf = "ASCENDING" if direction == "A" else "DESCENDING"
    specs: list[GranuleSpec] = []
    for i, d in enumerate(days):
        halves = (["north", "south"] if split_frames else [None])
        for h in halves:
            gid = gcov_id(d + timedelta(seconds=(24 if h == "south" else 0)), track, direction)
            specs.append(GranuleSpec(ref_for(gid, "GCOV", d, track, direction_asf), "GCOV", time=d, geom=geom, crs=crs,
                                     amp_db=loss_db if (event and i == n_epochs - 1) else 0.0, seed=100 + i,
                                     frame_half=h))
    for (a, b) in pairs:
        if b >= n_epochs:
            continue
        gid = gunw_id(days[a], days[b], track, direction)
        specs.append(GranuleSpec(ref_for(gid, "GUNW", days[a], track, direction_asf), "GUNW", ref_time=days[a],
                                 sec_time=days[b], geom=geom, crs=crs,
                                 dlos=dlos if (event and b == n_epochs - 1) else 0.0, seed=7 + a))
    return specs + (extra or [])


def selftest_scene(tile=None) -> list[GranuleSpec]:
    """The scene the debug hub's SELF-TEST source serves (one shared recipe so the hub and its tests
    cannot drift). It is the earthquake acceptance scene (T-8): a +50 mm LOS bowl on the newest GUNW
    pair, so the hub's PIPELINES tab has a detectable, forward-model-consistent signal end to end."""
    return make_scene(tile, n_epochs=4, pairs=((0, 1), (1, 2), (2, 3)), dlos=0.05)


# ---------------------------------------------------------------------------------------------
# ancillary rasters for DirectoryAncillary
# ---------------------------------------------------------------------------------------------
def write_ancillary(root, tile, *, dem=True, landcover=True, context=True, volcano_km: float | None = 30.0,
                    water_frac_north: float = 0.0, extra_facts: dict | None = None):
    """Rasters at 30 m around `tile` (+-6 km) in the tile's UTM CRS.

    DEM    flat in the west half, a 25 deg planar slope rising to the east in the east half (a landslide-like face)
    land   west half tree cover (WorldCover 10), east half grass (30); the northern `water_frac_north` of the tile is water (80)
    other  tree_cover 90 % west / 10 % east, builtup 0, permafrost 0, glacier 0, a N-S river corridor at e0+2 km
    """
    import json
    from pathlib import Path

    from engine.common.ancillary import write_raster_npz
    root = Path(root)
    x = tile.e0 - 6000 + 30.0 * (np.arange(int((tile.size + 12000) / 30)) + 0.5)
    y = tile.n0 + tile.size + 6000 - 30.0 * (np.arange(int((tile.size + 12000) / 30)) + 0.5)
    X, Y = np.meshgrid(x, y)
    east = X - (tile.e0 + 5000)
    if dem:
        z = 200.0 + np.where(east > 0, np.tan(np.radians(25.0)) * east, 0.0) + 0.0 * Y
        write_raster_npz(root / "dem" / "dem.npz", z.astype(np.float32), x, y, tile.epsg, source="synthetic-dem", version="t1")
    if landcover:
        lc = np.where(east > 0, 30, 10).astype(np.uint8)
        if water_frac_north > 0:
            lc[Y > tile.n0 + tile.size * (1 - water_frac_north)] = 80
        write_raster_npz(root / "landcover" / "lc.npz", lc, x, y, tile.epsg, nodata=0, source="synthetic-wc", version="t1")
    if context:
        river = (np.abs(X - (tile.e0 + 2000)) < 60)
        write_raster_npz(root / "water_occurrence" / "wo.npz", np.where(river, 100.0, 0.0).astype(np.float32), x, y, tile.epsg,
                         source="synthetic-gsw")
        write_raster_npz(root / "tree_cover" / "tc.npz", np.where(east > 0, 10.0, 90.0).astype(np.float32), x, y, tile.epsg)
        for nm in ("builtup", "permafrost", "glacier"):
            write_raster_npz(root / nm / f"{nm}.npz", np.zeros(X.shape, np.float32), x, y, tile.epsg)
        write_raster_npz(root / "river_buffer" / "rb.npz", river.astype(np.float32), x, y, tile.epsg)
    facts = dict(extra_facts or {})
    if volcano_km is not None:
        facts["volcano_distance_km"] = volcano_km
    (root / "facts.json").write_text(json.dumps(facts))
    return root
