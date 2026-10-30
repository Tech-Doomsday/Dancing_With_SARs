"""Run execution — single-flight job runner over the REAL registry path, with the heavy lane.

Lifecycle mirrors the reference app's runner (queued -> running -> ok | error,
progress polling, best-effort persistence). Normal-lane pipelines run inline (workers=0) in one
worker thread; heavy-lane pipelines (manifest execution: heavy) come back from the registry as
PENDING and are executed through engine.jobs.HeavyJobRunner (own deadline, progress file,
content-addressed result cache) — the run waits for them, so the UI keeps one run record.
Every finished verdict is calibrated (4.2) and persisted into verdicts.db via 4.3+4.5.

Task §5.3 additions: a scan (POST /api/scans) is a run — the same runs row, the same GET
/api/runs/{id} polling path, the same persistence tail. The tail (routing + pipelines -> heavy
lane -> calibrate -> link -> verdicts.db -> Supabase) is extracted into `_run_registered` and
shared by `_execute` (bundle runs) and `_execute_scan` (location scans) — start_run behaviour is
unchanged (tests/test_api.py pins it). The granule-availability search (§5.5) is metadata-only
and runs on the SAME single worker pool (single-flight-safe; it simply queues behind a scan).
"""
from __future__ import annotations

import base64
import dataclasses
import json
import os
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from engine.common.bundle_io import load_bundle, save_bundle
from engine.common.frozen import FrozenDict
from engine.common.result import PipelineResult
from engine.registry import Registry, coerce_result
from engine.verdict import VerdictStore, calibrate, calibrate_all, link
from engine.verdict.cloud_cache import CloudCache

from .store import RunsStore

_MAX_COMPLETED_IN_MEMORY = 10


def scans_enabled() -> bool:
    """DEC9: scans are off unless the operator enables them (an unauthenticated scan fetches
    hundreds of MB from ASF). Presence-only truthy set, matching common engine conventions."""
    return str(os.environ.get("NISAR_ENABLE_SCANS", "")).strip().lower() in ("1", "true", "yes", "on")


NO_EARTHDATA_MESSAGE = ("no Earthdata credentials in the environment "
                        "(set EARTHDATA_TOKEN, or EARTHDATA_USERNAME and EARTHDATA_PASSWORD)")


def earthdata_present() -> bool:
    """Presence only (never values): the same rule EarthdataAuth.available applies."""
    from engine.retrieval.auth.secrets import earthdata_auth
    from engine.retrieval.settings import load_settings
    load_settings()                      # make .env entries visible to os.environ
    return earthdata_auth().available


class ScanError(RuntimeError):
    """A scan that must not start. `status` is the HTTP status the route answers with."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


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
    def _fresh_run_state(self, kind: str) -> dict:
        return {"status": "queued", "progress": {"stage": "queued"},
                "error": None, "results": [], "kind": kind}

    def _begin_run(self, kind: str, bundle_id: str, tile: str, pipelines: list, params: dict) -> str:
        """D10 first: rows that claim queued/running but have no live state in THIS process are
        stale (the engine restarted mid-run) — mark them error so single-flight cannot 409 forever."""
        keep = {rid for rid, s in self._states.items() if s.get("status") in ("queued", "running")}
        self.store.reset_stale_runs(keep)
        active = self.store.active_run()
        if active:
            raise RuntimeError(
                f"a run is already active ({active['id']}) — wait for it to finish (single-flight, "
                "several-hundred-MB arrays make concurrent runs a memory hazard)")
        run_id = f"run-{uuid.uuid4().hex[:12]}"
        self.store.create_run(run_id, bundle_id, tile, pipelines, params, kind=kind)
        self._states[run_id] = self._fresh_run_state(kind)
        self._evict_old()
        return run_id

    def start_run(self, bundle_id: str, pipelines: list[str], params: dict | None) -> str:
        bundle_path = self.bundles_dir / bundle_id
        if not (bundle_path / "bundle.json").exists():
            raise FileNotFoundError(f"unknown bundle: {bundle_id}")
        tile = ""
        try:
            meta = json.loads((bundle_path / "bundle.json").read_text())
            tile = str((meta.get("tile") or {}).get("id") or "")
        except (OSError, json.JSONDecodeError):
            pass
        run_id = self._begin_run("bundle", bundle_id, tile, pipelines, params or {})
        self._pool.submit(self._execute, run_id, bundle_id, bundle_path, pipelines, params or {})
        return run_id

    def start_scan(self, *, lon: float, lat: float, as_of: str | None = None,
                   lookback_days: float | None = None,
                   retriever_factory=None) -> str:
        """§5.3: scan = tile_at -> build_scene_bundle -> save_bundle -> the SAME persistence tail.
        Guards run here (route thread); the work runs on the single worker thread. The retriever
        is constructed through `retriever_factory` (default NisarClient; tests inject a fake) and
        closed in finally."""
        if not scans_enabled():
            raise ScanError(403, "scans are disabled on this engine (set NISAR_ENABLE_SCANS=1)")
        if not earthdata_present():
            raise ScanError(503, NO_EARTHDATA_MESSAGE)
        run_id = self._begin_run("scan", "", "", ["*"], {"scan": {
            "lon": lon, "lat": lat, "as_of": as_of, "lookback_days": lookback_days}})
        self._pool.submit(self._execute_scan, run_id, lon, lat, as_of, lookback_days,
                          retriever_factory)
        return run_id

    def granule_search(self, *, lon: float, lat: float, products: list[str],
                       t0: str, t1: str, retriever_factory=None) -> dict:
        """§5.5: run ONLY P01's search + grouping half for one tile/window. Metadata-only (no
        fetch, no clip) — not gated by NISAR_ENABLE_SCANS (DEC9) — but it needs Earthdata
        credentials (ASF search) and runs on the SAME single worker pool, so it can never race
        a scan's retriever (single-flight-safe by construction)."""
        if not earthdata_present():
            raise ScanError(503, NO_EARTHDATA_MESSAGE)
        fut = self._pool.submit(self._granule_search_sync, lon, lat, products, t0, t1,
                                retriever_factory)
        return fut.result(timeout=600)

    def granule_search_bbox(self, *, bbox: tuple, levels: list[str], t0: str, t1: str,
                            product_names: dict[str, str], max_results: int, fresh: bool,
                            retriever_factory=None) -> dict:
        """E8 (Granule Explorer): bbox granule availability — the same single worker pool, the
        same metadata-only rules as granule_search. The route validates the box; this method answers
        503 without credentials, then runs the real search and enrichment."""
        if not earthdata_present():
            raise ScanError(503, NO_EARTHDATA_MESSAGE)
        fut = self._pool.submit(self._granule_search_bbox_sync, bbox, levels, t0, t1,
                                product_names, max_results, fresh, retriever_factory)
        return fut.result(timeout=600)

    def _granule_search_bbox_sync(self, bbox, levels, t0, t1, product_names, max_results, fresh,
                                  retriever_factory) -> dict:
        from .granules_bbox import search_bbox_sync
        factory = retriever_factory or _default_retriever_factory()
        retriever = factory()
        try:
            return search_bbox_sync(bbox=bbox, levels=levels, t0=t0, t1=t1,
                                    product_names=product_names, max_results=max_results,
                                    fresh=fresh, retriever=retriever)
        finally:
            try:
                retriever.close()
            except Exception:
                pass

    # ------------------------------------------------------------------ reads
    @staticmethod
    def _with_tier(result: dict) -> dict:
        """D4: run-state results carry the 4.2 tier (they used to be tier-less until persisted;
        the UI must not re-derive tiers from statuses). raw_p passes through untouched."""
        return calibrate(dict(result))

    def get_state(self, run_id: str) -> dict | None:
        row = self.store.get_run(run_id)
        with self._lock:
            state = self._states.get(run_id)
            if state is not None:
                out = {**state, "id": run_id}
                out["results"] = [self._with_tier(r) for r in state.get("results", [])]
                if row is not None:   # tile/bundle_id/kind may be set by the worker after the state was created
                    out.setdefault("kind", row.get("kind") or "bundle")
                    out.setdefault("tile", row.get("tile") or "")
                    out.setdefault("bundle_id", row.get("bundle_id") or "")
                return out
        if row is None:
            return None
        return {"id": run_id, "status": row["status"], "progress": {}, "error": row["error"],
                "results": [self._with_tier(r) for r in self.store.get_verdicts(run_id)],
                "kind": row.get("kind") or "bundle",
                "tile": row.get("tile") or "",
                "bundle_id": row.get("bundle_id") or ""}

    def list_states(self) -> list:
        return [{"id": r["id"], "status": r["status"], "kind": r.get("kind") or "bundle",
                 "tile": r.get("tile") or "", "bundle_id": r.get("bundle_id") or ""}
                for r in self.store.list_runs(20)]

    # ------------------------------------------------------------------ exec (bundle runs)
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
            # NOTE: params are merged into the request triggers above (audit in the runs
            # table + detail.params in every result). The bundle on disk is never modified.
            self._run_registered(run_id, bundle_id, bundle_path, bundle, pipelines, params, t0)
        except Exception as ex:  # noqa: BLE001 — a failed run is a status, not a crash
            err = f"{type(ex).__name__}: {ex}"
            with self._lock:
                self._states[run_id].update({"status": "error", "error": err,
                                             "progress": {"stage": "error"}})
            self.store.set_status(run_id, "error", error=err[:500], duration_s=time.monotonic() - t0)
            traceback.print_exc()
            self._write_replay_artifacts(run_id, bundle_id, pipelines, params, self._states[run_id])

    # ------------------------------------------------------------------ exec (scans)
    def _execute_scan(self, run_id: str, lon: float, lat: float, as_of: str | None,
                      lookback_days: float | None, retriever_factory) -> None:
        """The §5.3 worker path: tile_at -> retriever -> build_scene_bundle -> save_bundle ->
        _run_registered. Failure is a run status + the exception text, never swallowed."""
        with self._lock:
            self._states[run_id]["status"] = "running"
            self._states[run_id]["progress"] = {"stage": "resolving tile", "tile": ""}
        self.store.set_status(run_id, "running")
        t0 = time.monotonic()
        retriever = None
        try:
            from engine.common.ancillary import DirectoryAncillary, NullAncillary
            from engine.common.chain import STEPS, build_scene_bundle
            from engine.common.config import data_root, load_common_config
            from engine.retrieval.settings import tile_cell_size_m
            from engine.tiling.mgrs import tile_at

            tile = tile_at(lon, lat, size=tile_cell_size_m())
            with self._lock:
                self._states[run_id]["progress"] = {
                    "stage": f"P01 {STEPS[0].name}", "step_index": 1, "step_total": len(STEPS),
                    "tile": tile.id}
            self.store.set_run_meta(run_id, tile=tile.id)

            factory = retriever_factory or _default_retriever_factory()
            retriever = factory()
            droot = data_root()
            ancillary = (DirectoryAncillary(droot / "ancillary")
                         if (droot / "ancillary").is_dir() else NullAncillary())
            cfg = load_common_config()

            def observer(step, ctx, record) -> None:      # chain steps verbatim (§5.3 item 5)
                idx = next((i for i, s in enumerate(STEPS) if s.code == step.code), 0) + 1
                with self._lock:
                    self._states[run_id]["progress"] = {
                        "stage": f"{step.code} {step.name}", "step_index": idx,
                        "step_total": len(STEPS), "tile": tile.id}

            bundle = build_scene_bundle(tile, retriever, as_of=as_of, ancillary=ancillary,
                                        cfg=cfg, lookback_days=lookback_days, observer=observer)
            with self._lock:
                self._states[run_id]["progress"] = {"stage": "saving bundle", "tile": tile.id}
            bundle_path = save_bundle(bundle, self.bundles_dir)
            self.store.set_run_meta(run_id, bundle_id=bundle.bundle_id)
            # engine defaults only: params={} keeps the Supabase cache path open; the scan
            # request itself lives in the runs row's params under the "scan" key (audit).
            self._run_registered(run_id, bundle.bundle_id, bundle_path, bundle, None, {}, t0)
        except Exception as ex:  # noqa: BLE001 — a failed scan is a run status + the reason
            err = f"{type(ex).__name__}: {ex}"
            with self._lock:
                self._states[run_id].update({"status": "error", "error": err,
                                             "progress": {"stage": "error"}})
            self.store.set_status(run_id, "error", error=err[:500], duration_s=time.monotonic() - t0)
            traceback.print_exc()
            self._write_replay_artifacts(run_id, "", [], {"scan": {
                "lon": lon, "lat": lat, "as_of": as_of, "lookback_days": lookback_days}},
                self._states[run_id])
        finally:
            if retriever is not None:
                try:
                    retriever.close()
                except Exception:
                    pass

    # ------------------------------------------------------------------ shared tail
    def _run_registered(self, run_id: str, bundle_id: str, bundle_path: Path,
                        bundle, pipelines: list[str] | None, params: dict, t0: float) -> None:
        """The one tail both run kinds share: routing + pipelines -> heavy lane -> verdicts.db ->
        Supabase -> status ok. `pipelines=None` means every manifest (DEC8: a scan shows the full
        honest picture). Behaviour for bundle runs is unchanged (test_api.py pins it)."""
        with self._lock:
            self._states[run_id]["progress"] = {"stage": "routing + pipelines", "tile": bundle.tile.id}
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
        with self._lock:
            self._states[run_id]["progress"] = {"stage": "persisting", "tile": bundle.tile.id}
        for res in results:
            self.store.save_verdict(run_id, res)
        # canonical verdict persistence: calibrate (4.2) -> link (4.3) -> verdicts.db (4.5)
        rows = calibrate_all([_row_of(r) for r in results])
        links = link(rows)["events"]
        self.verdicts.write_run(rows, run_id=run_id, links=links)
        # 4.6: mirror the same rows into the Supabase cache (best-effort; the report says what happened)
        # Phase 2: the tile's own position rides along so the globe can pin cached areas.
        location = {"center_lon": float(bundle.tile.center_lonlat[0]),
                    "center_lat": float(bundle.tile.center_lonlat[1]),
                    "bbox_lonlat": [float(v) for v in bundle.tile.bbox_lonlat]}
        cache_report = self.cache.write_run(rows, run_id=run_id, bundle_id=bundle_id,
                                            tile=bundle.tile.id, params=params, location=location)
        with self._lock:
            self._states[run_id].update({
                "status": "ok", "results": results, "cache": cache_report,
                "tile": bundle.tile.id, "bundle_id": bundle_id,
                "progress": {"stage": "done", "tile": bundle.tile.id}})
        self.store.set_status(run_id, "ok", duration_s=time.monotonic() - t0)
        self._write_replay_artifacts(run_id, bundle_id, pipelines or [], params,
                                     self._states[run_id])

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

    # ------------------------------------------------------------------ granule search (§5.5)
    def _granule_search_sync(self, lon: float, lat: float, products: list[str],
                             t0: str, t1: str, retriever_factory) -> dict:
        """P01's search + grouping half only. Calls the REAL code (p01.resolve, the same function
        P01.run uses) with a BuildContext that carries the flags — what the UI
        shows is literally the resolution the rest of the app depends on."""
        from engine.common import p01_granule_resolution as p01
        from engine.common.config import load_common_config
        from engine.common.context import BuildContext, BuildRequest, iso
        from engine.common.grids import make_tile_grids
        from engine.retrieval.settings import tile_cell_size_m
        from engine.tiling.mgrs import tile_at

        tile = tile_at(lon, lat, size=tile_cell_size_m())
        cfg = load_common_config()
        grids = make_tile_grids(tile, cfg.grids)
        as_of = datetime.now(timezone.utc).replace(microsecond=0)
        t0_dt = _parse_utc(t0)
        t1_dt = _parse_utc(t1)
        ctx = BuildContext(BuildRequest(tile, as_of, t0_dt, t1_dt), cfg, grids, retriever=None)
        factory = retriever_factory or _default_retriever_factory()
        retriever = factory()
        try:
            ctx.retriever = retriever
            res = p01.resolve(ctx, products)
        finally:
            try:
                retriever.close()
            except Exception:
                pass

        stacks: dict = {}
        for key in sorted(set(res.gunw) | set(res.gcov) | set(res.goff)):
            groups = (res.gunw.get(key) or []) + (res.gcov.get(key) or []) + (res.goff.get(key) or [])
            meta = next((g for m in groups for g in m.granules), None)   # P01's own tag for this stack
            stacks[key] = {
                "direction": meta.direction if meta else None,
                "track": meta.track if meta else None,
                "gunw": [_pair_dict(p) for p in res.gunw.get(key, [])],
                "gcov": [_epoch_dict(e) for e in res.gcov.get(key, [])],
                "goff": [_pair_dict(p) for p in res.goff.get(key, [])],
            }
        # the same final flags P01.run raises, so an empty answer says WHY in the engine's own text
        if not any(groups for s in stacks.values() for groups in (s["gunw"], s["gcov"], s["goff"])) \
                and not res.search_errors:
            ctx.flag("no_granules", "warn", "P01", "no NISAR granules cover this tile in the window",
                     t0=iso(t0_dt), t1=iso(t1_dt))
        if res.dropped_older:
            ctx.flag("older_versions_dropped", "info", "P01",
                     f"{res.dropped_older} superseded processing version(s) ignored")
        return {
            "tile": tile.to_dict(),
            "window": {"t0": iso(t0_dt), "t1": iso(t1_dt)},
            "stacks": stacks,
            "search_errors": dict(res.search_errors),
            "search_degraded": res.search_degraded,
            "dropped_older": res.dropped_older,
            "flags": [{"code": f.code, "severity": f.severity, "step": f.step, "message": f.message}
                      for f in ctx.flags],
        }

    def close(self):
        self._pool.shutdown(wait=False)
        self.heavy.close()
        self.verdicts.close()


def _default_retriever_factory():
    """Production seam: a fresh NisarClient per scan/search (the hub's pattern — the client owns
    threads and an event loop, so one instance per job, closed in finally)."""
    from engine.retrieval.clients.nisar_client import NisarClient
    from engine.retrieval.settings import load_settings

    def factory():
        load_settings()
        return NisarClient()

    return factory


def _parse_utc(s: str) -> datetime:
    from engine.common.context import parse_time
    return parse_time(s)


def _pair_dict(p) -> dict:
    g0 = p.granules[0] if p.granules else None
    return {
        "ref_time": p.ref_time.isoformat() if p.ref_time else None,
        "sec_time": p.sec_time.isoformat() if p.sec_time else None,
        "dt_days": p.dt_days,
        "flipped": p.flipped,
        "resolved": p.resolved,
        "granule_ids": [m.ref.granule_id for m in p.granules],
        "frame": (g0.ref.frame if g0 is not None else None),
        "footprint": (g0.ref.footprint if g0 is not None else None),   # first granule's outer ring (approximation, documented)
    }


def _epoch_dict(e) -> dict:
    g0 = e.granules[0] if e.granules else None
    return {
        "time": e.time.isoformat() if e.time else None,
        "granule_ids": [m.ref.granule_id for m in e.granules],
        "frame": (g0.ref.frame if g0 is not None else None),
        "footprint": (g0.ref.footprint if g0 is not None else None),
    }


def _row_of(result: dict) -> dict:
    """Result dict -> verdict row for the 4.x chain (fields align with engine.verdict.collect)."""
    return {**result, "detail": result.get("detail") or {}}


def _error_result(pipeline: str, message: str) -> PipelineResult:
    return PipelineResult(pipeline=pipeline, tile="", epoch=None, status="ERROR",
                          summary=f"{pipeline}: {message}", raw_p=None, confidence=None,
                          warnings=[message])
