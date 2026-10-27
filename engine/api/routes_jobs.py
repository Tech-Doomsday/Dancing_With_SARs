"""5 — GET /api/jobs/{job_id}: heavy-lane job state (progress file + result cache)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .state import get_runner

router = APIRouter()


@router.get("/api/jobs/{job_id}")
def job_state(job_id: str):
    runner = get_runner()
    state = runner.heavy.get_state(job_id)
    if state.get("status") == "unknown":
        raise HTTPException(status_code=404, detail=f"unknown job {job_id!r}")
    return {"ok": True, **state}
