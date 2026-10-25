"""SQLite state shared by the poller and the pipeline workers: which granules were seen, which
versions superseded which, and a queue of tiles that need (re)processing. WAL mode + short
transactions so several worker processes can claim work concurrently."""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS granules(
  granule_id TEXT PRIMARY KEY, product TEXT, scene_key TEXT, version INTEGER, start TEXT, stop TEXT,
  url TEXT, footprint TEXT, first_seen REAL, superseded_by TEXT);
CREATE INDEX IF NOT EXISTS granules_scene ON granules(scene_key, version);
CREATE TABLE IF NOT EXISTS dirty_tiles(
  tile TEXT, granule_id TEXT, product TEXT, queued REAL, status TEXT DEFAULT 'pending',
  claimed REAL, worker TEXT, PRIMARY KEY(tile, granule_id));
CREATE INDEX IF NOT EXISTS dirty_status ON dirty_tiles(status, queued);
CREATE TABLE IF NOT EXISTS poll_log(ts REAL, product TEXT, found INTEGER, new INTEGER, error TEXT);
"""


class State:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        try:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(SCHEMA)           # executescript commits implicitly, so it stays out of _tx()
        finally:
            c.close()

    @contextmanager
    def _tx(self):
        c = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        c.row_factory = sqlite3.Row
        try:
            for attempt in range(60):                       # Windows can report 'locked' despite the busy timeout
                try:
                    c.execute("BEGIN IMMEDIATE")
                    break
                except sqlite3.OperationalError as ex:
                    if "locked" not in str(ex).lower() and "busy" not in str(ex).lower() or attempt == 59:
                        raise
                    time.sleep(0.05 * (1 + attempt % 10))
            yield c
            c.execute("COMMIT")
        except BaseException:
            if c.in_transaction:
                c.execute("ROLLBACK")
            raise
        finally:
            c.close()

    # ------------------------------------------------------------------ granules
    def seen(self, granule_id: str) -> bool:
        with self._tx() as c:
            return c.execute("SELECT 1 FROM granules WHERE granule_id=?", (granule_id,)).fetchone() is not None

    def add_granule(self, ref, key: str, version: int, tiles: list[str], now: float):
        """Insert a new granule and its dirty tiles atomically. Returns (older_ids_superseded, already_superseded)."""
        with self._tx() as c:
            older = [r["granule_id"] for r in c.execute(
                "SELECT granule_id FROM granules WHERE scene_key=? AND version<? AND superseded_by IS NULL", (key, version))]
            newer = c.execute("SELECT granule_id FROM granules WHERE scene_key=? AND version>? LIMIT 1", (key, version)).fetchone()
            c.execute("INSERT INTO granules VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (ref.granule_id, ref.product, key, version, ref.start, ref.stop, ref.url,
                       json.dumps(ref.footprint) if ref.footprint else None, now,
                       newer["granule_id"] if newer else None))
            if older:
                c.executemany("UPDATE granules SET superseded_by=? WHERE granule_id=?", [(ref.granule_id, o) for o in older])
                c.executemany("UPDATE dirty_tiles SET status='superseded' WHERE granule_id=? AND status='pending'",
                              [(o,) for o in older])
            if not newer:
                c.executemany("INSERT OR IGNORE INTO dirty_tiles(tile,granule_id,product,queued) VALUES(?,?,?,?)",
                              [(t, ref.granule_id, ref.product, now) for t in tiles])
            return older, bool(newer)

    def log_poll(self, product, found, new, error=None):
        with self._tx() as c:
            c.execute("INSERT INTO poll_log VALUES(?,?,?,?,?)", (time.time(), product, found, new, error))

    def last_ok_poll(self, product) -> float | None:
        with self._tx() as c:
            r = c.execute("SELECT MAX(ts) t FROM poll_log WHERE product=? AND error IS NULL", (product,)).fetchone()
            return r["t"]

    # ------------------------------------------------------------------ work queue
    def claim(self, limit: int = 50, worker: str = "w", product: str | None = None) -> list[dict]:
        with self._tx() as c:
            q = "SELECT tile, granule_id, product FROM dirty_tiles WHERE status='pending'"
            args: list = []
            if product:
                q += " AND product=?"; args.append(product)
            rows = c.execute(q + " ORDER BY queued, tile LIMIT ?", (*args, limit)).fetchall()
            now = time.time()
            c.executemany("UPDATE dirty_tiles SET status='claimed', claimed=?, worker=? WHERE tile=? AND granule_id=?",
                          [(now, worker, r["tile"], r["granule_id"]) for r in rows])
            return [dict(r) for r in rows]

    def complete(self, tile: str, granule_id: str, ok: bool = True):
        with self._tx() as c:
            c.execute("UPDATE dirty_tiles SET status=? WHERE tile=? AND granule_id=?", ("done" if ok else "pending", tile, granule_id))

    def requeue_stale(self, older_than_s: float = 3600) -> int:
        with self._tx() as c:
            return c.execute("UPDATE dirty_tiles SET status='pending', worker=NULL WHERE status='claimed' AND claimed<?",
                             (time.time() - older_than_s,)).rowcount

    def counts(self) -> dict:
        with self._tx() as c:
            return {r["status"]: r["n"] for r in c.execute("SELECT status, COUNT(*) n FROM dirty_tiles GROUP BY status")}
