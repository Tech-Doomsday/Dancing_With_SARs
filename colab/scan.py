#!/usr/bin/env python
"""Run one scan as a plain script (output streams live into a Colab cell):

    !python -u /content/nisar-hazard-engine/colab/scan.py --lon 90.41849 --lat 23.748 --lookback 60

Everything bootstrap() does, then one scan, the results table, and a zip (run.json + layers + PNGs)."""
import argparse
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
os.environ.setdefault("MPLBACKEND", "Agg")          # no display here; set via env so matplotlib is not imported yet
sys.path.insert(0, str(HERE))
import nisar_colab as nc  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lon", type=float, required=True)
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--as-of", default=None, help="ISO time, default now")
    ap.add_argument("--lookback", type=float, default=None, help="days of history (engine default 120)")
    ap.add_argument("--concurrency", type=int, default=None, help="simultaneous range requests (default 64)")
    ap.add_argument("--out", default=str(nc.CONTENT / "nisar_out"), help="output folder (also zipped next to it)")
    a = ap.parse_args()
    nc.REPO = HERE.parent
    if not nc.bootstrap(concurrency=a.concurrency)["ok"]:
        return 2
    state = nc.scan(a.lon, a.lat, as_of=a.as_of, lookback_days=a.lookback)
    print(nc.summary(state).to_string())
    if state.get("status") == "error":
        return 1
    print("results zip:", nc.export(state, out_dir=a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
