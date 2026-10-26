"""Localhost HTTP server for the hub (stdlib only). Binds 127.0.0.1; checks Host (anti DNS-rebinding) and a
per-process token on every POST, because a job can use your Earthdata credentials and the network."""
from __future__ import annotations

import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import health
from .events import Job
from .runner import run_job
from .util import plain, short_exc

UI = Path(__file__).resolve().parent / "ui" / "index.html"
MAX_JOBS = 6


class Hub:
    def __init__(self, port: int, allow_selftest: bool, default_source: str):
        from engine.common.config import load_common_config
        from engine.retrieval.settings import load_settings
        self.port, self.allow_selftest, self.default_source = port, allow_selftest, default_source
        self.token = secrets.token_hex(16)
        self.settings = load_settings()                       # also loads .env into os.environ
        self.cfg = load_common_config()
        self.jobs: dict = {}
        self.order: list = []
        self.lock = threading.Lock()
        self._health: tuple = (0.0, None)
        self.started = time.time()

    def new_job(self, params: dict) -> Job:
        with self.lock:
            if any(not j.finished for j in self.jobs.values()):
                raise RuntimeError("a job is already running")
            jid = time.strftime("%H%M%S") + "-" + secrets.token_hex(2)
            job = Job(jid, params)
            self.jobs[jid] = job
            self.order.append(jid)
            while len(self.order) > MAX_JOBS:
                self.jobs.pop(self.order.pop(0), None)
        threading.Thread(target=run_job, args=(job,), name=f"job-{jid}", daemon=True).start()
        return job

    def health(self, force: bool = False) -> dict:
        t, snap = self._health
        if snap is None or force or time.time() - t > 10:
            snap = health.health_snapshot(self.settings)
            self._health = (time.time(), snap)
        return snap


def _json(o) -> bytes:
    return json.dumps(plain(o), allow_nan=False).encode()


def make_handler(hub: Hub):
    html = UI.read_text(encoding="utf-8").replace("__HUB_TOKEN__", hub.token)

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "debug-hub"

        def log_message(self, *a):
            pass

        # -- plumbing ------------------------------------------------------------------------
        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").split(":")[0]
            return host in ("127.0.0.1", "localhost", "[::1]")

        def _send(self, code: int, body: bytes, ctype: str = "application/json", extra: dict | None = None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            if not extra or "Cache-Control" not in extra:
                self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _err(self, code: int, msg: str):
            self._send(code, _json({"error": msg}))

        def _job(self, q) -> Job | None:
            jid = (q.get("id") or q.get("job") or [""])[0]
            j = hub.jobs.get(jid)
            if j is None:
                self._err(404, f"no such job {jid!r}")
            return j

        # -- GET -----------------------------------------------------------------------------
        def do_GET(self):
            if not self._host_ok():
                return self._err(403, "bad Host header")
            u = urlparse(self.path)
            q = parse_qs(u.query)
            try:
                if u.path == "/":
                    return self._send(200, html.encode(), "text/html; charset=utf-8")
                if u.path == "/api/config":
                    return self._send(200, _json({
                        "sources": ["live"] + (["selftest"] if hub.allow_selftest else []), "default_source": hub.default_source,
                        "lookback_days": hub.cfg.window.lookback_days, "margin_m": hub.settings.tiling.get("margin_m"),
                        "jobs": [hub.jobs[i].brief() for i in reversed(hub.order)]}))
                if u.path == "/api/tile":
                    return self._tile(q)
                if u.path == "/api/health":
                    return self._send(200, _json(hub.health(force=q.get("force") == ["1"])))
                if u.path == "/api/events":
                    return self._sse(q)
                if u.path == "/api/job":
                    return self._job_part(q)
                if u.path == "/api/img":
                    return self._img(q)
                if u.path == "/api/px":
                    return self._px(q)
                return self._err(404, "not found")
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception as ex:
                return self._err(500, short_exc(ex))

        def _tile(self, q):
            try:
                lon, lat = float(q["lon"][0]), float(q["lat"][0])
            except (KeyError, ValueError):
                return self._err(400, "lon and lat must be numbers")
            if not (-180 <= lon <= 180 and -80 <= lat <= 84):
                return self._err(400, "lon must be in [-180, 180] and lat in [-80, 84] (MGRS/UTM coverage)")
            from engine.tiling.mgrs import tile_at
            t = tile_at(lon, lat)
            m = float(hub.settings.tiling.get("margin_m", 0))
            return self._send(200, _json({"id": t.id, "epsg": int(t.epsg), "e0": t.e0, "n0": t.n0, "size_m": t.size,
                                          "bbox": list(t.bbox(0.0)), "bbox_margin": list(t.bbox(m)), "margin_m": m,
                                          "center": list(t.center)}))

        def _job_part(self, q):
            j = self._job(q)
            if j is None:
                return
            part = (q.get("part") or ["all"])[0]
            base = {**j.brief(), "tile": j.tile, "source": j.source, "request": getattr(j, "request", {}), "net": j.net_last}
            parts = {"summary": lambda: base, "retrieval": lambda: j.retrieval, "p01": lambda: getattr(j, "p01", {}),
                     "processing": lambda: {"steps": j.steps}, "final": lambda: j.final, "handoff": lambda: j.handoff}
            if part == "all":
                out = {**base, "retrieval": j.retrieval, "p01": getattr(j, "p01", {}), "steps": j.steps, "final": j.final,
                       "handoff": j.handoff, "calls_live": j.calls if j.retrieval is None else None}
            elif part in parts:
                out = parts[part]()
            else:
                return self._err(400, f"unknown part {part!r}")
            return self._send(200, _json(out))

        def _sse(self, q):
            j = self._job(q)
            if j is None:
                return
            after = int((q.get("after") or ["0"])[0])
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            try:
                while True:
                    evs = j.wait(after, 10.0)
                    for e in evs:
                        self.wfile.write(f"id: {e['seq']}\ndata: {json.dumps(plain(e), allow_nan=False)}\n\n".encode())
                        after = e["seq"]
                    if not evs:
                        self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                    if j.finished and after >= len(j.events):
                        self.wfile.write(b"event: end\ndata: {}\n\n")
                        self.wfile.flush()
                        return
            except (BrokenPipeError, ConnectionResetError):
                return

        def _img(self, q):
            j = self._job(q)
            if j is None:
                return
            g = lambda k: (q.get(k) or [None])[0]                      # noqa: E731
            try:
                lo = float(g("lo")) if g("lo") not in (None, "") else None
                hi = float(g("hi")) if g("hi") not in (None, "") else None
                body, meta = j.store.png(g("aid"), g("cmap") or None, lo, hi, int(g("max") or 720))
            except KeyError:
                return self._err(404, "no such array")
            return self._send(200, body, "image/png", {"X-Img-Meta": json.dumps(plain(meta)), "Cache-Control": "private, max-age=3600"})

        def _px(self, q):
            j = self._job(q)
            if j is None:
                return
            try:
                out = j.store.probe(q["aid"][0], float(q["fx"][0]), float(q["fy"][0]))
            except (KeyError, ValueError):
                return self._err(404, "no such array or bad coordinates")
            return self._send(200, _json(out))

        # -- POST ----------------------------------------------------------------------------
        def do_POST(self):
            if not self._host_ok():
                return self._err(403, "bad Host header")
            if self.headers.get("X-Hub-Token") != hub.token:
                return self._err(403, "missing or wrong X-Hub-Token (reload the page)")
            origin = self.headers.get("Origin")
            if origin and urlparse(origin).netloc != self.headers.get("Host"):
                return self._err(403, "cross-origin request refused")
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}") if n else {}
            except ValueError:
                return self._err(400, "body is not JSON")
            u = urlparse(self.path)
            try:
                if u.path == "/api/run":
                    src = body.get("source") or hub.default_source
                    if src == "selftest" and not hub.allow_selftest:
                        return self._err(400, "self-test source is disabled (start the hub with --allow-selftest)")
                    if src not in ("live", "selftest"):
                        return self._err(400, f"unknown source {src!r}")
                    try:
                        lon, lat = float(body["lon"]), float(body["lat"])
                    except (KeyError, TypeError, ValueError):
                        return self._err(400, "lon and lat are required numbers")
                    if not (-180 <= lon <= 180 and -80 <= lat <= 84):
                        return self._err(400, "lon must be in [-180, 180] and lat in [-80, 84]")
                    params = {"lon": lon, "lat": lat, "source": src, "as_of": body.get("as_of") or None,
                             "lookback_days": body.get("lookback_days") or None,
                             "synthetic_ancillary": bool(body.get("synthetic_ancillary"))}
                    try:
                        job = hub.new_job(params)
                    except RuntimeError as ex:
                        return self._err(409, str(ex))
                    return self._send(200, _json(job.brief()))
                if u.path == "/api/health/network":
                    return self._send(200, _json(health.network_check()))
                if u.path == "/api/health/tests":
                    return self._send(200, _json(health.run_tests()))
                return self._err(404, "not found")
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception as ex:
                return self._err(500, short_exc(ex))

    return H


def serve(port: int = 8765, allow_selftest: bool = False, default_source: str = "live", open_browser: bool = False):
    hub = Hub(port, allow_selftest, default_source)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(hub))
    httpd.daemon_threads = True
    url = f"http://127.0.0.1:{port}/"
    print(f"debug hub on {url}   (Ctrl-C to stop)   selftest source: {'enabled' if allow_selftest else 'disabled'}")
    if open_browser:
        import webbrowser
        threading.Timer(0.6, webbrowser.open, args=(url,)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
