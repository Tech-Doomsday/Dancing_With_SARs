#!/usr/bin/env python3
"""Run this on a REAL NISAR granule to see its layout and what the engine achieves on it.

    python tools/probe_granule.py --url https://.../NISAR_L2_..._GUNW_....h5 --layers "unwrappedPhase|coherence"
    python tools/probe_granule.py --bbox -118.5 33.9 -118.0 34.2 --product GUNW     # searches ASF, takes the newest

Use path-qualified layer regexes so you do not also pull the 20 m wrapped grid, e.g.
    --layers "unwrappedInterferogram/HH/(unwrappedPhase|coherenceMagnitude|connectedComponents)"

--index-experiment   times the layout-index strategies on this granule (nothing cached) and exits.
--clip-bbox W S E N  box used for the clip timings (default: 500x500 px around the grid centre, NOT the
                     search box, because the search box may only graze the granule's valid data).

Credentials come from .env (EARTHDATA_TOKEN or EARTHDATA_USERNAME/PASSWORD). Prints: is the file paged,
per-layer chunk shape / filters / compression ratio / whether the fast path supports it, the cost of the
one-time layout index, then cold / repeat / shifted-box clip timings with request and byte counts.
"""
import argparse, re, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from engine.retrieval.clients.h5_index import GranuleIndex
from engine.retrieval.clients.nisar_client import NisarClient
from engine.retrieval.clients.types import GranuleRef


def center_box(ix: GranuleIndex, half_px=250):
    from pyproj import Transformer
    g = next(iter(ix.grids.values()))
    tr = Transformer.from_crs(g.epsg, 4326, always_xy=True)
    cx, cy = g.x[len(g.x) // 2], g.y[len(g.y) // 2]
    dx = abs(float(g.x[1] - g.x[0])) * half_px
    lon, lat = tr.transform([cx - dx, cx + dx, cx - dx, cx + dx], [cy - dx, cy - dx, cy + dx, cy + dx])
    return (min(lon), min(lat), max(lon), max(lat))


def valid_frac(arr):
    if arr.dtype.kind == "f":
        return float(np.mean(np.isfinite(arr) & (arr != 0)))
    if arr.dtype.kind in "iu":
        return float(np.mean(arr != 0))
    return None


def verify(c, g, ix, r):
    """Ground truth check: engine output vs h5py reading the same pixel window of the same layers."""
    h5 = c._fallback_file(g)
    allok = True
    for gc in r.clips:
        for gr in gc.grids.values():
            gl = next(v for v in ix.grids.values() if v.key == gr.grid)
            x0 = int(np.nonzero(gl.x == gr.x[0])[0][0])
            y0 = int(np.nonzero(gl.y == gr.y[0])[0][0])
            for lk, arr in gr.layers.items():
                L = next(v for v in ix.layers.values() if v.key == lk)
                with c._fb_lock:
                    d = h5[L.path]
                    ref = d[(slice(None),) * (d.ndim - 2) + (slice(y0, y0 + arr.shape[-2]), slice(x0, x0 + arr.shape[-1]))]
                ok = (arr.shape == ref.shape and arr.dtype == ref.dtype and
                      (np.array_equal(arr, ref, equal_nan=True) if arr.dtype.kind in "fc" else np.array_equal(arr, ref)))
                allok &= ok
                print(f"      verify {'OK  ' if ok else 'FAIL'} {lk.split('__')[-1]:24} {arr.shape} {arr.dtype}")
    print(f"      => {'ALL LAYERS IDENTICAL TO h5py' if allok else 'MISMATCH - do not trust the fast path on this product'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url"); ap.add_argument("--bbox", type=float, nargs=4)
    ap.add_argument("--product", default="GUNW"); ap.add_argument("--start"); ap.add_argument("--end")
    ap.add_argument("--layers", default=None, help="regex on HDF5 dataset path")
    ap.add_argument("--no-bandwidth", action="store_true", help="skip the parallel-download scaling test")
    ap.add_argument("--verify", action="store_true",
                    help="after the cold clip, re-read the same windows with plain h5py and compare every value")
    ap.add_argument("--clip-bbox", type=float, nargs=4, metavar=("W", "S", "E", "N"))
    ap.add_argument("--index-experiment", action="store_true", help="compare layout-index strategies, then exit")
    ap.add_argument("--include-old", action="store_true", help="with --index-experiment: also time the old 4 MiB page-buffer walk (~90 s on a slow link)")
    a = ap.parse_args()

    c = NisarClient()
    if not c.auth.available:
        sys.exit("no Earthdata credentials: set EARTHDATA_TOKEN (or USERNAME/PASSWORD) in .env")
    if a.url:
        gid = a.url.rsplit("/", 1)[-1].removesuffix(".h5")
        g = GranuleRef(gid, a.url, a.product)
    elif a.bbox:
        refs, _ = c.search(tuple(a.bbox), a.start, a.end, a.product)
        if not refs:
            sys.exit("search returned nothing")
        g = sorted(refs, key=lambda r: r.start or "")[-1]
        print("granule:", g.granule_id)
    else:
        sys.exit("give --url or --bbox")

    m = c.io.metrics
    pat = re.compile(a.layers) if a.layers else None
    if a.index_experiment:
        print(f"\nlayout-index strategies on {g.granule_id[:60]}...  (layers={a.layers or '*'})")
        print(f"  {'strategy':48} {'time':>8} {'requests':>14} {'wire':>11}")
        c.index_experiment(g, pat, include_old=a.include_old)
        print(f"\ncatalog saved to {c.cfg.catalog_dirs[1] / (g.product + '.json')}\n"
              f"copy it to config/catalog/{g.product}.json and commit it, so every machine skips the full walk.")
        c.close()
        return
    t = time.perf_counter()
    ix = c.io.lt.run(c._index(g, pat))
    k = c.counters
    print(f"\nlayout index: {time.perf_counter() - t:.1f}s total (skeleton {k['index_skeleton_s']:.1f}s, "
          f"chunk tables {k['index_tables_s']:.1f}s for {k['index_tables_layers']} layer(s)), "
          f"{m.requests} requests, {m.bytes_fetched / 1e6:.1f} MB   [file is {ix.size / 1e9:.2f} GB; one-time per granule]")

    with c._fb_lock:
        fcpl = c._fallback_file(g).id.get_create_plist()
        strat = fcpl.get_file_space_strategy()[0]
        try:
            page = fcpl.get_file_space_page_size()
        except Exception:
            page = None
    print(f"file space strategy: {'PAGED' if strat == 1 else f'not paged (code {strat})'}"
          f"{f', page size {page / 1024:.0f} KiB' if page else ''}")

    print(f"\n{'layer (tail of path)':50} {'shape':>16} {'chunks':>14} {'filters':>9} {'n':>5} {'MB':>7} {'ratio':>6}  path")
    for L in ix.layers.values():
        if pat and not pat.search(L.path):
            continue
        raw = float(np.prod(L.shape)) * L.dtype.itemsize / 1e6
        flt = "+".join({1: "gz", 2: "shuf", 3: "f32"}.get(f[0], f"id{f[0]}") for f in L.filters) or "-"
        if L.loaded:
            stored = int(L.sizes.sum()) / 1e6
            nums = f"{len(L.offsets):5d} {stored:7.1f} {raw / max(stored, 1e-9):6.1f}"
        else:
            nums = f"{'-':>5} {'-':>7} {'-':>6}"
        print(f"{L.path[-50:]:50} {str(L.shape):>16} {str(L.chunks):>14} {flt:>9} {nums}  "
              f"{'FAST' if L.fast else 'fallback: ' + L.reason}")
    print(f"({len(ix.layers)} layers in this index; only matching ones listed)")

    if not a.no_bandwidth:
        print("\nparallel download scaling (1 MB ranges spread over the file):")
        n_sz, step = 1 << 20, max(ix.size // 40, n_sz := 1 << 20)
        for conc, count in ((1, 4), (4, 8), (16, 16)):
            offs = [(i * step + (1 << 20), n_sz) for i in range(count)]
            t = time.perf_counter()
            for i in range(0, count, conc):
                c.io.lt.run(c.io.read_ranges(g.url, offs[i:i + conc]))
            dt = time.perf_counter() - t
            print(f"  {conc:2d} in flight: {count * n_sz / 1e6 / dt:6.2f} MB/s   ({dt:.1f}s for {count} MB)")

    box = tuple(a.clip_bbox) if a.clip_bbox else center_box(ix)
    shifted = (box[0] + (box[2] - box[0]) * .3, box[1] + (box[3] - box[1]) * .3,
               box[2] + (box[2] - box[0]) * .3, box[3] + (box[3] - box[1]) * .3)
    print(f"\nclip box {tuple(round(v, 4) for v in box)}   layers={a.layers or '*'}")
    for label, bb in (("cold", box), ("repeat (clip cache)", box), ("shifted 30% (chunk reuse)", shifted)):
        before = m.snapshot()
        t = time.perf_counter()
        r = c.fetch(bb, layers=a.layers, granules=[g])
        dt = time.perf_counter() - t
        d = {k: m.snapshot()[k] - before[k] for k in before}
        n = sum(arr.nbytes for gc in r.clips for gr in gc.grids.values() for arr in gr.layers.values()) / 1e6
        print(f"  {label:28} {dt:6.2f}s  {d['requests']:4d} requests  {d['bytes_fetched'] / 1e6:8.1f} MB wire  "
              f"{n:8.1f} MB result  errors={r.errors or 'none'}")
        if label == "cold" and a.verify:
            verify(c, g, ix, r)
        if label == "cold":
            for gc in r.clips:
                for gr in gc.grids.values():
                    for k_, arr in gr.layers.items():
                        v = valid_frac(arr)
                        if v is not None:
                            print(f"      valid data {100 * v:5.1f}%  {arr.shape}  {k_.split('__')[-1]}")
                            if v < 0.05:
                                print("      ^ almost empty: this box has little real data in this granule, so timings are optimistic")
    print("\ncounters:", c.counters)
    c.close()


if __name__ == "__main__":
    main()
