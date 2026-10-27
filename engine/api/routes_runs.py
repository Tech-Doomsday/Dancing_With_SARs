"""Run routes — POST/GET /api/runs (+ layer payloads).

Envelope convention inherited from the reference app: {ok: true, ...} or an
HTTP error with {ok: false, error}.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

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
    if "earthquake_coseismic" not in body.pipelines:
        raise HTTPException(status_code=400, detail={
            "ok": False, "error": "v1 serves earthquake_coseismic only; got " + ",".join(body.pipelines)})
    for key, (lo, hi) in ALLOWED_PARAMS.items():
        if key in (body.params or {}):
            v = float(body.params[key])
            if not (lo <= v <= hi):
                raise HTTPException(status_code=400, detail={
                    "ok": False, "error": f"param {key}={v} outside [{lo}, {hi}]"})
    runner: JobRunner = get_runner()
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


@router.get("/runs/{run_id}")
def get_run(run_id: str):
    runner: JobRunner = get_runner()
    state = runner.get_state(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail={"ok": False, "error": f"unknown run: {run_id}"})
    return {"ok": True, "run": state}


@router.get("/runs/{run_id}/layers/{layer}")
def get_layer(run_id: str, layer: str):
    runner: JobRunner = get_runner()
    state = runner.get_state(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail={"ok": False, "error": f"unknown run: {run_id}"})
    for result in state.get("results", []):
        payload = (result.get("detail") or {}).get("layers", {}).get(layer)
        if payload is not None:
            return {"ok": True, "layer": payload, "pipeline": result.get("pipeline")}
    known = sorted({name for result in state.get("results", [])
                    for name in ((result.get("detail") or {}).get("layers") or {})})
    raise HTTPException(status_code=404, detail={
        "ok": False, "error": f"layer {layer!r} not in run {run_id}", "available": known})
