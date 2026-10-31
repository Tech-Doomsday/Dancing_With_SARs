"""Runs store — stdlib sqlite3, the first concrete slice of verdict 4.5.

Schema is deliberately the state shape the future task queue will adopt
(docs/IMPLEMENTATION_PLAN.md 10.3). DB write failures are best-effort and
never block science (mirrors the reference app's runner semantics).
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from engine.common.sqlite_util import connect

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs(
  id TEXT PRIMARY KEY, created_at TEXT, bundle_id TEXT, tile TEXT,
  pipelines TEXT, params TEXT, status TEXT, error TEXT, duration_s REAL);
CREATE TABLE IF NOT EXISTS run_verdicts(
  run_id TEXT, pipeline TEXT, tile TEXT, epoch TEXT, raw_p REAL, status TEXT,
  result TEXT,
  PRIMARY KEY(run_id, pipeline));
"""

# D11/D2: a run knows whether it came from a bundle run or a location scan. The column is added
# idempotently for databases created before this field existed.
_KIND_MIGRATION = "ALTER TABLE runs ADD COLUMN kind TEXT NOT NULL DEFAULT 'bundle'"


class RunsStore:
    def __init__(self, db_path: str | Path):
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._conn() as conn:
            conn.executescript(_SCHEMA)
            cols = {r[1] for r in conn.execute("PRAGMA table_info(runs)").fetchall()}
            if "kind" not in cols:
                conn.execute(_KIND_MIGRATION)

    def _conn(self) -> sqlite3.Connection:
        # Rollback journal by default (WAL's -shm file fails on some Windows filesystems and a failed
        # attempt can leave the file stuck in WAL) - see engine/common/sqlite_util.py. The verdict
        # store (4.5) shares this db file.
        conn = connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    def create_run(self, run_id: str, bundle_id: str, tile: str, pipelines: list, params: dict,
                   kind: str = "bundle") -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO runs(id, created_at, bundle_id, tile, pipelines, params, status, kind)"
                " VALUES (?,?,?,?,?,?, 'queued', ?)",
                (run_id, _now_iso(), bundle_id, tile, json.dumps(pipelines), json.dumps(params), kind))

    def set_status(self, run_id: str, status: str, error: str | None = None,
                   duration_s: float | None = None) -> None:
        with self._lock, self._conn() as conn:
            conn.execute("UPDATE runs SET status=?, error=?, duration_s=? WHERE id=?",
                         (status, error, duration_s, run_id))

    def set_run_meta(self, run_id: str, *, tile: str | None = None,
                     bundle_id: str | None = None) -> None:
        """D11: the runs row carries the tile (and bundle id) as soon as they are known."""
        with self._lock, self._conn() as conn:
            if tile is not None:
                conn.execute("UPDATE runs SET tile=? WHERE id=?", (tile, run_id))
            if bundle_id is not None:
                conn.execute("UPDATE runs SET bundle_id=? WHERE id=?", (bundle_id, run_id))

    def save_verdict(self, run_id: str, result: dict) -> None:
        """Run-scoped result cache (table run_verdicts). The CANONICAL verdict table (`verdicts`)
        is owned by engine.verdict.persist.VerdictStore — one store, two concerns (Decision 1)."""
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO run_verdicts(run_id, pipeline, tile, epoch, raw_p, status, result)"
                " VALUES (?,?,?,?,?,?,?)",
                (run_id, result.get("pipeline"), result.get("tile"), result.get("epoch"),
                 result.get("raw_p"), result.get("status"), json.dumps(result)))

    def get_run(self, run_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def get_verdicts(self, run_id: str) -> list:
        with self._conn() as conn:
            rows = conn.execute("SELECT result FROM run_verdicts WHERE run_id=? ORDER BY pipeline", (run_id,)).fetchall()
        return [json.loads(r["result"]) for r in rows]

    def list_runs(self, limit: int = 50) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, created_at, bundle_id, tile, pipelines, params, status, error, duration_s, kind"
                " FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["pipelines"] = json.loads(d.get("pipelines") or "[]")
            d["params"] = json.loads(d.get("params") or "{}")
            out.append(d)
        return out

    def active_run(self) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id FROM runs WHERE status IN ('queued','running') ORDER BY created_at DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def reset_stale_runs(self, keep: set[str]) -> int:
        """D10: a crashed engine leaves queued/running rows in sqlite forever, and start_run
        would then 409 eternally (active_run() reads only the db). On runner start, rows that
        claim to be active but have NO live state in this process are stale: mark them error.
        `keep` holds the ids that ARE alive in this process (single-flight still holds)."""
        if not keep:
            keep = set()
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT id FROM runs WHERE status IN ('queued','running')").fetchall()
            stale = [r["id"] for r in rows if r["id"] not in keep]
            for rid in stale:
                conn.execute(
                    "UPDATE runs SET status='error', error='engine restarted before this run finished'"
                    " WHERE id=?", (rid,))
        return len(stale)


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def monotonic() -> float:
    return time.monotonic()
