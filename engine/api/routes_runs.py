"""Run routes — POST/GET /api/runs (+ layer payloads).

Envelope convention inherited from the reference app: {ok: true, ...} or an
HTTP error with {ok: false, error}.
"""
from __future__ import annotations

import copy

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from engine.lib.layer_payload import validate_layer_payload

from .jobs import JobRunner
from .state import get_runner

router = APIRouter(prefix="/api")

ALLOWED_PARAMS = {
    "coherence_threshold": (0.0, 1.0),
    "significance_k": (1.0, 6.0),
    "detection_floor_m": (0.001, 0.2),
    "far_field_margin": (0.02, 0.4),
}


class RunRequest(BaseModel):
    bundle_id: str = Field(min_length=1)
    pipelines: list[str] = Field(default_factory=lambda: ["earthquake_coseismic"])
    params: dict | None = None


@router.post("/runs")
def create_run(body: RunRequest):
    runner: JobRunner = get_runner()
    known = set(runner.heavy.registry.names())
    unknown = [p for p in body.pipelines if p not in known]
    if unknown:
        raise HTTPException(status_code=400, detail={
            "ok": False, "error": f"unknown pipeline(s): {unknown}"})
    if not body.pipelines:
        raise HTTPException(status_code=400, detail={"ok": False, "error": "pipelines list is empty"})
    unknown = sorted(set(body.params or {}) - set(ALLOWED_PARAMS))
    if unknown:
        raise HTTPException(status_code=400, detail={
            "ok": False, "error": f"unknown param(s) {unknown}; allowed: {sorted(ALLOWED_PARAMS)}"})
    for key, (lo, hi) in ALLOWED_PARAMS.items():
        if key in (body.params or {}):
            v = float(body.params[key])
            if not (lo <= v <= hi):
                raise HTTPException(status_code=400, detail={
                    "ok": False, "error": f"param {key}={v} outside [{lo}, {hi}]"})
    try:
        run_id = runner.start_run(body.bundle_id, body.pipelines, body.params)
    except FileNotFoundError as ex:
        raise HTTPException(status_code=404, detail={"ok": False, "error": str(ex)}) from ex
    except RuntimeError as ex:
        raise HTTPException(status_code=409, detail={"ok": False, "error": str(ex)}) from ex
    return {"ok": True, "run_id": run_id}


@router.get("/runs")
def list_runs():
    runner: JobRunner = get_runner()
    return {"ok": True, "runs": runner.list_states()}


def _strip_grids(payload: dict) -> dict:
    """slim=1 keeps the layer inventory (metadata only): data_b64/data are dropped, everything
    else survives — a panel that only needs names/bounds/units never downloads megabytes."""
    out = copy.deepcopy(payload)
    out.pop("data_b64", None)
    out.pop("data", None)
    return out


def _slim_state(state: dict) -> dict:
    slim = {**state, "results": []}
    for result in state.get("results", []):
        r = copy.deepcopy(result)
        detail = dict(r.get("detail") or {})
        layers = detail.get("layers") or {}
        detail["layers"] = {name: _strip_grids(p) for name, p in layers.items()}
        r["detail"] = detail
        slim["results"].append(r)
    return slim


@router.get("/runs/{run_id}")
def get_run(run_id: str, slim: int = 0):
    runner: JobRunner = get_runner()
    state = runner.get_state(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail={"ok": False, "error": f"unknown run: {run_id}"})
    if slim:
        return {"ok": True, **_slim_state(state)}
    return {"ok": True, **state}


def _check_payload(pipeline: str, layer_name: str, payload: dict) -> None:
    """§5.1 item 5: a contract violation at the API edge is a 422 with the list of problems,
    never a 500 and never a silent pass."""
    problems = validate_layer_payload(payload)
    if problems:
        raise HTTPException(status_code=422, detail={
            "ok": False,
            "error": f"layer {layer_name!r} of pipeline {pipeline!r} violates the layer contract: "
                     + "; ".join(problems),
            "problems": problems})


def _layer_names(state: dict) -> list[str]:
    """Every layer name any pipeline in this run produced (the 404 `available` list)."""
    return sorted({name for result in state.get("results", [])
                   for name in ((result.get("detail") or {}).get("layers") or {})})


@router.get("/runs/{run_id}/pipelines/{pipeline}/layers/{layer}")
def get_pipeline_layer(run_id: str, pipeline: str, layer: str):
    """D5: pipeline-scoped layer route — layer names collide once more pipelines emit them
    (e.g. two `detected` grids). The UI uses THIS route; the first-match route below stays
    for compatibility and is deprecated."""
    runner: JobRunner = get_runner()
    state = runner.get_state(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail={"ok": False, "error": f"unknown run: {run_id}"})
    for result in state.get("results", []):
        if result.get("pipeline") != pipeline:
            continue
        payload = (result.get("detail") or {}).get("layers", {}).get(layer)
        if payload is not None:
            _check_payload(pipeline, layer, payload)
            return {"ok": True, "layer": payload, "pipeline": pipeline}
    known = _layer_names(state)
    raise HTTPException(status_code=404, detail={
        "ok": False, "error": f"layer {layer!r} not produced by pipeline {pipeline!r} in run {run_id}",
        "available": known})


@router.get("/runs/{run_id}/layers/{layer}")
def get_layer(run_id: str, layer: str):
    """DEPRECATED (first match across pipelines; kept for the existing report pages).
    Prefer GET /api/runs/{id}/pipelines/{pipeline}/layers/{layer}."""
    runner: JobRunner = get_runner()
    state = runner.get_state(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail={"ok": False, "error": f"unknown run: {run_id}"})
    for result in state.get("results", []):
        payload = (result.get("detail") or {}).get("layers", {}).get(layer)
        if payload is not None:
            _check_payload(result.get("pipeline", ""), layer, payload)
            return {"ok": True, "layer": payload, "pipeline": result.get("pipeline")}
    known = _layer_names(state)
    raise HTTPException(status_code=404, detail={
        "ok": False, "error": f"layer {layer!r} not in run {run_id}", "available": known})
