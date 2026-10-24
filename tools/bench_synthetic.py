#!/usr/bin/env python3
"""Old approach (prototype: h5py + fsspec blockcache, one file open per clip) vs the new engine,
on a synthetic granule served with injected latency. Reproducible, no network needed.

    python tools/bench_synthetic.py --latency-ms 80 --tiles 12
"""
import argparse, re, sys, tempfile, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

import fsspec, h5py, numpy as np
from pyproj import Transformer

from conftest import make_client
from engine.retrieval.clients.types import GranuleRef
from synth import DX, EPSG, X0, Y0, RangeServer, lonlat_box, make_granule

LAYERS = r"unwrappedPhase|coherenceMagnitude|connectedComponents"


def baseline_clip(url, bbox, layer_re):
    """Faithful to nisar_fast.clip_h5: open over fsspec blockcache, walk the tree, read every match."""
    pat = re.compile(layer_re)
    w, s, e, n = bbox
    fs = fsspec.filesystem("http")
    with fs.open(url, "rb", cache_type="blockcache", block_size=8 << 20) as fobj:
        with h5py.File(fobj, "r", page_buf_size=16 << 20, rdcc_nbytes=4 << 20) as h5:
            grids = []
            h5.visititems(lambda nm, o: grids.append(o) if isinstance(o, h5py.Group) and "xCoordinates" in o else None)
            out = {}
            for g in grids:
                x, y = g["xCoordinates"][:], g["yCoordinates"][:]
                tr = Transformer.from_crs(4326, EPSG, always_xy=True)
                px, py = tr.transform([w, e, w, e], [s, s, n, n])
                xi = np.nonzero((x >= min(px) - DX) & (x <= max(px) + DX))[0]
                yi = np.nonzero((y >= min(py) - DX) & (y <= max(py) + DX))[0]
                if not len(xi) or not len(yi):
                    continue
                ys, xs = slice(yi[0], yi[-1] + 1), slice(xi[0], xi[-1] + 1)
                lay = []
                g.visititems(lambda nm, o: lay.append(o) if isinstance(o, h5py.Dataset) and o.ndim == 2
                             and o.shape == (len(y), len(x)) and pat.search(o.name) else None)
                for d in lay:
                    out[d.name] = d[ys, xs]
            return out


def tiles(n, ny, nx, size=400):
    rng = np.random.default_rng(1)
    return [lonlat_box(int(a), int(b), int(a) + size, int(b) + size)
            for a, b in zip(rng.integers(0, nx - size, n), rng.integers(0, ny - size, n))]


def timed(f):
    t = time.perf_counter(); r = f(); return r, time.perf_counter() - t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--latency-ms", type=float, default=80)
    ap.add_argument("--mbps", type=float, default=200, help="per-connection bandwidth cap")
    ap.add_argument("--tiles", type=int, default=12)
    ap.add_argument("--size", type=int, default=3000)
    a = ap.parse_args()

    tmp = Path(tempfile.mkdtemp())
    name = "NISAR_L2_PR_GUNW_A_001_000_000_000_000_000_000_20260701T000000_20260701T000030_X05010_N_F_J_001.h5"
    print(f"building {a.size}x{a.size} synthetic granule ...", flush=True)
    make_granule(tmp / name, ny=a.size, nx=a.size)
    print(f"file: {(tmp / name).stat().st_size / 1e6:.0f} MB")
    srv = RangeServer(tmp, latency_s=a.latency_ms / 1000, bandwidth_mbps=a.mbps)
    signed = f"http://localhost:{srv.port}/signed/{name}?sig={srv.sig}"
    g = GranuleRef(name[:-3], srv.url(name), "GUNW")
    boxes = tiles(a.tiles, a.size, a.size)
    rows = []

    # ---- baseline: every tile is an independent cold clip
    srv.reset(); t0 = time.perf_counter()
    per = []
    for b in boxes:
        _, dt = timed(lambda: baseline_clip(signed, b, LAYERS)); per.append(dt)
    rows.append(("prototype (h5py+fsspec)", time.perf_counter() - t0, per[0], srv.requests, srv.bytes_sent))

    # ---- new engine, fresh caches
    c = make_client(tmp / "run")
    srv.reset(); per = []; t0 = time.perf_counter()
    for b in boxes:
        r, dt = timed(lambda: c.fetch(b, layers=LAYERS, granules=[g])); per.append(dt)
        assert not r.errors, r.errors
    rows.append(("engine, cold start", time.perf_counter() - t0, per[0], srv.requests, srv.bytes_sent))
    first_cold, later = per[0], np.median(per[1:])

    # ---- same tiles again: clip cache
    srv.reset(); t0 = time.perf_counter()
    for b in boxes:
        c.fetch(b, layers=LAYERS, granules=[g])
    rows.append(("engine, repeat tiles (clip cache)", time.perf_counter() - t0, None, srv.requests, srv.bytes_sent))
    c.close()

    # ---- new process, same disk: index + chunk cache, RAM cold, tiles shifted by 100 px
    c2 = make_client(tmp / "run")
    shifted = tiles(a.tiles, a.size, a.size, size=400)
    srv.reset(); t0 = time.perf_counter()
    for b in boxes:
        w, s, e, n = b
        c2.fetch((w + 0.004, s + 0.004, e + 0.004, n + 0.004), layers=LAYERS, granules=[g])
    rows.append(("engine, restart + shifted tiles", time.perf_counter() - t0, None, srv.requests, srv.bytes_sent))
    c2.close()

    print(f"\n{a.tiles} tiles of 400x400 px, latency {a.latency_ms:.0f} ms, {a.mbps:.0f} Mbps/connection\n")
    print(f"{'scenario':38} {'total s':>8} {'1st tile s':>10} {'requests':>9} {'MB on wire':>11}")
    for n_, t, f, rq, by in rows:
        print(f"{n_:38} {t:8.1f} {'' if f is None else f'{f:10.1f}'} {rq:9d} {by / 1e6:11.1f}")
    print(f"\nengine cold: first tile {first_cold:.1f}s (builds the layout index), later tiles median {later:.2f}s")
    srv.loop.call_soon_threadsafe(srv.loop.stop)


if __name__ == "__main__":
    main()
