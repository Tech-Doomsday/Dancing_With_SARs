"""Run execution — single-flight job runner over the REAL registry path.

Lifecycle mirrors the reference app's runner (queued -> running -> ok | error,
progress polling, best-effort persistence). v1 runs inline (workers=0) in one
worker thread: UI runs are single-user, and keeping them in-process makes the
fetch seam, flags and layer payloads observable. Swapping in a task queue
later is a drop-in — the runs table is already the queue's state shape
(docs/IMPLEMENTATION_PLAN.md 10.2, 14).
"""
from __future__ import annotations

import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from engine.common.bundle_io import load_bundle
from engine.registry import Registry

from .store import RunsStore

_MAX_COMPLETED_IN_MEMORY = 10


class JobRunner:
    def __init__(self, store: RunsStore, bundles_dir: Path, data_dir: Path | None = None):
        self.store = store
        self.bundles_dir = Path(bundles_dir)
        self.data_dir = Path(data_dir) if data_dir else self.bundles_dir.parent
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="eq-run")
        self._states: dict[str, dict] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ API
    def start_run(self, bundle_id: str, pipelines: list[str], params: dict | None) -> str:
        active = self.store.active_run()
        if active:
            raise RuntimeError(
                f"a run is already active ({active['id']}) — wait for it to finish (single-flight, "
                "several-hundred-MB arrays make concurrent runs a memory hazard)")
        run_id = f"run-{uuid.uuid4().hex[:12]}"
        bundle_path = self.bundles_dir / bundle_id
        if not (bundle_path / "bundle.json").exists():
            raise FileNotFoundError(f"unknown bundle: {bundle_id}")
        self.store.create_run(run_id, bundle_id, "", pipelines, params or {})
        self._states[run_id] = {"status": "queued", "progress": {"stage": "queued"},
                                "error": None, "results": []}
        self._evict_old()
        self._pool.submit(self._execute, run_id, bundle_id, bundle_path, pipelines, params or {})
        return run_id

    def get_state(self, run_id: str) -> dict | None:
        with self._lock:
            state = self._states.get(run_id)
            if state is not None:
                return {**state, "id": run_id}
        row = self.store.get_run(run_id)
        if row is None:
            return None
        return {"id": run_id, "status": row["status"], "progress": {}, "error": row["error"],
                "results": self.store.get_verdicts(run_id)}

    def list_states(self) -> list:
        return [{"id": r["id"], "status": r["status"]} for r in self.store.list_runs(20)]

    # ------------------------------------------------------------------ exec
    def _execute(self, run_id: str, bundle_id: str, bundle_path: Path,
                 pipelines: list[str], params: dict) -> None:
        import time as _time

        with self._lock:
            self._states[run_id]["status"] = "running"
            self._states[run_id]["progress"] = {"stage": "loading bundle"}
        self.store.set_status(run_id, "running")
        t0 = _time.monotonic()
        try:
            bundle = load_bundle(bundle_path, mmap=True)
            with self._lock:
                self._states[run_id]["progress"] = {"stage": "routing + pipelines", "tile": bundle.tile.id}
            # NOTE: params are recorded for audit; they take effect when a run
            # REBUILDS the bundle (registry.run_tile with a retriever passes
            # triggers into build_scene_bundle). A pre-built bundle is frozen —
            # its baked triggers win — so the UI params form ships with the
            # rebuild path in M5 (docs/IMPLEMENTATION_PLAN.md 10.2, 11.4).
            registry = Registry(bundles_dir=self.bundles_dir)
            tile_run = registry.run_tile(
                bundle.tile, retriever=None, bundle=bundle,
                names=pipelines, workers=0,
            )
            results = [r.to_dict() for r in tile_run.results.values()]
            for res in results:
                self.store.save_verdict(run_id, res)
            with self._lock:
                self._states[run_id].update({"status": "ok", "results": results,
                                             "progress": {"stage": "done", "tile": bundle.tile.id}})
            self.store.set_status(run_id, "ok", duration_s=_time.monotonic() - t0)
        except Exception as ex:  # noqa: BLE001 — a failed run is a status, not a crash
            err = f"{type(ex).__name__}: {ex}"
            with self._lock:
                self._states[run_id].update({"status": "error", "error": err,
                                             "progress": {"stage": "error"}})
            self.store.set_status(run_id, "error", error=err[:500], duration_s=_time.monotonic() - t0)
            traceback.print_exc()

    def _evict_old(self) -> None:
        with self._lock:
            done = [rid for rid, s in self._states.items() if s["status"] in ("ok", "error")]
            if len(done) > _MAX_COMPLETED_IN_MEMORY:
                for rid in done[: len(done) - _MAX_COMPLETED_IN_MEMORY]:
                    self._states.pop(rid, None)
