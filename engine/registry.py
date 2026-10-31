"""7 · Cross-cutting registry — scans `engine/pipelines/*`, reads manifests, runs applicable pipelines in a process pool.

    reg = Registry()                                         # scans engine/pipelines/*/manifest.yaml
    run = reg.run_tile(tile, retriever, as_of=..., ancillary=..., triggers=..., event_window=...)
    run.results["landslide"]  ->  PipelineResult(pipeline, tile, epoch, raw_p, status, evidence, ...)

What it does, in order
    1. scan      every `engine/pipelines/<name>/manifest.yaml` (folders starting with `_` are templates and skipped).
    2. bundle    P01..P13 once for the tile (`engine.common.build_scene_bundle`), with the measurement codes the
                 selected manifests declare; the bundle is stored content-addressed under `<data>/bundles/` so every
                 worker memory-maps the SAME read-only arrays instead of copying them.
    3. route     the bundle's own P12 decision per pipeline: yes / no / unknown.
                    no       -> status NOT_APPLICABLE   (never launched)
                    unknown  -> status NOT_RUN          (never launched unless registry.run_unknown), reasons + needs kept
                    yes      -> gate, then launch
    4. gate      manifest `min_epochs` against the bundle's data depth: too little history -> INSUFFICIENT_EVIDENCE is
                 answered here, without launching a worker (registry.gate_min_epochs). An EMPTY bundle gates every
                 pipeline the same way.
    5. lane      the manifest declares its execution lane: `execution: normal` (spawn pool, per-manifest `timeout_s`
                 or registry.task_timeout_s) or `execution: heavy` (the wave-solver class of work: own timeout,
                 progress file and a content-addressed result cache — engine/jobs). A heavy pipeline encountered in a
                 normal run_tile is DEFERRED, not launched: it answers status PENDING with the lane annotated, and the
                 caller submits it to the jobs runner (engine/jobs/runner.py). run_tile(..., heavy="run") executes it
                 in this process (used by the jobs runner itself and by tests).
    6. run       a `spawn` process pool (never fork: the retrieval client owns threads and an event loop). Each task has a
                 deadline; a task that exceeds it is reported TIMEOUT -> status ERROR, the pool is recycled and the tasks
                 that were only collateral damage are re-run (alone, so a crash can be pinned on its pipeline).
                 One pipeline crashing, raising or hanging never affects another pipeline's result.
    7. collect   every return is validated against the manifest's `status_values` and normalised to `PipelineResult`
                 (engine/common/result.py — the typed shape the UI renders).

Pipeline entry point (the contract this module defines):
    engine/pipelines/<name>/verdict.py   defines   run(bundle: SceneBundle, providers=None)
                                              -> PipelineResult | Mapping | (raw_p, status, evidence)
The registry calls run(bundle) — the optional `providers` mapping (injected event/bathy/gauge sources, a progress
callback) is how TESTS and the API hand a pipeline offline or alternate data sources; see each pipeline's fetch.py.
Extra DATA beyond the bundle comes only through the pipeline's own fetch.py, which may import only
engine.retrieval.clients.adapters.* — never NisarClient, never another pipeline, never the P01-P13 chain.
A folder without a `run()` yields status NOT_MODELLED with a warning (it exists but is not implemented yet), not a crash.
Statuses a pipeline may return are those in its manifest's `status_values`; the registry itself adds NOT_APPLICABLE,
NOT_RUN, NOT_MODELLED, PENDING and ERROR. Apart from its own fetch.py (adapters only) a pipeline sees ONLY the bundle:
no retriever, no other pipeline's result — event-level conclusions live in engine/verdict/link_events.py.
"""
from __future__ import annotations

import concurrent.futures as cf
import dataclasses
import importlib
import multiprocessing as mp
import os
import sys
import time
import traceback
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import yaml

from .common.config import CommonConfig, data_root, load_common_config
from .common.result import REGISTRY_STATUSES, PipelineResult, plain
from .common.scene_bundle import RoutingDecision

ENTRY_MODULE = "verdict"
ENTRY_FUNC = "run"


# ---------------------------------------------------------------------------------------------
# manifests
# ---------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Manifest:
    name: str
    code: str
    path: Path
    sensor: str = ""
    applies: str = ""
    uses_codes: tuple = ()
    fetch_codes: tuple = ()
    min_epochs: int | None = None
    status_values: tuple = ()
    execution: str = "normal"               # normal | heavy (engine/jobs lane: own timeout + progress + cache)
    timeout_s: float | None = None          # per-manifest deadline; None -> registry.task_timeout_s
    needs_sar: bool = True                  # False = the pipeline can produce evidence without NISAR data
    raw: Mapping = field(default_factory=dict, compare=False, hash=False)

    @property
    def measurement_codes(self) -> tuple:
        return tuple(c for c in self.uses_codes if c[:1] in ("M", "P"))

    @property
    def deadline_s(self) -> float:
        return float(self.timeout_s) if self.timeout_s else 900.0


def load_manifest(path: Path) -> Manifest:
    raw = yaml.safe_load(path.read_text()) or {}
    name = str(raw.get("name") or path.parent.name)
    if name != path.parent.name:
        raise ValueError(f"{path}: manifest name {name!r} differs from its folder {path.parent.name!r}")
    me = raw.get("min_epochs")
    if me is not None and (isinstance(me, bool) or not isinstance(me, int) or me < 0):
        raise ValueError(f"{path}: min_epochs must be a non-negative integer or null, got {me!r}")
    execution = str(raw.get("execution") or "normal").lower()
    if execution not in ("normal", "heavy"):
        raise ValueError(f"{path}: execution must be 'normal' or 'heavy', got {execution!r}")
    ts = raw.get("timeout_s")
    if ts is not None and (isinstance(ts, bool) or not isinstance(ts, (int, float)) or ts <= 0):
        raise ValueError(f"{path}: timeout_s must be a positive number or null, got {ts!r}")
    return Manifest(name, str(raw.get("code", "")), path, str(raw.get("sensor") or ""), str(raw.get("applies") or ""),
                    tuple(str(c) for c in raw.get("uses_codes") or ()), tuple(str(c) for c in raw.get("fetch_codes") or ()),
                    me, tuple(str(s) for s in raw.get("status_values") or ()),
                    execution, (float(ts) if ts is not None else None), bool(raw.get("needs_sar", True)), raw)


def scan_pipelines(pipelines_dir: Path | str) -> tuple:
    """-> (manifests sorted by code, errors {folder: message}). A broken manifest is reported, not fatal."""
    out, errors = [], {}
    root = Path(pipelines_dir)
    for d in sorted(root.iterdir()) if root.is_dir() else []:
        if not d.is_dir() or d.name.startswith(("_", ".")) or not (d / "manifest.yaml").exists():
            continue
        try:
            out.append(load_manifest(d / "manifest.yaml"))
        except Exception as ex:
            errors[d.name] = f"{type(ex).__name__}: {ex}"
    return sorted(out, key=lambda m: (m.code, m.name)), errors


# ---------------------------------------------------------------------------------------------
# result schema (what 4.1 collect receives) — PipelineResult, EvidenceItem, LayerRef and Source
# live in engine/common/result.py (the typed shape the UI renders) and are re-exported here.
# ---------------------------------------------------------------------------------------------
def _plain(v: Any) -> Any:
    return plain(v)


def coerce_result(raw: Any, manifest: Manifest, bundle) -> PipelineResult:
    """Normalise whatever a pipeline returned; a malformed return becomes status ERROR, never an exception."""
    base = dict(pipeline=manifest.name, tile=bundle.tile.id, epoch=bundle.epoch, bundle_id=bundle.bundle_id)
    try:
        if isinstance(raw, PipelineResult):
            d = raw.to_dict()
        elif isinstance(raw, Mapping):
            d = dict(raw)
            if "P" in d and "raw_p" not in d:
                d["raw_p"] = d.pop("P")
        elif isinstance(raw, (tuple, list)) and 2 <= len(raw) <= 3:
            d = {"raw_p": raw[0], "status": raw[1], "evidence": raw[2] if len(raw) == 3 else []}
        else:
            raise TypeError(f"run() returned {type(raw).__name__}, expected PipelineResult, mapping or (P, status, evidence)")
        status = d.get("status")
        allowed = set(manifest.status_values) | set(REGISTRY_STATUSES)
        if status not in allowed:
            raise ValueError(f"status {status!r} is not in the manifest's status_values {sorted(manifest.status_values)}")
        p = d.get("raw_p")
        if p is not None:
            p = float(p)
            if not (0.0 <= p <= 1.0) or p != p:
                raise ValueError(f"raw_p {p!r} outside [0, 1]")
        conf = d.get("confidence")
        if conf is not None:
            conf = float(conf)
            if not (0.0 <= conf <= 1.0) or conf != conf:
                raise ValueError(f"confidence {conf!r} outside [0, 1]")
        ev = d.get("evidence") or []
        if isinstance(ev, (str, Mapping)):
            ev = [ev]
        prov = d.get("provenance") or []
        if isinstance(prov, Mapping):
            prov = [prov]
        fields = {f.name for f in dataclasses.fields(PipelineResult)}
        extra = {k: _plain(v) for k, v in d.items() if k not in fields}
        res = PipelineResult(**{**base, "raw_p": p, "status": status, "confidence": conf,
                                "summary": str(d.get("summary") or ""),
                                "evidence": [_plain(e) for e in ev],
                                "layers": [_plain(l) for l in (d.get("layers") or [])],
                                "provenance": [_plain(s) for s in prov],
                                "data_used": list(d.get("data_used") or []),
                                "references_used": list(d.get("references_used") or []),
                                "diagnostics": dict(d.get("diagnostics") or {}),
                                "versions": dict(d.get("versions") or {}),
                                "warnings": [str(w) for w in d.get("warnings") or []],
                                "min_epochs_met": d.get("min_epochs_met"),
                                "detail": {**_plain(d.get("detail") or {}), **extra}})
        return res
    except Exception as ex:
        return PipelineResult(**base, raw_p=None, status="ERROR",
                              evidence=[{"error": f"malformed pipeline return: {type(ex).__name__}: {ex}"}])


def _synthetic(manifest: Manifest, bundle, status: str, reasons: list, **kw) -> PipelineResult:
    evidence = [dict(reason=r) if isinstance(r, str) else dict(r) for r in reasons]
    return PipelineResult(manifest.name, bundle.tile.id, bundle.epoch, status, evidence=evidence,
                          bundle_id=bundle.bundle_id, **kw)


# ---------------------------------------------------------------------------------------------
# worker side (top-level so a spawned interpreter can import it)
# ---------------------------------------------------------------------------------------------
def _worker(task: dict) -> dict:
    """Runs in a fresh process: load the bundle read-only, import ONLY this pipeline's module, call run()."""
    t0 = time.perf_counter()
    for p in task.get("sys_path") or []:
        if p not in sys.path:
            sys.path.insert(0, p)
    try:
        if task.get("bundle_path"):
            from .common.bundle_io import load_bundle
            bundle = load_bundle(task["bundle_path"], mmap=True)
        else:
            bundle = task["bundle"]
        mod = importlib.import_module(task["module"])
        fn = getattr(mod, ENTRY_FUNC, None)
        if fn is None:
            return {"kind": "no_entry", "runtime_s": time.perf_counter() - t0}
        raw = fn(bundle)
        if isinstance(raw, PipelineResult):
            raw = raw.to_dict()
        elif isinstance(raw, Mapping):
            raw = {k: _plain(v) for k, v in raw.items()}
        return {"kind": "ok", "raw": raw, "runtime_s": time.perf_counter() - t0}
    except BaseException as ex:                                   # SystemExit / KeyboardInterrupt from pipeline code too
        return {"kind": "raised", "error": f"{type(ex).__name__}: {ex}", "trace": traceback.format_exc(limit=8),
                "runtime_s": time.perf_counter() - t0}


@dataclass
class _Task:
    manifest: Manifest
    module: str
    attempts: int = 0
    solo: bool = False
    submitted: float = 0.0


@dataclass
class TileRun:
    tile: str
    bundle: Any
    bundle_path: Path | None
    results: dict
    routing: Any
    seconds: float
    scan_errors: dict = field(default_factory=dict)

    def by_status(self) -> dict:
        out: dict = {}
        for n, r in self.results.items():
            out.setdefault(r.status, []).append(n)
        return out


# ---------------------------------------------------------------------------------------------
class Registry:
    def __init__(self, pipelines_dir: Path | str | None = None, package: str = "engine.pipelines",
                 cfg: CommonConfig | None = None, sys_path: list | None = None, bundles_dir: Path | str | None = None):
        self.cfg = cfg or load_common_config()
        self.pipelines_dir = Path(pipelines_dir) if pipelines_dir else Path(__file__).resolve().parent / "pipelines"
        self.package = package
        self.sys_path = list(sys_path or [])
        self._bundles_dir = Path(bundles_dir) if bundles_dir else None
        self.manifests, self.scan_errors = scan_pipelines(self.pipelines_dir)

    # -- discovery --------------------------------------------------------------------------
    def names(self) -> list:
        return [m.name for m in self.manifests]

    def manifest(self, name: str) -> Manifest:
        for m in self.manifests:
            if m.name == name:
                return m
        raise KeyError(name)

    def is_modelled(self, name: str) -> bool:
        """True when the pipeline's verdict module defines run() — the SAME entry-point test the workers
        use (ENTRY_FUNC), so a count built on it can never disagree with what a run would report
        (a folder without run() ends as NOT_MODELLED). Import problems count as not modelled."""
        for p in self.sys_path:
            if p not in sys.path:
                sys.path.insert(0, p)
        try:
            mod = importlib.import_module(self.module_for(name))
        except Exception:
            return False
        return callable(getattr(mod, ENTRY_FUNC, None))

    def counts(self) -> dict:
        """Live, never stored: declared = manifests found on disk (the _template folder is not one),
        modelled = those that define run(). The numbers the UI shows; nothing hard-codes them."""
        modelled = [m.name for m in self.manifests if self.is_modelled(m.name)]
        return {"declared": len(self.manifests), "modelled": len(modelled), "modelled_names": modelled}

    def describe(self) -> list:
        """What GET /pipelines needs: one plain dict per manifest."""
        return [{"name": m.name, "code": m.code, "sensor": m.sensor, "applies": m.applies, "uses_codes": list(m.uses_codes),
                 "fetch_codes": list(m.fetch_codes), "min_epochs": m.min_epochs, "status_values": list(m.status_values),
                 "execution": m.execution, "timeout_s": m.timeout_s, "needs_sar": m.needs_sar}
                for m in self.manifests]

    def active_codes(self, names: list | None = None) -> set:
        sel = [m for m in self.manifests if names is None or m.name in names]
        return {c for m in sel for c in m.measurement_codes}

    @property
    def bundles_dir(self) -> Path:
        return self._bundles_dir or (data_root() / "bundles")

    def module_for(self, name: str) -> str:
        return f"{self.package}.{name}.{ENTRY_MODULE}"

    # -- one tile ---------------------------------------------------------------------------
    def run_tile(self, tile, retriever, as_of=None, *, ancillary=None, event_window=None, triggers=None, names: list | None = None,
                 bundle=None, workers: int | None = None, heavy: str = "defer") -> TileRun:
        """Build (or accept) the bundle, route, gate and run. `workers=0` runs the pipelines inline (debugging / tests).

        heavy="defer" (default): heavy-lane pipelines (manifest execution: heavy) are NOT launched — they answer PENDING
        with the lane annotated, and the caller submits them to the jobs runner (engine/jobs). heavy="run" executes them
        here (the jobs runner itself and tests use this)."""
        if heavy not in ("defer", "run"):
            raise ValueError(f"heavy must be 'defer' or 'run', got {heavy!r}")
        t_start = time.perf_counter()
        sel = [m for m in self.manifests if names is None or m.name in names]
        if names is not None:
            unknown = set(names) - {m.name for m in self.manifests}
            if unknown:
                raise KeyError(f"unknown pipeline(s): {sorted(unknown)}")
        if bundle is None:
            from .common.chain import build_scene_bundle
            bundle = build_scene_bundle(tile, retriever, as_of, ancillary=ancillary, active_codes=self.active_codes(names) or None,
                                        event_window=event_window, triggers=triggers, cfg=self.cfg)
        results, todo = self.plan(bundle, sel, heavy=heavy)
        bpath = None
        if todo:
            if self.cfg.registry.persist_bundles and workers != 0:
                from .common.bundle_io import save_bundle
                bpath = save_bundle(bundle, self.bundles_dir)
            results.update(self._execute(bundle, bpath, todo, workers))
        ordered = {m.name: results[m.name] for m in sel}
        return TileRun(bundle.tile.id, bundle, bpath, ordered, bundle.routing, time.perf_counter() - t_start, dict(self.scan_errors))

    # -- decide what runs -------------------------------------------------------------------
    def plan(self, bundle, manifests: list, heavy: str = "defer") -> tuple:
        """-> ({name: synthetic PipelineResult for everything not launched}, [manifests to launch])."""
        rcfg = self.cfg.registry
        results: dict = {}
        todo: list = []
        decisions = bundle.routing.decisions if bundle.routing is not None else {}
        for m in manifests:
            d = decisions.get(m.name)
            if d is None:
                if bundle.routing is None:
                    results[m.name] = _synthetic(m, bundle, "NOT_RUN", ["bundle has no routing (P12 did not run)"])
                    continue
                d = RoutingDecision(m.name, "unknown", ("no routing rule for this pipeline",), ("routing rule",))
            detail = {"routing": {"applies": d.applies, "reasons": list(d.reasons), "needs": list(d.needs), "proxy": d.proxy}}
            if d.applies == "no":
                r = _synthetic(m, bundle, "NOT_APPLICABLE", list(d.reasons))
            elif d.applies == "unknown" and not rcfg.run_unknown:
                r = _synthetic(m, bundle, "NOT_RUN", ["applicability could not be evaluated"] + list(d.reasons))
            else:
                r = self._gate(m, bundle)
                if r is None:
                    if m.execution == "heavy" and heavy == "defer":
                        r = _synthetic(m, bundle, "PENDING",
                                       [f"{m.name} declares execution: heavy — deferred to the jobs lane "
                                        f"(own timeout {m.deadline_s:g} s, progress file, content-addressed result cache)"],
                                       detail={"lane": "heavy", "timeout_s": m.deadline_s,
                                               "submit": "engine.jobs.HeavyJobRunner.submit(pipeline=..., bundle_path=...)"})
                    else:
                        todo.append(m)
                        continue
            r.detail.update(detail)
            results[m.name] = r
        return results, todo

    def _gate(self, m: Manifest, bundle) -> PipelineResult | None:
        rcfg = self.cfg.registry
        if not rcfg.gate_min_epochs:
            return None
        if not bundle.stacks:
            if m.needs_sar:
                return _synthetic(m, bundle, "INSUFFICIENT_EVIDENCE", ["no usable NISAR data for this tile in the window"],
                                  min_epochs_met=False if m.min_epochs else None)
            return None          # needs_sar: false — the pipeline answers from its own fetched data (honest INSUFFICIENT later)
        if m.min_epochs is None:
            return None
        metric = rcfg.min_epochs_metric.get(m.name, "epochs")
        have = bundle.depth.metric(metric)
        if have >= m.min_epochs:
            return None
        return _synthetic(m, bundle, "INSUFFICIENT_EVIDENCE",
                          [f"{m.name} needs min_epochs={m.min_epochs} ({metric}); the bundle has {have}"], min_epochs_met=False,
                          detail={"min_epochs": m.min_epochs, "metric": metric, "have": have})

    # -- execution --------------------------------------------------------------------------
    def _make_task(self, m: Manifest, bundle, bpath) -> dict:
        t = {"module": self.module_for(m.name), "sys_path": self.sys_path}
        if bpath is not None:
            t["bundle_path"] = str(bpath)
        else:
            t["bundle"] = bundle
        return t

    def _finish(self, m: Manifest, bundle, out: dict, wall: float) -> PipelineResult:
        k = out["kind"]
        if k == "no_entry":
            r = _synthetic(m, bundle, "NOT_MODELLED", [f"{self.module_for(m.name)} has no {ENTRY_FUNC}() yet"],
                           warnings=["pipeline not implemented"])
        elif k == "raised":
            r = _synthetic(m, bundle, "ERROR", [out["error"]], detail={"trace": out.get("trace", "")})
        elif k in ("timeout", "crashed"):
            r = _synthetic(m, bundle, "ERROR", [out["error"]])
        else:
            r = coerce_result(out["raw"], m, bundle)
            if m.min_epochs is not None and r.min_epochs_met is None:
                r.min_epochs_met = True
        r.runtime_s = float(out.get("runtime_s", wall))
        return r

    def _execute(self, bundle, bpath, todo: list, workers: int | None) -> dict:
        if workers == 0:
            res = {}
            for m in todo:
                t0 = time.perf_counter()
                out = _worker(self._make_task(m, bundle, None))
                res[m.name] = self._finish(m, bundle, out, time.perf_counter() - t0)
            return res
        rcfg = self.cfg.registry
        n = workers or rcfg.max_workers or max(1, min((os.cpu_count() or 2) - 1, 8))
        n = max(1, min(n, len(todo)))
        ctx = mp.get_context(rcfg.start_method)
        queue = deque(_Task(m, self.module_for(m.name)) for m in todo)
        results: dict = {}
        inflight: dict = {}                       # future -> _Task
        pool = cf.ProcessPoolExecutor(max_workers=n, mp_context=ctx)

        def recycle(reason: str, culprit: "_Task | None"):
            """Kill the pool; tasks that were merely in flight go back to the queue to run alone."""
            nonlocal pool
            procs = list(getattr(pool, "_processes", {}).values())
            for p in procs:
                try:
                    p.terminate()
                except Exception:
                    pass
            pool.shutdown(wait=False, cancel_futures=True)
            for f, t in list(inflight.items()):
                inflight.pop(f)
                if t is culprit:
                    continue
                t.attempts += 1
                if t.attempts >= rcfg.max_attempts:
                    results[t.manifest.name] = _synthetic(t.manifest, bundle, "ERROR",
                                                          [f"worker process died ({reason}); {t.attempts} attempts"])
                else:
                    t.solo = True
                    queue.appendleft(t)
            pool = cf.ProcessPoolExecutor(max_workers=n, mp_context=ctx)

        try:
            while queue or inflight:
                while queue and len(inflight) < n:
                    t = queue[0]
                    if t.solo and inflight:
                        break                      # a retried task runs alone so a crash can be pinned on it
                    queue.popleft()
                    t.submitted = time.monotonic()
                    inflight[pool.submit(_worker, self._make_task(t.manifest, bundle, bpath))] = t
                    if t.solo:
                        break
                done, _ = cf.wait(list(inflight), timeout=0.2, return_when=cf.FIRST_COMPLETED)
                broken = False
                for f in done:
                    t = inflight.pop(f)
                    try:
                        out = f.result()
                    except cf.process.BrokenProcessPool:
                        inflight[f] = t
                        broken = True
                        continue
                    except Exception as ex:
                        out = {"kind": "crashed", "error": f"{type(ex).__name__}: {ex}"}
                    results[t.manifest.name] = self._finish(t.manifest, bundle, out, time.monotonic() - t.submitted)
                if broken:
                    solo = [t for t in inflight.values() if t.solo]
                    if len(inflight) == 1 and solo:      # alone and still dying: it is the culprit
                        (f, t), = list(inflight.items())
                        inflight.pop(f)
                        results[t.manifest.name] = self._finish(t.manifest, bundle, {
                            "kind": "crashed", "error": "worker process died while running this pipeline (crash / out of memory)"},
                            time.monotonic() - t.submitted)
                        recycle("crash", t)
                    else:
                        recycle("a worker crashed", None)
                    continue
                now = time.monotonic()
                over = [t for t in inflight.values() if now - t.submitted > t.manifest.deadline_s]
                if over:
                    for t in over:
                        results[t.manifest.name] = self._finish(t.manifest, bundle, {
                            "kind": "timeout", "error": f"exceeded timeout_s={t.manifest.deadline_s:g}",
                            "runtime_s": now - t.submitted}, now - t.submitted)
                    for f in [f for f, t in inflight.items() if t in over]:
                        inflight.pop(f)
                    recycle("timeout", None)
        finally:
            for p in list(getattr(pool, "_processes", {}).values()):
                try:
                    p.terminate()
                except Exception:
                    pass
            pool.shutdown(wait=False, cancel_futures=True)
        return results


def run_tile(tile, retriever, as_of=None, **kw) -> TileRun:
    """Module-level convenience: `Registry().run_tile(...)`."""
    reg_kw = {k: kw.pop(k) for k in ("pipelines_dir", "package", "cfg", "sys_path", "bundles_dir") if k in kw}
    return Registry(**reg_kw).run_tile(tile, retriever, as_of, **kw)


__all__ = ["Registry", "Manifest", "PipelineResult", "TileRun", "scan_pipelines", "load_manifest", "coerce_result", "run_tile",
           "REGISTRY_STATUSES"]
