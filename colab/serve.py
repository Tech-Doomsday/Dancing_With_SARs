#!/usr/bin/env python
"""Run the engine's API on Colab behind a Cloudflare quick tunnel and keep it up, so your LOCAL web UI
(app-ui) can use Colab's network:

    !python -u /content/nisar-hazard-engine/colab/serve.py

Prints an https URL. Paste it into the engine pill in the UI's nav bar (or put it in app-ui/.env.local
as NEXT_PUBLIC_ENGINE_API=<url> and restart `npm run dev`).
The URL has NO authentication and scans are enabled: anyone holding it can spend your Earthdata token.
Stop the cell (square button) when finished."""
import argparse
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import nisar_colab as nc  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8900)
    ap.add_argument("--concurrency", type=int, default=None)
    ap.add_argument("--ui-origin", default=None, help="extra CORS origin (scheme+host+port); localhost:3000 is always allowed")
    a = ap.parse_args()
    nc.REPO = HERE.parent
    if not nc.bootstrap(concurrency=a.concurrency)["ok"]:
        return 2
    url = nc.serve(a.port, tunnel=True, ui_origin=a.ui_origin)
    print(f"\nNEXT_PUBLIC_ENGINE_API={url}\n(keep this URL private; stop this cell to close the tunnel)\n", flush=True)
    try:
        while True:
            time.sleep(30)
    except KeyboardInterrupt:
        pass
    finally:
        nc.stop_serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())
