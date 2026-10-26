<<<<<<< HEAD
"""python -m engine.common.debug --sample ../dancing-with-sars-eq/data/derived/nisar-la-2008 --out bundle_debug.html

Runs P01..P13 on a REAL extracted GUNW and writes a self-contained HTML report of everything that was loaded.
No network, no credentials, nothing under engine/retrieval is touched.
=======
"""python -m engine.common.debug --sample app/data/derived/nisar-la-2008 --out bundle_debug.html

Runs P01..P13 on a REAL extracted GUNW and writes a self-contained HTML report of everything that was loaded.
No network, no credentials, nothing under engine/retrieval is touched.

The default sample is the one committed with the integrated web application
(app/data/derived/nisar-la-2008, extracted from the real 2008 NISAR-format
sample product by app/tools/extract_gunw.py).
>>>>>>> 8200fa0 (Integrate MOTUS earthquake analysis into Dancing With SARs)
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from engine.common.debug.compare import reality_check
from engine.common.debug.eq_sample import EqSampleRetriever
from engine.common.debug.report import write_report
from engine.common.debug.trace import traced_build
from engine.tiling.mgrs import tile_at

<<<<<<< HEAD
DEFAULT = Path(__file__).resolve().parents[3].parent / "dancing-with-sars-eq" / "data" / "derived" / "nisar-la-2008"
=======
# The integrated web application ships the extracted 2008 sample product
# inside this repository (app/data/derived/nisar-la-2008); EQ_SAMPLE_DIR can
# still point anywhere else.
DEFAULT = Path(__file__).resolve().parents[3] / "app" / "data" / "derived" / "nisar-la-2008"
>>>>>>> 8200fa0 (Integrate MOTUS earthquake analysis into Dancing With SARs)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sample", default=os.environ.get("EQ_SAMPLE_DIR", str(DEFAULT)), help="folder with meta.json + phase.f32 ...")
    ap.add_argument("--out", default="bundle_debug.html")
    ap.add_argument("--mode", choices=("best", "mixed", "worst"), default="mixed",
                    help="which 10 km tile: most valid phase / ~60 %% valid (default; exercises the P03 gate) / least valid")
    ap.add_argument("--lonlat", nargs=2, type=float, metavar=("LON", "LAT"), help="use the tile containing this point instead")
    ap.add_argument("--list-tiles", action="store_true", help="print every usable tile with its valid-phase fraction and exit")
    a = ap.parse_args(argv)
    if not (Path(a.sample) / "meta.json").is_file():
<<<<<<< HEAD
        print(f"no meta.json in {a.sample}: pass --sample <dancing-with-sars-eq>/data/derived/<slug>", file=sys.stderr)
=======
        print(f"no meta.json in {a.sample}: pass --sample app/data/derived/<slug>", file=sys.stderr)
>>>>>>> 8200fa0 (Integrate MOTUS earthquake analysis into Dancing With SARs)
        return 2
    r = EqSampleRetriever(a.sample)
    if a.list_tiles:
        for t, v in sorted(r.tile_candidates(), key=lambda x: -x[1]):
            print(f"{t.id}  valid {100 * v:5.1f} %  E{t.e0:.0f} N{t.n0:.0f}")
        return 0
    tile = tile_at(*a.lonlat) if a.lonlat else r.best_tile(mode=a.mode)
    trace = traced_build(tile, r, as_of=r.as_of, cfg=r.config())
    b = trace.bundle
    print(f"tile {tile.id}  status {b.status}  as_of {r.as_of:%Y-%m-%d}")
    for s in trace.steps:
        print(f"  {s.code} {s.name:20s} {s.status:8s} {s.seconds:7.3f}s  +{len(s.arrays):2d} arrays  {s.message}")
    for f in b.flags:
        print(f"  [{f.severity:5s}] {f.step} {f.code}: {f.message[:110]}")
    rows = reality_check(r, b)
    p = write_report(a.out, trace, retriever=r, reality=rows, limitations=r.limitations,
                     title=f"SceneBundle debug · {r.meta.get('title', r.granule_id)[:80]}")
    print(f"wrote {p} ({p.stat().st_size / 1e6:.2f} MB)")
    return 0 if not trace.tracer_errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
