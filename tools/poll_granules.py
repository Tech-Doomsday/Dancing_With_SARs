#!/usr/bin/env python3
"""Poll ASF for new NISAR granules and queue the MGRS tiles they touch.

    python tools/poll_granules.py                  # one pass, prints what was found
    python tools/poll_granules.py --loop           # every poll.interval_hours (config/retrieval.yaml)
    python tools/poll_granules.py --region -119 34 -118 35 --products GCOV
    python tools/poll_granules.py --queue          # just show the work queue
"""
import argparse, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.retrieval.clients.nisar_client import NisarClient
from engine.retrieval.scheduler.granule_poller import GranulePoller


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--queue", action="store_true")
    ap.add_argument("--region", type=float, nargs=4, metavar=("W", "S", "E", "N"))
    ap.add_argument("--products", nargs="+")
    ap.add_argument("--lookback-days", type=float)
    a = ap.parse_args()
    c = NisarClient()
    if a.region: c.cfg.poll["region"] = list(a.region)
    if a.products: c.cfg.poll["products"] = a.products
    if a.lookback_days: c.cfg.poll["lookback_days"] = a.lookback_days
    p = GranulePoller(c)
    if a.queue:
        print("queue:", p.state.counts())
        for it in p.state.claim(0): print(it)
        return
    if a.loop:
        p.run_forever()
    r = p.poll()
    print(f"new granules: {len(r.new)}   superseded: {len(r.superseded)}   dirty tiles: {len(r.dirty)}   degraded: {r.degraded}")
    for ref in r.new[:15]:
        print("  ", ref.granule_id)
    if len(r.new) > 15: print(f"   ... and {len(r.new) - 15} more")
    if r.no_footprint: print("no footprint from ASF (not tiled):", r.no_footprint[:5])
    if r.errors: print("errors:", r.errors)
    print("queue:", p.state.counts())
    c.close()


if __name__ == "__main__":
    main()
