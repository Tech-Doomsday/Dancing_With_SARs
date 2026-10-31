#!/usr/bin/env python3
"""Measure what one tile window costs BEFORE downloading any data chunk (UI-build-3 §6, §8).

    python tools/measure_plan.py --lon 90.40 --lat 23.71 [--lookback 60] [--products GUNW,GCOV] [--max-granules 3]

Needs real credentials in the environment/.env (EARTHDATA_TOKEN, or EARTHDATA_USERNAME + EARTHDATA_PASSWORD) and
network. It only reads each granule's HDF5 METADATA (layout index / chunk tables — a few MB per granule); no data
chunk is fetched. Prints, per granule and layer: chunk shape, chunks touched, compressed MB. Then, for the SAME plan,
the wire bytes the range coalescer would request at gaps of 64 KiB / 256 KiB / 1 MiB (answers "is the 1 MiB
coalesce gap the main source of overhead?"). Never prints credentials.
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lon", type=float, required=True)
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--lookback", type=float, default=60.0)
    ap.add_argument("--products", default="GUNW,GCOV")
    ap.add_argument("--max-granules", type=int, default=0, help="plan only the newest N granules per product (0 = all)")
    a = ap.parse_args()

    from engine.common import p01_granule_resolution as p01
    from engine.common.config import load_common_config
    from engine.common.context import BuildContext, BuildRequest
    from engine.common.grids import make_tile_grids
    from engine.common.layers import request_layers
    from engine.retrieval.clients.nisar_client import NisarClient, bbox_window, plan_chunks
    from engine.retrieval.clients.range_io import coalesce
    from engine.retrieval.settings import load_settings, tile_cell_size_m
    from engine.tiling.mgrs import tile_at

    from engine.api.jobs import earthdata_present
    if not earthdata_present():
        print("NOT RUN: no Earthdata credentials in the environment (EARTHDATA_TOKEN or EARTHDATA_USERNAME + "
              "EARTHDATA_PASSWORD) — this tool reads real granule metadata from NASA.", file=sys.stderr)
        return 2
    cfg = load_common_config()
    tile = tile_at(a.lon, a.lat, size=tile_cell_size_m())
    now = datetime.now(timezone.utc).replace(microsecond=0)
    cl = NisarClient(load_settings())
    try:
        ctx = BuildContext(BuildRequest(tile, now, now - timedelta(days=a.lookback), now), cfg,
                           make_tile_grids(tile, cfg.grids), cl)
        p01.run(ctx)
        bbox = tuple(tile.bbox(cfg.clip.margin_m if cfg.clip.margin_m is not None else cl.cfg.tiling["margin_m"]))
        names = {"GUNW": cfg.products.gunw, "GCOV": cfg.products.gcov}
        want = [p.strip().upper() for p in a.products.split(",")]
        total = 0
        per_gap = {65536: 0, 262144: 0, 1048576: 0}
        useful = 0
        print(f"tile {tile.id}  bbox {bbox}  window {a.lookback:g} d")
        for prod, refs in (ctx.resolved.to_fetch.items() if ctx.resolved else []):
            if prod not in want:
                continue
            layers = request_layers(names[prod], None, cfg.products.frequency, tuple(cfg.products.pols),
                                    cfg.products.use_noise_floor)
            refs = list(refs)[-a.max_granules:] if a.max_granules else list(refs)
            pat = re.compile(layers)
            print(f"\n== {prod}: {len(refs)} granule(s) ==")
            for g in refs:
                ix = cl.io.lt.run(cl._index(g, pat))
                g_total = 0
                print(f"-- {g.granule_id}")
                ranges = []
                for _, gl in ix.grids.items():
                    win = bbox_window(gl, bbox)
                    if win is None:
                        continue
                    for p in gl.layers:
                        if not pat.search(p):
                            continue
                        L = ix.layers[p]
                        if not L.fast:
                            print(f"   {L.key.split('__')[-1]:28} not planned (h5py fallback)")
                            continue
                        plan = [ci for ci, _ in plan_chunks(L, *win) if ci >= 0]
                        b = sum(int(L.sizes[ci]) for ci in plan)
                        g_total += b
                        ranges += [(int(L.offsets[ci]), int(L.sizes[ci])) for ci in plan]
                        print(f"   {L.key.split('__')[-2] + '/' + L.key.split('__')[-1]:40} chunks {tuple(L.chunks)!s:18} "
                              f"touched {len(plan):4d}  {b / 1e6:9.2f} MB   dtype {L.dtype}")
                total += g_total
                useful += g_total
                for gap in per_gap:
                    per_gap[gap] += sum(e - s for s, e, _ in coalesce(ranges, gap, cl.cfg.net["max_request_bytes"]))
                print(f"   granule total {g_total / 1e6:.1f} MB")
        print(f"\nPLAN TOTAL (compressed, useful): {total / 1e6:.1f} MB")
        for gap, wire in per_gap.items():
            ov = (wire / useful - 1) * 100 if useful else 0
            print(f"  coalesce gap {gap // 1024:5d} KiB -> wire {wire / 1e6:9.1f} MB  (+{ov:.0f}% over useful)")
        m = cl.io.metrics.snapshot()
        print(f"metadata cost of planning: {m['bytes_fetched'] / 1e6:.1f} MB on wire in {m['requests']} requests")
    finally:
        cl.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
