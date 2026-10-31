"""Stage 3 routes: POST /api/retrievals, GET /api/retrievals/{id}, POST /api/retrievals/{id}/cancel.
Every call answers at once (202 + id, then poll) — nothing here holds a request open."""
from __future__ import annotations

from functools import lru_cache

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import jobs as api_jobs
from .jobs import NO_EARTHDATA_MESSAGE, earthdata_present, scans_enabled
from .retrievals import RetrievalManager
from .routes_scanner import _ALLOWED_PRODUCTS, _product_names
from .state import get_runner

router = APIRouter(prefix="/api")


@lru_cache(maxsize=1)
def get_retrievals() -> RetrievalManager:
    from engine.common.config import data_root
    return RetrievalManager(get_runner(), data_root())


class RetrievalRequest(BaseModel):
    lon: float | None = Field(None, description="tile centre (use with lat) — the engine tiles it")
    lat: float | None = None
    bbox: list[float] | None = Field(None, description="[west, south, east, north]; alternative to lon/lat")
    products: list[str] = Field(default_factory=lambda: ["GUNW", "GCOV"])
    lookback_days: float = 60.0
    layers: str | None = Field(None, description="layer regex; default = the engine's own layer request")
    max_granules: int | None = Field(None, ge=1, le=500, description="cap per product: the newest N")


def _err(status: int, msg: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"ok": False, "error": msg})


@router.post("/retrievals", status_code=202)
def create_retrieval(body: RetrievalRequest):
    if not scans_enabled():
        raise _err(403, "retrieval is disabled on this engine (set NISAR_ENABLE_SCANS=1)")
    if not earthdata_present():
        raise _err(503, NO_EARTHDATA_MESSAGE)
    bad = [p for p in body.products if p not in _ALLOWED_PRODUCTS]
    if not body.products or bad:
        raise _err(400, f"products must be a non-empty subset of {sorted(_ALLOWED_PRODUCTS)}; got {body.products}")
    if not (0.0 < body.lookback_days <= 365.0):
        raise _err(400, f"lookback_days {body.lookback_days} outside (0, 365]")
    tile_id = None
    if body.bbox is not None:
        if len(body.bbox) != 4 or body.bbox[0] >= body.bbox[2] or body.bbox[1] >= body.bbox[3]:
            raise _err(400, "bbox must be [west, south, east, north] with west < east and south < north")
        bbox = tuple(body.bbox)
    elif body.lon is not None and body.lat is not None:
        from engine.retrieval.settings import load_settings, tile_cell_size_m
        from engine.tiling.mgrs import tile_at
        try:
            tile = tile_at(body.lon, body.lat, size=tile_cell_size_m())
        except ValueError as ex:
            raise _err(400, str(ex)) from ex
        tile_id, bbox = tile.id, tile.bbox(load_settings().tiling["margin_m"])
    else:
        raise _err(400, "give either lon+lat or bbox")
    try:
        rid = get_retrievals().start(
            bbox=bbox, tile=tile_id, products=body.products, product_names=_product_names(body.products),
            lookback_days=body.lookback_days, layers=body.layers, max_granules=body.max_granules,
            retriever_factory=api_jobs._default_retriever_factory())
    except RuntimeError as ex:
        raise _err(409, str(ex)) from ex
    return {"ok": True, "retrieval_id": rid, "tile": tile_id, "bbox": list(bbox)}


@router.get("/retrievals/{rid}")
def get_retrieval(rid: str):
    j = get_retrievals().get(rid)
    if j is None:
        raise _err(404, f"no retrieval {rid}")
    j.pop("cancel_requested", None)
    gs = j["granules"]
    count = {s: sum(1 for g in gs if g["state"] == s) for s in ("queued", "fetching", "stored", "skipped", "failed", "cancelled")}
    return {"ok": True, **j, "counts": count, "total": len(gs)}


@router.post("/retrievals/{rid}/cancel")
def cancel_retrieval(rid: str):
    mgr = get_retrievals()
    if mgr.get(rid) is None:
        raise _err(404, f"no retrieval {rid}")
    if not mgr.cancel(rid):
        raise _err(409, f"retrieval {rid} is not running")
    return {"ok": True, "message": "cancel requested: granules already downloading finish and stay stored; no new ones start"}
