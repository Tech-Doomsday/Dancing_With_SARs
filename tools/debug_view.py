#!/usr/bin/env python3
"""Debug view for the retrieval layer: a local, read-only dashboard over everything it has
loaded -- every granule engine/retrieval/state.py has ever seen, the MGRS tile queue those
granules produced, the poll history, and the on-disk cache footprint.

Deliberately independent of the rest of `engine/retrieval/`: this file imports only
`settings.py` and `state.py` (and, for --demo, nothing beyond the stdlib), so it never pulls
in numpy/h5py/aiohttp/zarr/pyproj/asf_search and can't fail just because one of those isn't
installed. It only ever *reads* the sqlite file (opened with sqlite3's `mode=ro`), so it is
safe to run alongside a live poller or pipeline workers without disturbing them -- it never
calls State.claim()/add_granule()/complete().

    python tools/debug_view.py                 # serve on http://127.0.0.1:8787
    python tools/debug_view.py --port 9000
    python tools/debug_view.py --data-dir /path/to/data
    python tools/debug_view.py --once           # print one JSON snapshot, no server
    python tools/debug_view.py --demo           # point at fabricated sample data instead
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.retrieval.settings import load_settings  # noqa: E402

ASSETS_DIR = Path(__file__).resolve().parent / "debug_view_assets"
QUEUE_STATUSES = ("pending", "claimed", "done", "superseded")


# ------------------------------------------------------------------ reading the state db
def _ro_connect(db_path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=5)
    c.row_factory = sqlite3.Row
    return c


def _dir_stats(p: Path, cap: int = 150_000) -> dict:
    """Byte/file count of a cache dir without assuming anything about what's in it."""
    if not p.exists():
        return {"exists": False, "bytes": 0, "files": 0, "truncated": False}
    total = n = 0
    truncated = False
    for root, _, files in os.walk(p):
        for f in files:
            n += 1
            if n > cap:
                truncated = True
                break
            try:
                total += (Path(root) / f).stat().st_size
            except OSError:
                pass
        if truncated:
            break
    return {"exists": True, "bytes": total, "files": n, "truncated": truncated}


def build_snapshot(cfg, granule_limit: int = 1000, queue_limit: int = 1000,
                    poll_log_limit: int = 300) -> dict:
    db = cfg.state_db
    snap = {
        "generated_at": time.time(),
        "data_dir": str(cfg.data_dir),
        "state_db": str(db),
        "db_exists": db.exists(),
        "config": {"poll": cfg.poll, "tiling": cfg.tiling, "cache": cfg.cache},
        "summary": {"total_granules": 0, "active_granules": 0, "superseded_granules": 0,
                    "by_product": {}, "queue": {s: 0 for s in QUEUE_STATUSES}, "last_poll": {}},
        "poll_log": [], "granules": [], "granules_total": 0,
        "queue_items": [], "queue_total": 0,
        "storage": {
            "clip_cache": _dir_stats(cfg.clips_dir),
            "chunk_cache": _dir_stats(cfg.chunks_dir),
            "search_cache": _dir_stats(cfg.search_dir),
            "layout_index": _dir_stats(cfg.index_dir),
        },
    }
    if not db.exists():
        return snap

    with closing(_ro_connect(db)) as c:
        total = c.execute("SELECT COUNT(*) n FROM granules").fetchone()["n"]
        active = c.execute("SELECT COUNT(*) n FROM granules WHERE superseded_by IS NULL").fetchone()["n"]
        by_product = {r["product"]: dict(r) for r in c.execute(
            "SELECT product, COUNT(*) total, "
            "SUM(CASE WHEN superseded_by IS NULL THEN 1 ELSE 0 END) active, "
            "MAX(start) last_start, MAX(first_seen) last_seen "
            "FROM granules GROUP BY product")}
        queue_counts = {s: 0 for s in QUEUE_STATUSES}
        queue_counts.update({r["status"]: r["n"] for r in c.execute(
            "SELECT status, COUNT(*) n FROM dirty_tiles GROUP BY status")})
        last_poll = {}
        for (prod,) in c.execute("SELECT DISTINCT product FROM poll_log"):
            r = c.execute("SELECT ts, found, new, error FROM poll_log WHERE product=? "
                          "ORDER BY ts DESC LIMIT 1", (prod,)).fetchone()
            last_poll[prod] = dict(r)

        snap["summary"] = {
            "total_granules": total, "active_granules": active,
            "superseded_granules": total - active,
            "by_product": by_product, "queue": queue_counts, "last_poll": last_poll,
        }
        snap["poll_log"] = [dict(r) for r in c.execute(
            "SELECT ts, product, found, new, error FROM poll_log ORDER BY ts DESC LIMIT ?",
            (poll_log_limit,))]
        snap["granules_total"] = total
        snap["granules"] = [dict(r) for r in c.execute(
            "SELECT granule_id, product, scene_key, version, start, stop, url, first_seen, "
            "superseded_by FROM granules ORDER BY first_seen DESC LIMIT ?", (granule_limit,))]
        snap["queue_total"] = sum(queue_counts.values())
        snap["queue_items"] = [dict(r) for r in c.execute(
            "SELECT tile, granule_id, product, status, queued, claimed, worker "
            "FROM dirty_tiles ORDER BY queued DESC LIMIT ?", (queue_limit,))]
    return snap


# ------------------------------------------------------------------ http server
class Handler(BaseHTTPRequestHandler):
    server_version = "RetrievalDebugView/1.0"

    def log_message(self, fmt, *args):  # keep stdout quiet; errors still raise
        pass

    def _send(self, status: int, body: bytes, content_type: str):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path in ("/", "/index.html"):
                self._send(200, (ASSETS_DIR / "index.html").read_bytes(), "text/html; charset=utf-8")
            elif path == "/api/snapshot":
                snap = build_snapshot(self.server.cfg)
                self._send(200, json.dumps(snap).encode("utf-8"), "application/json")
            else:
                self._send(404, b"not found", "text/plain")
        except Exception as ex:                              # debug tool: never die silently
            self._send(500, f"{type(ex).__name__}: {ex}".encode("utf-8"), "text/plain")


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


# ------------------------------------------------------------------ cli
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--data-dir", help="overrides NISAR_DATA_DIR for this run")
    ap.add_argument("--once", action="store_true", help="print one JSON snapshot to stdout and exit")
    ap.add_argument("--demo", action="store_true",
                     help="fabricate a throwaway data dir with sample granules/queue/poll "
                          "history, then use that instead of the real one")
    a = ap.parse_args()

    if a.demo:
        from _debug_demo_data import make_demo_dir
        demo_dir = make_demo_dir()
        os.environ["NISAR_DATA_DIR"] = str(demo_dir)
        print(f"[debug_view] demo data dir: {demo_dir}", file=sys.stderr)
    elif a.data_dir:
        os.environ["NISAR_DATA_DIR"] = a.data_dir

    cfg = load_settings()

    if a.once:
        print(json.dumps(build_snapshot(cfg), indent=2))
        return

    httpd = Server((a.host, a.port), Handler)
    httpd.cfg = cfg
    print(f"[debug_view] data dir : {cfg.data_dir}")
    print(f"[debug_view] state db : {cfg.state_db} "
          f"({'found' if cfg.state_db.exists() else 'not created yet -- poll first'})")
    print(f"[debug_view] serving  : http://{a.host}:{a.port}/  (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
