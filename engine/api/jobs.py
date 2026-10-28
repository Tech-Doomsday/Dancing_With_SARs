"""Run execution — single-flight job runner over the REAL registry path, with the heavy lane.

Lifecycle mirrors the reference app's runner (queued -> running -> ok | error,
progress polling, best-effort persistence). Normal-lane pipelines run inline (workers=0) in one
worker thread; heavy-lane pipelines (manifest execution: heavy) come back from the registry as
PENDING and are executed through engine.jobs.HeavyJobRunner (own deadline, progress file,
content-addressed result cache) — the run waits for them, so the UI keeps one run record.
Every finished verdict is calibrated (4.2) and persisted into verdicts.db via 4.3+4.5.
"""
from __future__ import annotations

import base64
import dataclasses
import json
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from engine.common.bundle_io import load_bundle
from engine.common.frozen import FrozenDict
from engine.common.result import PipelineResult
from engine.registry import Registry, coerce_result
from engine.verdict import VerdictStore, calibrate_all, link
from engine.verdict.cloud_cache import CloudCache

from .store import RunsStore

_MAX_COMPLETED_IN_MEMORY = 10


def _apply_params(bundle, params: dict):
    """Merge run params into the bundle's request triggers (`eq_params` namespace).

    Final-prompt fix (params were recorded but silently ignored): the pipelines read
    their parameters from `bundle.request.triggers["eq_params"]` (EqParams.from_triggers),
    so a run's params must ride the request. A stored SceneBundle stays frozen on disk —
    the override lives only in this run's in-memory copy, is recorded in the runs table
    (audit) and lands in each result's detail.params, so what was applied is always
    inspectable. Baked triggers keep their values for keys the caller did not send.
    """
    if not params:
        return bundle
    triggers = dict(bundle.request.triggers or {})
    eq = dict(triggers.get("eq_params") or {})
    eq.update(params)
    triggers["eq_params"] = eq
    request = dataclasses.replace(bundle.request, triggers=FrozenDict(triggers))
    return dataclasses.replace(bundle, request=request)


class JobRunner:
    def __init__(self, store: RunsStore, bundles_dir: Path, data_dir: Path | None = None,
                 cache: CloudCache | None = None):
        self.store = store
        self.bundles_dir = Path(bundles_dir)
        self.data_dir = Path(data_dir) if data_dir else self.bundles_dir.parent
        from engine.jobs import HeavyJobRunner
        self.heavy = HeavyJobRunner(data_dir=self.data_dir / "jobs",
                                    registry=Registry(bundles_dir=self.bundles_dir),
                                    bundles_dir=self.bundles_dir)
        self.verdicts = VerdictStore(self.data_dir / "verdicts.db")
        self.cache = cache if cache is not None else CloudCache.from_env()   # Supabase mirror (4.6)
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="engine-run")
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
        with self._lock:
            self._states[run_id]["status"] = "running"
            self._states[run_id]["progress"] = {"stage": "loading bundle"}
        self.store.set_status(run_id, "running")
        t0 = time.monotonic()
        try:
            bundle = load_bundle(bundle_path, mmap=True)
            bundle = _apply_params(bundle, params)          # run params now REACH the pipelines
            with self._lock:
                self._states[run_id]["progress"] = {"stage": "routing + pipelines", "tile": bundle.tile.id}
            # NOTE: params are merged into the request triggers above (audit in the runs
            # table + detail.params in every result). The bundle on disk is never modified.
            registry = Registry(bundles_dir=self.bundles_dir)
            tile_run = registry.run_tile(
                bundle.tile, retriever=None, bundle=bundle,
                names=pipelines, workers=0,
            )
            results = []
            for name, res in tile_run.results.items():
                if res.status == "PENDING" and res.detail.get("lane") == "heavy":
                    res = self._run_heavy(run_id, name, bundle_path)
                results.append(res.to_dict() if isinstance(res, PipelineResult) else dict(res))
            for res in results:
                self.store.save_verdict(run_id, res)
            # canonical verdict persistence: calibrate (4.2) -> link (4.3) -> verdicts.db (4.5)
            rows = calibrate_all([_row_of(r) for r in results])
            links = link(rows)["events"]
            self.verdicts.write_run(rows, run_id=run_id, links=links)
            # 4.6: mirror the same rows into the Supabase cache (best-effort; the report says what happened)
            cache_report = self.cache.write_run(rows, run_id=run_id, bundle_id=bundle_id,
                                                tile=bundle.tile.id, params=params)
            with self._lock:
                self._states[run_id].update({"status": "ok", "results": results, "cache": cache_report,
                                             "progress": {"stage": "done", "tile": bundle.tile.id}})
            self.store.set_status(run_id, "ok", duration_s=time.monotonic() - t0)
            self._write_replay_artifacts(run_id, bundle_id, pipelines, params, self._states[run_id])
        except Exception as ex:  # noqa: BLE001 — a failed run is a status, not a crash
            err = f"{type(ex).__name__}: {ex}"
            with self._lock:
                self._states[run_id].update({"status": "error", "error": err,
                                             "progress": {"stage": "error"}})
            self.store.set_status(run_id, "error", error=err[:500], duration_s=time.monotonic() - t0)
            traceback.print_exc()
            self._write_replay_artifacts(run_id, bundle_id, pipelines, params, self._states[run_id])

    def _write_replay_artifacts(self, run_id: str, bundle_id: str, pipelines: list,
                                params: dict, state: dict) -> None:
        """plan §13 — every run writes `data/runs/<run_id>/` so any verdict can be
        replayed offline (load_bundle + run(bundle)) without the API:

            params.json   bundle id, pipelines, params, status (+ how to replay)
            result.json   the full run state — b64 grids replaced by .npy pointers
            layers/*.npy  decoded evidence grids, one file per pipeline+layer

        Best-effort: a write failure is printed, never fails the run.
        """
        root = self.data_dir / "runs" / run_id
        layers_dir = root / "layers"
        try:
            layers_dir.mkdir(parents=True, exist_ok=True)
            (root / "params.json").write_text(json.dumps({
                "run_id": run_id, "bundle_id": bundle_id, "pipelines": pipelines,
                "params": params, "status": state.get("status"), "error": state.get("error"),
                "replay": ("load_bundle(<bundles_dir>/<bundle_id>) then call each pipeline's "
                           "verdict.run(bundle) — or: python tools/replay_run.py " + run_id),
            }, indent=2))
            serialisable = {"id": run_id, "status": state.get("status"),
                            "progress": state.get("progress"), "error": state.get("error"),
                            "results": []}
            for res in state.get("results", []):
                res = dict(res)
                detail = dict(res.get("detail") or {})
                grid_layers = {}
                for name, payload in ((res.get("detail") or {}).get("layers") or {}).items():
                    try:
                        arr = np.frombuffer(base64.b64decode(payload["data_b64"]),
                                            dtype=payload["dtype"]).reshape(payload["shape"])
                        fname = f"layers/{res.get('pipeline', 'pipeline')}__{name}.npy"
                        np.save(root / fname, arr)
                        grid_layers[name] = {**payload, "data_b64": f"<see {fname}>",
                                             "npy": fname}
                    except Exception:
                        grid_layers[name] = {"name": name, "note": "layer could not be decoded for replay"}
                detail["layers"] = grid_layers
                res["detail"] = detail
                serialisable["results"].append(res)
            (root / "result.json").write_text(json.dumps(serialisable, indent=2, default=str))
        except Exception:
            traceback.print_exc()

    def _run_heavy(self, run_id: str, pipeline: str, bundle_path: Path):
        """Submit the deferred heavy pipeline to the jobs lane and wait for its verdict."""
        with self._lock:
            self._states[run_id]["progress"] = {"stage": f"heavy lane: {pipeline}"}
        out = self.heavy.submit(pipeline, bundle_path)
        job_id = out.get("job_id")
        if out.get("cached") and out.get("result"):
            return self._coerce_cached(out["result"], pipeline, bundle_path)
        if not job_id:
            return _error_result(pipeline, out.get("error") or "heavy submission failed")
        while True:
            state = self.heavy.get_state(job_id)
            with self._lock:
                self._states[run_id]["progress"] = {
                    "stage": f"heavy lane: {pipeline}", "job": job_id,
                    "fraction": (state.get("progress") or {}).get("fraction"),
                    "job_stage": (state.get("progress") or {}).get("stage")}
            if state.get("status") in ("ok", "error"):
                break
            time.sleep(0.5)
        if state.get("status") == "ok" and state.get("result"):
            return self._coerce_cached(state["result"], pipeline, bundle_path)
        return _error_result(pipeline, state.get("error") or "heavy job failed (no error recorded)")

    def _coerce_cached(self, result: dict, pipeline: str, bundle_path: Path) -> PipelineResult:
        try:
            bundle = load_bundle(bundle_path, mmap=False)
            return coerce_result(result, Registry(bundles_dir=self.bundles_dir).manifest(pipeline), bundle)
        except Exception:
            return _error_result(pipeline, "cached heavy result could not be normalised")

    def _evict_old(self) -> None:
        with self._lock:
            done = [rid for rid, s in self._states.items() if s["status"] in ("ok", "error")]
            if len(done) > _MAX_COMPLETED_IN_MEMORY:
                for rid in done[: len(done) - _MAX_COMPLETED_IN_MEMORY]:
                    self._states.pop(rid, None)

    def close(self):
        self._pool.shutdown(wait=False)
        self.heavy.close()
        self.verdicts.close()


def _row_of(result: dict) -> dict:
    """Result dict -> verdict row for the 4.x chain (fields align with engine.verdict.collect)."""
    return {**result, "detail": result.get("detail") or {}}


def _error_result(pipeline: str, message: str) -> PipelineResult:
    return PipelineResult(pipeline=pipeline, tile="", epoch=None, status="ERROR",
                          summary=f"{pipeline}: {message}", raw_p=None, confidence=None,
                          warnings=[message])
