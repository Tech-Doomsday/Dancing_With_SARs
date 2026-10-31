#!/usr/bin/env python
"""NISAR retrieval hub for Colab: type a place and a size, see exactly what will be downloaded, download it,
then browse every stored layer as an image. One page, one tunnel, everything stored under /content/datasets.

    in a notebook cell (recommended: Colab Secrets and the hidden prompt only work inside the kernel):
        import runpy; runpy.run_path("/content/nisar_hub.pyz", run_name="__main__")
    or from a shell cell (the token must then come from the environment or a packed .env):
        !EARTHDATA_TOKEN=... python -u /content/nisar_hub.pyz

It prints one https link that ends in ?k=<secret>. Open that link: the secret becomes a cookie, and every request
without it (the tunnel URL is public) gets a 401. This file adds no engine logic: it serves the engine's own
/api/retrievals routes, plus four small /hub/api routes (tile cover, batch queue, disk usage) and the page.

Note: no `from __future__ import annotations` here on purpose. FastAPI resolves a route's annotations at
definition time, and the request models are defined inside build_app().
"""
import argparse
import hmac
import math
import os
import secrets
import shutil
import sys
import threading
import time
from http.cookies import CookieError, SimpleCookie
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode

HERE = Path(__file__).resolve().parent
PAGE = HERE / "hub_page.html"
COOKIE = "nisar_hub"
MAX_TILES = int(os.environ.get("NISAR_HUB_MAX_TILES", "25"))      # a 10 km tile of GCOV + GUNW is hundreds of MB
PRODUCTS = ("GCOV", "GUNW", "GOFF")
TERMINAL = {"done", "planned", "failed", "error", "cancelled", "interrupted"}      # RetrievalManager statuses


# ------------------------------------------------------------------------------------------ the key gate
class KeyGate:
    """Pure-ASGI shared-secret gate in front of the WHOLE app. A trycloudflare URL is public and the engine API
    can start downloads on your Earthdata token, so nothing answers without the secret.

    Accepted, in order: the cookie, an `X-Hub-Key` header, or `?k=<secret>` (which sets the cookie and redirects
    to the same address without the secret, so it does not stay in the address bar or the history)."""

    def __init__(self, app, key: str):
        if len(key) < 16:
            raise ValueError("the hub key must be at least 16 characters")
        self.app, self.key = app, key

    def _ok(self, presented) -> bool:
        return bool(presented) and hmac.compare_digest(str(presented).encode("utf-8"), self.key.encode("utf-8"))

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):                    # lifespan
            return await self.app(scope, receive, send)
        headers = {k.decode("latin1").lower(): v.decode("latin1") for k, v in scope.get("headers", [])}
        try:
            jar = SimpleCookie(headers.get("cookie", ""))
        except CookieError:
            jar = SimpleCookie()
        if (COOKIE in jar and self._ok(jar[COOKIE].value)) or self._ok(headers.get("x-hub-key")):
            return await self.app(scope, receive, send)
        query = parse_qs(scope.get("query_string", b"").decode("latin1"))
        if scope["type"] == "http" and self._ok((query.get("k") or [""])[0]):
            rest = urlencode([(k, v) for k, vs in query.items() if k != "k" for v in vs])
            where = quote(scope.get("root_path", "") + scope["path"]) + ("?" + rest if rest else "")
            flags = "; Path=/; HttpOnly; SameSite=Lax" + ("; Secure" if headers.get("x-forwarded-proto") == "https" else "")
            await send({"type": "http.response.start", "status": 303, "headers": [
                (b"location", where.encode("latin1")), (b"set-cookie", f"{COOKIE}={self.key}{flags}".encode("latin1")),
                (b"cache-control", b"no-store")]})
            await send({"type": "http.response.body", "body": b""})
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        body = b"NISAR hub: open the full link printed in your Colab cell (it ends with ?k=...)."
        await send({"type": "http.response.start", "status": 401, "headers": [
            (b"content-type", b"text/plain; charset=utf-8"), (b"cache-control", b"no-store")]})
        await send({"type": "http.response.body", "body": body})


# ------------------------------------------------------------------------------------------ tile cover
def _mgrs():
    from engine.tiling import mgrs
    return mgrs


def _cell_m() -> int:
    """The engine's configured MGRS cell (config/tiling.yaml). Retrievals use exactly this, so ids match."""
    from engine.retrieval.settings import tile_cell_size_m
    return tile_cell_size_m()


def _box_ring(lon: float, lat: float, size_km: float):
    """The square of side `size_km` centred on the point, in the point's UTM zone, as a lon/lat ring."""
    m = _mgrs()
    epsg = m.utm_epsg(m.utm_zone(lon, lat), lat >= 0)
    x, y = m._tr(4326, epsg).transform(lon, lat)
    h, back = size_km * 500.0, m._tr(epsg, 4326)
    return [back.transform(x + dx, y + dy) for dx, dy in ((-h, -h), (h, -h), (h, h), (-h, h), (-h, -h))]


def cover(lon: float, lat: float, size_km: float, snap: bool = False, max_tiles: int | None = None) -> list:
    """The engine tiles a `size_km` box around the point needs, nearest first. snap=True: just the tile under the
    point. Every returned tile round-trips (the retrieval re-derives its tile from the lon/lat given here), so what
    the page previews is what gets retrieved; duplicates at UTM zone edges collapse into one.

    Why tiles and not one big box: the engine's unit is the 10 km MGRS tile. Its canonical grids only divide
    evenly at that size (1 km is not a whole number of 80 m pixels) and cap at 4 M pixels (100 km is 25 M)."""
    m, cell = _mgrs(), _cell_m()
    limit = MAX_TILES if max_tiles is None else max_tiles
    if snap:
        found = [m.tile_at(lon, lat, size=cell)]
    else:
        found = []
        for t in m.tiles_for_polygon(_box_ring(lon, lat, size_km), cell):
            clon, clat = t.center
            try:
                found.append(m.tile_at(clon, clat, size=cell))
            except ValueError:
                continue
    unique = {t.id: t for t in found}
    if not unique:
        raise ValueError("no tile covers this box")
    if len(unique) > limit:
        raise ValueError(f"a {size_km:g} km box needs {len(unique)} tiles; this hub allows {limit} at a time "
                         f"(a smaller size, or NISAR_HUB_MAX_TILES)")
    k = math.cos(math.radians(lat))
    tiles = sorted(unique.values(), key=lambda t: ((t.center[0] - lon) * k) ** 2 + (t.center[1] - lat) ** 2)
    return [{**t.to_dict(), "lon": float(t.center[0]), "lat": float(t.center[1])} for t in tiles]


def have_map(rows: list) -> dict:
    """tile id -> what is already stored for it (most granules stored by any retrieval of that tile)."""
    out: dict = {}
    for r in rows:
        stored = int(((r.get("totals") or {}).get("stored")) or 0)
        if stored and r.get("tile") and stored > out.get(r["tile"], {}).get("stored", 0):
            out[r["tile"]] = {"stored": stored, "retrieval": r["id"]}
    return out


# ------------------------------------------------------------------------------------------ batch queue
class BatchBusy(RuntimeError):
    pass


class BatchRunner:
    """Runs a list of tiles through the engine's RetrievalManager one after another, from THIS process, so
    closing the browser tab does not stop the queue. The manager allows one retrieval at a time; this waits
    its turn (including behind a scan started from a notebook) instead of failing the tile."""

    def __init__(self, manager_factory, poll_s: float = 1.0, busy_retries: int = 600):
        self._manager_factory, self.poll_s, self.busy_retries = manager_factory, poll_s, busy_retries
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._thread = None
        self._state = {"status": "idle", "tiles": []}

    # ---- control
    def start(self, tiles: list, mode: str, params: dict) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise BatchBusy("a batch is already running; cancel it or wait for it to finish")
            self._cancel.clear()
            self._state = {"status": "running", "mode": mode, "params": dict(params), "started_at": time.time(),
                           "finished_at": None, "current": 0,
                           "tiles": [{"id": t["id"], "lon": t["lon"], "lat": t["lat"], "rid": None, "status": "waiting",
                                      "stage": None, "totals": None, "error": None} for t in tiles]}
            self._thread = threading.Thread(target=self._run, args=(mode, dict(params)), name="hub-batch", daemon=True)
            self._thread.start()

    def cancel(self) -> bool:
        with self._lock:
            running = self._state["status"] == "running"
        if running:
            self._cancel.set()
        return running

    def snapshot(self) -> dict:
        import copy
        with self._lock:
            return copy.deepcopy(self._state)

    def join(self, timeout: float | None = None) -> None:
        t = self._thread
        if t is not None:
            t.join(timeout)

    # ---- worker
    def _mark(self, i: int, **kw) -> None:
        with self._lock:
            self._state["tiles"][i].update(kw)

    def _run(self, mode: str, params: dict) -> None:
        try:
            try:
                mgr = self._manager_factory()
            except Exception as ex:                          # the engine itself would not load: say so on every tile
                for i in range(len(self._state["tiles"])):
                    self._mark(i, status="error", error=f"engine unavailable: {type(ex).__name__}: {ex}")
                return
            for i in range(len(self._state["tiles"])):
                if self._cancel.is_set():
                    self._mark(i, status="skipped")
                    continue
                with self._lock:
                    self._state["current"] = i
                try:
                    self._one(mgr, i, mode, params)
                except Exception as ex:                      # one tile failing never stops the others
                    self._mark(i, status="error", error=f"{type(ex).__name__}: {ex}")
        finally:
            with self._lock:
                self._state["status"] = "cancelled" if self._cancel.is_set() else "finished"
                self._state["finished_at"] = time.time()

    def _one(self, mgr, i: int, mode: str, params: dict) -> None:
        row = self._state["tiles"][i]
        rid = None
        for _ in range(self.busy_retries):
            try:
                rid = mgr.start(lon=row["lon"], lat=row["lat"], mode=mode, **params)
                break
            except Exception as ex:
                if getattr(ex, "status", None) == 409:        # the engine is busy with something else: wait our turn
                    if self._cancel.is_set():                 # ...unless the batch was cancelled meanwhile
                        self._mark(i, status="skipped", stage=None)
                        return
                    self._mark(i, status="waiting", stage="engine busy with something else")
                    time.sleep(self.poll_s * 2)
                    continue
                self._mark(i, status="error", error=str(ex))
                return
        if rid is None:
            self._mark(i, status="error", error="the engine stayed busy; this tile was not started")
            return
        self._mark(i, rid=rid, status="queued")
        cancel_sent = False
        while True:
            j = mgr.get(rid) or {}
            status = j.get("status") or "queued"
            self._mark(i, status=status, stage=j.get("stage"), totals=j.get("totals"), error=j.get("error"))
            if status in TERMINAL:
                return
            if self._cancel.is_set() and not cancel_sent and status in ("queued", "running"):
                try:
                    mgr.cancel(rid)
                except Exception:
                    pass
                cancel_sent = True
            time.sleep(self.poll_s)


# ------------------------------------------------------------------------------------------ disk usage
_DISK_CACHE: dict = {}


def disk_usage(root, budget_s: float = 4.0, max_age_s: float = 10.0) -> dict:
    """Bytes under each top-level folder of the data dir (clips, retrievals, cache, ...) plus the volume's free
    space. Stops walking after `budget_s` and says so (`partial`) instead of hanging on a huge cache."""
    root = Path(root)
    hit = _DISK_CACHE.get(str(root))
    if hit and time.monotonic() - hit[0] < max_age_s:
        return hit[1]
    end, partial = time.monotonic() + budget_s, False

    def walk(top: str):
        nonlocal partial
        total = files = 0
        stack = [top]
        while stack:
            if time.monotonic() > end:
                partial = True
                break
            try:
                it = os.scandir(stack.pop())
            except OSError:
                continue
            with it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            total += e.stat(follow_symlinks=False).st_size
                            files += 1
                    except OSError:
                        pass
        return total, files

    parts, loose, loose_n = [], 0, 0
    if root.is_dir():
        for child in sorted(root.iterdir(), key=lambda p: p.name):
            if child.is_dir() and not child.is_symlink():
                b, n = walk(str(child))
                parts.append({"name": child.name, "bytes": b, "files": n})
            elif child.is_file():
                loose += child.stat().st_size
                loose_n += 1
    if loose_n:
        parts.append({"name": "(loose files)", "bytes": loose, "files": loose_n})
    vol = shutil.disk_usage(root if root.exists() else root.parent if root.parent.exists() else Path("/"))
    out = {"ok": True, "data_dir": str(root), "parts": sorted(parts, key=lambda p: -p["bytes"]),
           "used_bytes": sum(p["bytes"] for p in parts), "free_bytes": vol.free, "total_bytes": vol.total,
           "partial": partial}
    _DISK_CACHE[str(root)] = (time.monotonic(), out)
    return out


# ------------------------------------------------------------------------------------------ the app
def build_app(key: str):
    """A FRESH engine app (so a second launch in one kernel never serves a stale key) plus the hub routes."""
    from fastapi import HTTPException, Query
    from fastapi.responses import HTMLResponse
    from pydantic import BaseModel, Field

    from engine.api.jobs import NO_EARTHDATA_MESSAGE, earthdata_present, scans_enabled
    from engine.api.main import create_app
    from engine.api.routes_retrievals import manager
    from engine.common.config import data_root

    app = create_app()
    batch = BatchRunner(manager)

    class BatchBody(BaseModel):
        lon: float = Field(..., ge=-180, le=180)
        lat: float = Field(..., ge=-90, le=90)
        size_km: float = Field(10.0, gt=0, le=200)
        snap: bool = False
        mode: str = "plan"
        lookback_days: float | None = Field(default=None, gt=0, le=3650)
        max_gcov_epochs: int | None = Field(default=None, ge=1, le=500)
        max_gunw_pairs: int | None = Field(default=None, ge=1, le=500)
        products: list[str] | None = None

    def bad(status: int, message: str):
        return HTTPException(status_code=status, detail={"ok": False, "error": message})

    @app.get("/", include_in_schema=False)
    def hub_page():
        return HTMLResponse(PAGE.read_text("utf-8").replace("__HUB_TOKEN__", key), headers={"Cache-Control": "no-store"})

    @app.get("/hub/api/cover")
    def hub_cover(lon: float = Query(..., ge=-180, le=180), lat: float = Query(..., ge=-90, le=90),
                  size_km: float = Query(10.0, gt=0, le=200), snap: bool = False):
        try:
            tiles = cover(lon, lat, size_km, snap)
        except ValueError as ex:
            raise bad(400, str(ex)) from ex
        return {"ok": True, "cell_m": _cell_m(), "max_tiles": MAX_TILES, "tiles": tiles,
                "have": have_map(manager().list(None))}

    @app.post("/hub/api/batch", status_code=202)
    def hub_batch_start(body: BatchBody):
        if body.mode not in ("plan", "retrieve"):
            raise bad(400, "mode must be 'plan' or 'retrieve'")
        products = [p.upper() for p in (body.products or [])]
        if any(p not in PRODUCTS for p in products):
            raise bad(400, f"products must be a subset of {list(PRODUCTS)}")
        if not scans_enabled():
            raise bad(403, "downloads are disabled on this engine (NISAR_ENABLE_SCANS=1 is set by the Colab launcher)")
        if not earthdata_present():
            raise bad(503, NO_EARTHDATA_MESSAGE)
        try:
            tiles = cover(body.lon, body.lat, body.size_km, body.snap)
        except ValueError as ex:
            raise bad(400, str(ex)) from ex
        params = {"lookback_days": body.lookback_days, "max_gcov_epochs": body.max_gcov_epochs,
                  "max_gunw_pairs": body.max_gunw_pairs, "products": products or None}
        try:
            batch.start(tiles, body.mode, params)
        except BatchBusy as ex:
            raise bad(409, str(ex)) from ex
        return {"ok": True, "tiles": [t["id"] for t in tiles]}

    @app.get("/hub/api/batch")
    def hub_batch_state():
        return {"ok": True, **batch.snapshot()}

    @app.post("/hub/api/batch/cancel")
    def hub_batch_cancel():
        return {"ok": True, "was_running": batch.cancel()}

    @app.get("/hub/api/disk")
    def hub_disk(fresh: bool = False):
        return disk_usage(data_root(), max_age_s=0.0 if fresh else 10.0)

    return app


# ------------------------------------------------------------------------------------------ entry point
def _have_credentials() -> bool:
    import nisar_colab as nc
    nc.load_env()
    e = os.environ
    return bool(e.get("EARTHDATA_TOKEN") or (e.get("EARTHDATA_USERNAME") and e.get("EARTHDATA_PASSWORD")))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8900)
    ap.add_argument("--concurrency", type=int, default=None, help="simultaneous range requests (default 64)")
    ap.add_argument("--data-dir", default=None, help="where everything is stored (default /content/datasets)")
    ap.add_argument("--no-tunnel", action="store_true", help="serve on 127.0.0.1 only")
    a = ap.parse_args(argv)

    sys.path.insert(0, str(HERE))
    import nisar_colab as nc
    nc.REPO = HERE.parent
    in_kernel = "ipykernel" in sys.modules
    if not in_kernel and not _have_credentials():
        print("No Earthdata credentials, and a shell cell cannot read Colab Secrets or prompt you.\n"
              "  Run it from a notebook cell instead:   import runpy; runpy.run_path(\"/content/nisar_hub.pyz\", run_name=\"__main__\")\n"
              "  or pass the token:                     !EARTHDATA_TOKEN=... python -u /content/nisar_hub.pyz\n"
              "  or build the module with --include-env (then keep that file private).")
        return 2
    boot = nc.bootstrap(concurrency=a.concurrency, data_dir=a.data_dir or str(nc.CONTENT / "datasets"))
    if not boot["ok"]:
        return 2

    key = secrets.token_urlsafe(24)
    os.environ["NISAR_BACKGROUND_TOKEN"] = key           # the engine's own gate for the stored-clip routes
    url = nc.serve(a.port, tunnel=not a.no_tunnel, app=KeyGate(build_app(key), key))
    print(f"\nOpen this link (it contains your access key; do not share it):\n\n    {url}/?k={key}\n\n"
          f"Everything you retrieve is stored in {boot['applied']['NISAR_DATA_DIR']} on this runtime's disk "
          f"(wiped when the session ends: download from the Datasets tab).\n", flush=True)
    if in_kernel:
        print("The hub keeps running in the background of this notebook. Stop it: import nisar_colab as nc; nc.stop_serve()")
        return 0
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
