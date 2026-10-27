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

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs(
  id TEXT PRIMARY KEY, created_at TEXT, bundle_id TEXT, tile TEXT,
  pipelines TEXT, params TEXT, status TEXT, error TEXT, duration_s REAL);
CREATE TABLE IF NOT EXISTS verdicts(
  run_id TEXT, pipeline TEXT, tile TEXT, epoch TEXT, raw_p REAL, status TEXT,
  result TEXT,
  PRIMARY KEY(run_id, pipeline));
"""


class RunsStore:
    def __init__(self, db_path: str | Path):
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    def create_run(self, run_id: str, bundle_id: str, tile: str, pipelines: list, params: dict) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO runs(id, created_at, bundle_id, tile, pipelines, params, status)"
                " VALUES (?,?,?,?,?,?, 'queued')",
                (run_id, _now_iso(), bundle_id, tile, json.dumps(pipelines), json.dumps(params)))

    def set_status(self, run_id: str, status: str, error: str | None = None,
                   duration_s: float | None = None) -> None:
        with self._lock, self._conn() as conn:
            conn.execute("UPDATE runs SET status=?, error=?, duration_s=? WHERE id=?",
                         (status, error, duration_s, run_id))

    def save_verdict(self, run_id: str, result: dict) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO verdicts(run_id, pipeline, tile, epoch, raw_p, status, result)"
                " VALUES (?,?,?,?,?,?,?)",
                (run_id, result.get("pipeline"), result.get("tile"), result.get("epoch"),
                 result.get("raw_p"), result.get("status"), json.dumps(result)))

    def get_run(self, run_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def get_verdicts(self, run_id: str) -> list:
        with self._conn() as conn:
            rows = conn.execute("SELECT result FROM verdicts WHERE run_id=? ORDER BY pipeline", (run_id,)).fetchall()
        return [json.loads(r["result"]) for r in rows]

    def list_runs(self, limit: int = 50) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, created_at, bundle_id, tile, pipelines, params, status, error, duration_s"
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


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def monotonic() -> float:
    return time.monotonic()
