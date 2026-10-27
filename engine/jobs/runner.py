"""HeavyJobRunner — own timeout, progress file, content-addressed result cache (SQLite WAL).

Design (port decisions 9 + Pipeline contract):
  * The registry stays the only place that knows about pools and lanes; it DEFERS heavy pipelines
    (status PENDING, detail.lane=heavy). The API submits them here.
  * One heavy job at a time (the solver owns hundreds of MB); a thread pool of 1 + a queue.
  * Cooperative deadline: the pipeline reports progress through a callback; when the job exceeds
    the manifest's timeout_s the callback raises inside the pipeline (the solver checks it every
    chunk), and the job answers ERROR with the timeout reason — never a silent partial result.
  * Content-addressed result cache: key = sha256(pipeline_id + bundle_id + params + code versions).
    A repeated submit returns the cached verdict immediately (flagged `cached`).
  * Bookkeeping in a SQLite WAL database (data/jobs/jobs.sqlite) and a per-job progress JSON file
    (data/jobs/<job_id>.json) the UI can poll without holding any lock.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import sqlite3
import threading
import time
import sys
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from engine.common.bundle_io import load_bundle
from engine.common.result import JobCancelled, PipelineResult, plain
from engine.registry import Registry

_JOBS_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs(
  id TEXT PRIMARY KEY,
  pipeline TEXT NOT NULL,
  bundle_id TEXT NOT NULL,
  cache_key TEXT,
  status TEXT NOT NULL,                 -- queued | running | ok | error
  error TEXT,
  duration_s REAL,
  cached INTEGER DEFAULT 0,
  created_at TEXT,
  finished_at TEXT
);
"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class HeavyJobRunner:
    def __init__(self, data_dir: Path | str | None = None, registry: Registry | None = None,
                 bundles_dir: Path | str | None = None):
        from engine.common.config import data_root
        self.data_dir = Path(data_dir) if data_dir else data_root() / "jobs"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.registry = registry or Registry(bundles_dir=bundles_dir)
        self.bundles_dir = Path(bundles_dir) if bundles_dir else (self.data_dir.parent / "bundles")
        self._db = sqlite3.connect(self.data_dir / "jobs.sqlite", check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_JOBS_SCHEMA)
        self._db.commit()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="heavy-job")
        self._progress: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._cancelled: set[str] = set()

    # ------------------------------------------------------------------ API
    def submit(self, pipeline: str, bundle_path: Path | str, params: dict | None = None) -> dict:
        """Submit (or return the cached result of) one heavy job. Never raises for known states."""
        bundle_path = Path(bundle_path)
        if not (bundle_path / "bundle.json").exists():
            raise FileNotFoundError(f"unknown bundle: {bundle_path}")
        try:
            bundle = load_bundle(bundle_path, mmap=False)
        except Exception as ex:
            return {"job_id": None, "status": "error", "error": f"bundle unreadable: {type(ex).__name__}: {ex}"}
        m = self.registry.manifest(pipeline)
        params_json = json.dumps(params or {}, sort_keys=True, default=str)
        key = hashlib.sha256(
            f"{pipeline}|{bundle.bundle_id}|{params_json}|{m.code}|{_code_version()}".encode("utf-8")).hexdigest()

        cached = self._cache_get(key)
        if cached is not None:
            job_id = f"job-{uuid.uuid4().hex[:12]}"
            self._record(job_id, pipeline, bundle.bundle_id, key, "ok", None, 0.0, cached=True)
            return {"job_id": job_id, "status": "ok", "cached": True, "result": cached}

        job_id = f"job-{uuid.uuid4().hex[:12]}"
        self._record(job_id, pipeline, bundle.bundle_id, key, "queued", None, None)
        self._write_progress(job_id, {"status": "queued", "stage": "queued", "fraction": 0.0})
        self._pool.submit(self._execute, job_id, pipeline, bundle_path, params or {}, m.deadline_s, key)
        return {"job_id": job_id, "status": "queued", "cached": False}

    def get_state(self, job_id: str) -> dict:
        row = self._db.execute("SELECT id, pipeline, bundle_id, status, error, duration_s, cached FROM jobs WHERE id=?",
                               (job_id,)).fetchone()
        if row is None:
            return {"id": job_id, "status": "unknown"}
        state = {"id": row[0], "pipeline": row[1], "bundle_id": row[2], "status": row[3],
                 "error": row[4], "duration_s": row[5], "cached": bool(row[6])}
        with self._lock:
            prog = self._progress.get(job_id)
        if prog is not None:
            state["progress"] = prog
        else:
            pf = self.data_dir / f"{job_id}.json"
            try:
                state["progress"] = json.loads(pf.read_text())
            except Exception:
                state["progress"] = {}
        if state["status"] == "ok":
            state["result"] = self._cache_get_by_job(job_id)
        return state

    def cancel(self, job_id: str) -> bool:
        """Cooperative cancel: the next progress callback inside the pipeline raises JobCancelled."""
        with self._lock:
            if job_id in self._progress and self._progress[job_id].get("status") == "running":
                self._cancelled.add(job_id)
                return True
        return False

    # ------------------------------------------------------------------ exec
    def _execute(self, job_id: str, pipeline: str, bundle_path: Path, params: dict,
                 timeout_s: float, cache_key: str) -> None:
        t0 = time.monotonic()
        self._record_status(job_id, "running")
        self._write_progress(job_id, {"status": "running", "stage": "loading bundle", "fraction": 0.0})
        started = time.time()

        def progress(stage: str, fraction: float) -> None:
            if time.monotonic() - t0 > timeout_s:
                raise JobCancelled(
                    f"heavy job exceeded timeout_s={timeout_s:g} (manifest deadline) at stage {stage!r}")
            if job_id in self._cancelled:
                raise JobCancelled("cancelled by request")
            self._write_progress(job_id, {"status": "running", "stage": stage,
                                          "fraction": round(max(0.0, min(1.0, float(fraction))), 4),
                                          "elapsed_s": round(time.time() - started, 1)})

        try:
            for p in self.registry.sys_path:              # same rule as the registry's spawned workers
                if p not in sys.path:
                    sys.path.insert(0, p)
            bundle = load_bundle(bundle_path, mmap=True)
            mod = importlib.import_module(self.registry.module_for(pipeline))
            result = mod.run(bundle, providers={"progress": progress, "params": params})
            if not isinstance(result, PipelineResult):
                from engine.registry import coerce_result
                result = coerce_result(result, self.registry.manifest(pipeline), bundle)
            result.runtime_s = time.monotonic() - t0
            self._cache_put(cache_key, result.to_dict())
            self._record(job_id, pipeline, bundle.bundle_id, cache_key, "ok", None,
                         time.monotonic() - t0)
            self._write_progress(job_id, {"status": "ok", "stage": "done", "fraction": 1.0,
                                          "elapsed_s": round(time.monotonic() - t0, 1)})
        except JobCancelled as ex:
            self._record(job_id, pipeline, "", cache_key, "error", str(ex), time.monotonic() - t0)
            self._write_progress(job_id, {"status": "error", "stage": "timeout/cancelled",
                                          "error": str(ex)})
        except Exception as ex:
            err = f"{type(ex).__name__}: {ex}"
            self._record(job_id, pipeline, "", cache_key, "error", err[:500], time.monotonic() - t0)
            self._write_progress(job_id, {"status": "error", "stage": "error", "error": err})
            traceback.print_exc()

    # ------------------------------------------------------------------ storage
    def _record(self, job_id, pipeline, bundle_id, cache_key, status, error, duration_s, cached=False):
        self._db.execute(
            "INSERT INTO jobs(id, pipeline, bundle_id, cache_key, status, error, duration_s, cached, created_at, finished_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET status=excluded.status, error=excluded.error, "
            "duration_s=excluded.duration_s, cached=excluded.cached, finished_at=excluded.finished_at",
            (job_id, pipeline, bundle_id, cache_key, status, error, duration_s, int(cached),
             _utcnow(), _utcnow() if status in ("ok", "error") else None))
        self._db.commit()

    def _record_status(self, job_id, status):
        self._db.execute("UPDATE jobs SET status=?, finished_at=? WHERE id=?",
                         (status, _utcnow() if status in ("ok", "error") else None, job_id))
        self._db.commit()

    def _cache_path(self, key: str) -> Path:
        return self.data_dir / "cache" / f"{key}.json"

    def _cache_get(self, key: str) -> dict | None:
        try:
            return json.loads(self._cache_path(key).read_text())
        except Exception:
            return None

    def _cache_get_by_job(self, job_id: str) -> dict | None:
        row = self._db.execute("SELECT cache_key FROM jobs WHERE id=?", (job_id,)).fetchone()
        return self._cache_get(row[0]) if row and row[0] else None

    def _cache_put(self, key: str, result: dict) -> None:
        try:
            d = self._cache_path(key).parent
            d.mkdir(parents=True, exist_ok=True)
            self._cache_path(key).write_text(json.dumps(plain(result)))
        except Exception:
            pass  # cache write failures never fail the job

    def _write_progress(self, job_id: str, state: dict) -> None:
        with self._lock:
            self._progress[job_id] = state
        try:
            (self.data_dir / f"{job_id}.json").write_text(json.dumps(
                {**state, "updated_at": _utcnow()}))
        except Exception:
            pass  # the in-memory copy remains authoritative while the process lives

    def close(self):
        self._pool.shutdown(wait=False)
        self._db.close()


def _code_version() -> str:
    """Digest of the science code the result depends on (coarse but stable)."""
    import engine.lib.tsunami as tsu
    from pathlib import Path as _P
    h = hashlib.sha256()
    for f in sorted(_P(tsu.__file__).parent.glob("*.py")):
        h.update(f.read_bytes())
    return h.hexdigest()[:16]


__all__ = ["HeavyJobRunner", "JobCancelled"]
