"""Event context route — GET /api/events (live ComCat via the R1 adapter).

Display-only context for the UI: the pipeline itself derives events from the
pair window. Upstream failures surface verbatim as 502 (the app's convention).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from engine.retrieval.clients.adapters import r1_comcat

router = APIRouter(prefix="/api")


@router.get("/events")
def list_events(lon: float = Query(..., ge=-180, le=180),
                lat: float = Query(..., ge=-85, le=85),
                days: int = Query(default=30, ge=1, le=365),
                min_magnitude: float = Query(default=4.0, ge=0, le=10)):
    from datetime import datetime, timedelta, timezone

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    bbox = (lon - 0.5, lat - 0.5, lon + 0.5, lat + 0.5)
    out = r1_comcat.fetch_events(
        bbox,
        start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        min_magnitude=min_magnitude, limit=100,
    )
    if out.get("error"):
        raise HTTPException(status_code=502, detail={
            "ok": False, "error": f"Catalog search failed: {out['error']}"})
    return {"ok": True, "events": out["events"], "source": out.get("source")}
