"""§5.2 / §5.3 / §5.5 — location routes: GET /api/tile-at, POST /api/scans, GET /api/granules.

The UI cannot do tiling math (thin client), so the engine resolves points to MGRS tiles and
answers granule availability. Envelope convention: {ok: true, ...} or an HTTPException whose
detail is {ok: false, error: "<verbatim reason>"}.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .jobs import NO_EARTHDATA_MESSAGE, JobRunner, ScanError, earthdata_present, scans_enabled
from .state import get_runner

router = APIRouter(prefix="/api")


def _resolve_tile(lon: float, lat: float) -> dict:
    """§5.2: the engine does the tiling; out-of-range input answers 400 with the engine's own
    ValueError text (verbatim — e.g. the -80..84 latitude band message)."""
    from engine.retrieval.settings import tile_cell_size_m
    from engine.tiling.mgrs import tile_at

    size = tile_cell_size_m()
    try:
        tile = tile_at(lon, lat, size=size)
    except ValueError as ex:
        raise HTTPException(status_code=400, detail={"ok": False, "error": str(ex)}) from ex
    return tile.to_dict()


def _coord(lon: float, lat: float) -> None:
    if not (-180.0 <= lon <= 180.0):
        raise HTTPException(status_code=400, detail={"ok": False, "error": f"lon {lon} outside [-180, 180]"})
    if not (-90.0 <= lat <= 90.0):
        raise HTTPException(status_code=400, detail={"ok": False, "error": f"lat {lat} outside [-90, 90]"})


@router.get("/tile-at")
def tile_at_route(lon: float | None = Query(default=None), lat: float | None = Query(default=None),
                  tile: str | None = Query(default=None)):
    """§5.2: a point -> its MGRS tile. Also accepts `tile=<MGRS id>` alone (§7.7 URL restore:
    a pasted ?tile= link needs the tile's geometry/centre back from the engine — the UI never
    inverts ids itself). Exactly one mode must be given."""
    if tile is not None:
        from engine.retrieval.settings import tile_cell_size_m
        from engine.tiling.mgrs import tile_from_id

        try:
            t = tile_from_id(tile)
        except ValueError as ex:
            raise HTTPException(status_code=400, detail={"ok": False, "error": str(ex)}) from ex
        size = tile_cell_size_m()
        if t.size != size:
            raise HTTPException(status_code=400, detail={
                "ok": False, "error": f"tile {t.id} has size {t.size} m, this engine serves {size} m tiles"})
        return {"ok": True, "tile": t.to_dict()}
    if lon is None or lat is None:
        raise HTTPException(status_code=400, detail={
            "ok": False, "error": "give either lon+lat or a tile id"})
    _coord(lon, lat)
    return {"ok": True, "tile": _resolve_tile(lon, lat)}


class ScanRequest(BaseModel):
    lon: float = Field(..., description="longitude in [-180, 180]")
    lat: float = Field(..., description="latitude in [-90, 90]")
    as_of: str | None = None                 # ISO-8601 UTC; default now
    lookback_days: float | None = None       # in (0, 365]; default the engine's own window


@router.post("/scans")
def create_scan(body: ScanRequest):
    _coord(body.lon, body.lat)
    if body.as_of is not None:
        from engine.common.context import parse_time
        try:
            parse_time(body.as_of)
        except ValueError as ex:
            raise HTTPException(status_code=400, detail={"ok": False, "error": str(ex)}) from ex
    if body.lookback_days is not None and not (0.0 < body.lookback_days <= 365.0):
        raise HTTPException(status_code=400, detail={
            "ok": False, "error": f"lookback_days {body.lookback_days} outside (0, 365]"})
    tile = _resolve_tile(body.lon, body.lat)          # the scan scope: the ONE tile (DEC3)
    runner: JobRunner = get_runner()
    try:
        run_id = runner.start_scan(lon=body.lon, lat=body.lat, as_of=body.as_of,
                                   lookback_days=body.lookback_days)
    except ScanError as ex:
        raise HTTPException(status_code=ex.status, detail={"ok": False, "error": str(ex)}) from ex
    except RuntimeError as ex:                        # single-flight 409 (message names the active run)
        raise HTTPException(status_code=409, detail={"ok": False, "error": str(ex),
                                                     "active_run_id": _active_run_id(runner)}) from ex
    return {"ok": True, "run_id": run_id, "tile": tile}


def _active_run_id(runner: JobRunner) -> str | None:
    active = runner.store.active_run()
    return active["id"] if active else None


_ALLOWED_PRODUCTS = {"GUNW": "gunw", "GCOV": "gcov", "GOFF": "goff"}


@router.get("/granules")
def granules_route(lon: float = Query(...), lat: float = Query(...),
                   product: str = Query("all"),
                   from_: str | None = Query(None, alias="from"),
                   to: str | None = Query(None)):
    """§5.5: granule availability for the tile — search + grouping half of P01 only.
    Default window: config/common.yaml window.lookback_days (120) — chosen over the poller's 45
    because the Data tab must answer "how much data does a scan of this tile use" (the chain
    searches 120); the UI's own date-range control overrides it either way (U8)."""
    _coord(lon, lat)
    from engine.common.config import load_common_config
    from engine.common.context import iso

    prod = product.strip().upper() or "ALL"
    if prod == "ALL":
        products = ["GUNW", "GCOV", "GOFF"] if _include_goff() else ["GUNW", "GCOV"]
    elif prod in _ALLOWED_PRODUCTS:
        products = [prod]
    else:
        raise HTTPException(status_code=400, detail={
            "ok": False, "error": f"product {product!r} not in GUNW|GCOV|GOFF|all"})
    if not earthdata_present():
        raise HTTPException(status_code=503, detail={
            "ok": False, "error": NO_EARTHDATA_MESSAGE})
    cfg = load_common_config()
    from datetime import datetime, timedelta, timezone
    t1 = datetime.now(timezone.utc).replace(microsecond=0)
    try:
        t0 = t1 - timedelta(days=float(cfg.window.lookback_days))
        if from_ is not None:
            from engine.common.context import parse_time
            t0 = parse_time(from_)
        if to is not None:
            from engine.common.context import parse_time
            t1 = parse_time(to)
    except ValueError as ex:
        raise HTTPException(status_code=400, detail={"ok": False, "error": str(ex)}) from ex
    if t0 >= t1:
        raise HTTPException(status_code=400, detail={"ok": False, "error": "window 'from' must be before 'to'"})
    runner: JobRunner = get_runner()
    try:
        out = runner.granule_search(lon=lon, lat=lat, products=products,
                                    t0=iso(t0), t1=iso(t1))
    except ScanError as ex:
        raise HTTPException(status_code=ex.status, detail={"ok": False, "error": str(ex)}) from ex
    return {"ok": True, **out}


def _include_goff() -> bool:
    from engine.common.config import load_common_config
    return bool(load_common_config().products.include_goff)


__all__ = ["router", "tile_at_route", "create_scan", "granules_route", "scans_enabled", "earthdata_present"]
