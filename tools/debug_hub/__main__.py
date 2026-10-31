"""python -m debug_hub  (from tools/)   or   python tools/hub.py  (from anywhere)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="debug_hub", description=__doc__)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--open", action="store_true", help="open the browser")
    ap.add_argument("--allow-selftest", action="store_true",
                    help="offer the synthetic SELF-TEST source (to test the hub itself; never mixed with live data)")
    a = ap.parse_args(argv)
    for p in (REPO, REPO / "tools"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    from debug_hub.server import serve
    serve(a.port, a.allow_selftest, "live", a.open)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
