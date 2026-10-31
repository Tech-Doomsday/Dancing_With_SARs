"""S3 spike (U4, U5) — throw-away: build a NisarClient inside a ThreadPoolExecutor(max_workers=1)
worker, read client.auth.available, close it; import engine.tiling.mgrs.tile_at for (-118.25, 34.05).
Captures exact output. No network call beyond what the hub already makes (auth check is local).
"""
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def worker() -> dict:
    out: dict = {}
    try:
        from engine.retrieval.clients.nisar_client import NisarClient
        client = NisarClient()
        out["auth_available"] = client.auth.available
        out["auth_repr_safe"] = repr(client.auth)[:120]
        client.close()
        out["closed"] = True
    except Exception as ex:
        out["error"] = f"{type(ex).__name__}: {ex}"
        out["trace"] = traceback.format_exc(limit=4)
    return out


def main() -> None:
    print("=== S3: NisarClient in a ThreadPoolExecutor(max_workers=1) worker ===")
    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(worker)
        result = fut.result(timeout=60)
    for k, v in result.items():
        print(f"{k}: {v}")

    print("=== S3b: pyproj-backed tile_at ===")
    try:
        from engine.tiling.mgrs import tile_at
        t = tile_at(-118.25, 34.05)
        print("tile_at(-118.25, 34.05) ->", t.id, "epsg", t.epsg, "center", [round(c, 5) for c in t.center])
        import pyproj
        print("pyproj version:", pyproj.__version__)
    except Exception as ex:
        print("tile_at error:", f"{type(ex).__name__}: {ex}")


if __name__ == "__main__":
    main()
