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


_ALLOWED_PRODUCTS = {"GUNW", "GCOV", "GOFF"}


def _product_names(levels: list[str]) -> dict[str, str]:
    """level (GUNW/GCOV/GOFF) -> the search provider's processing-level name from config
    (the same names the legacy route and P01 use — one source of truth)."""
    from engine.common.config import load_common_config
    prods = load_common_config().products
    return {lv: getattr(prods, _ALLOWED_PRODUCTS_FIELD[lv]) for lv in levels}


_ALLOWED_PRODUCTS_FIELD = {"GUNW": "gunw", "GCOV": "gcov", "GOFF": "goff"}

# E8 rate limit for the bbox mode: a sliding window over successful searches. The legacy
# per-tile mode keeps its existing behaviour (existing tests pin it); the bbox mode is the
# public Explorer entry point, so it answers 429 + Retry-After instead of queueing forever.
_BBOX_RATE = {"max": 30, "window_s": 60.0}
_BBOX_HITS: list[float] = []


def _bbox_rate_limited() -> int | None:
    """Return the Retry-After seconds when over budget, else None."""
    import time as _time
    now = _time.monotonic()
    while _BBOX_HITS and now - _BBOX_HITS[0] > _BBOX_RATE["window_s"]:
        _BBOX_HITS.pop(0)
    if len(_BBOX_HITS) >= _BBOX_RATE["max"]:
        return max(1, int(_BBOX_RATE["window_s"] - (now - _BBOX_HITS[0])) + 1)
    _BBOX_HITS.append(now)
    return None


def _bbox_granules(west: float, south: float, east: float, north: float, level: str,
                   from_: str | None, to: str | None, max_results: int | None, fresh: bool):
    """E8: the Granule Explorer's bbox mode. Antimeridian boxes are REFUSED (400) — the search
    provider takes W<S<E<N and a wrapped box would silently invert; split the query instead."""
    for name, v, lo, hi in (("west", west, -180.0, 180.0), ("east", east, -180.0, 180.0),
                            ("south", south, -90.0, 90.0), ("north", north, -90.0, 90.0)):
        if not (lo <= v <= hi):
            raise HTTPException(status_code=400, detail={
                "ok": False, "error": f"{name} {v} outside [{lo}, {hi}]"})
    if west > east:
        raise HTTPException(status_code=400, detail={
            "ok": False,
            "error": ("box crosses the antimeridian (west > east); split it at ±180 — "
                      "the granule search takes west < east")})
    if south >= north:
        raise HTTPException(status_code=400, detail={
            "ok": False, "error": f"south {south} must be below north {north}"})
    levels = [lv.strip().upper() for lv in level.split(",") if lv.strip()] if level else ["GUNW"]
    if not levels:
        levels = ["GUNW"]
    unknown = [lv for lv in levels if lv not in _ALLOWED_PRODUCTS]
    if unknown:
        raise HTTPException(status_code=400, detail={
            "ok": False, "error": f"level {unknown[0]!r} not in GUNW|GCOV|GOFF"})
    retry_after = _bbox_rate_limited()
    if retry_after is not None:
        _raise_429(retry_after)
    from engine.common.config import load_common_config
    from engine.common.context import iso
    from datetime import datetime, timedelta, timezone

    cfg = load_common_config()
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
    budget = int(max_results) if max_results else 250
    budget = max(1, min(budget, 250))         # the engine's own search budget caps the request
    runner: JobRunner = get_runner()
    try:
        out = runner.granule_search_bbox(
            bbox=(west, south, east, north), levels=levels, t0=iso(t0), t1=iso(t1),
            product_names=_product_names(levels),
            max_results=budget, fresh=fresh)
    except ScanError as ex:
        raise HTTPException(status_code=ex.status, detail={"ok": False, "error": str(ex)}) from ex
    return _meta_answer(out)


def _meta_answer(out: dict):
    """A finished metadata result is a plain 200 (unchanged contract); a slow one is 202 + job id."""
    if isinstance(out, dict) and out.get("pending"):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=202, content={
            "ok": True, "pending": True, "job_id": out["job_id"],
            "poll": f"/api/granules/jobs/{out['job_id']}",
            "note": "the search is still running; poll the job (no request stays open past ~25 s)"})
    return {"ok": True, **out}


@router.get("/granules/jobs/{job_id}")
def granule_job(job_id: str):
    rec = get_runner().meta_job(job_id)
    if rec is None:
        raise HTTPException(status_code=404, detail={"ok": False, "error": f"unknown or expired search job: {job_id}"})
    if rec["status"] == "error":
        raise HTTPException(status_code=int(rec.get("http") or 500), detail={"ok": False, "error": rec["error"]})
    if rec["status"] == "done":
        return {"ok": True, "pending": False, **rec["result"]}
    return {"ok": True, "pending": True, "job_id": job_id, **{k: v for k, v in rec.items() if k != "status"}}


def _raise_429(retry_after: int):
    raise HTTPException(status_code=429, detail={
        "ok": False,
        "error": ("granule search rate limit hit — the engine searches ASF live; "
                  "retry after the window resets")},
        headers={"Retry-After": str(retry_after)})


@router.get("/granules")
def granules_route(lon: float | None = Query(default=None), lat: float | None = Query(default=None),
                   product: str = Query("all"),
                   west: float | None = Query(default=None), south: float | None = Query(default=None),
                   east: float | None = Query(default=None), north: float | None = Query(default=None),
                   level: str = Query("GUNW"), max_: int | None = Query(default=None, alias="max"),
                   fresh: bool = Query(False),
                   from_: str | None = Query(None, alias="from"),
                   to: str | None = Query(None)):
    """§5.5 + E8: granule availability.

    * legacy per-tile mode (lon+lat): P01's search + grouping for the ONE tile — unchanged,
      pinned by the existing tests (the old Data tab keeps working).
    * bbox mode (west+south+east+north): the Granule Explorer's answer — granules[] enriched
      from the filename where verified + provider metadata, stacks[] with engine-computed day
      gaps. Antimeridian box → 400; rate limited → 429 + Retry-After; unknown → null.
    """
    if None not in (west, south, east, north):
        return _bbox_granules(west, south, east, north, level, from_, to, max_, fresh)
    if lon is None or lat is None:
        raise HTTPException(status_code=400, detail={
            "ok": False, "error": "give either lon+lat or a west/south/east/north box"})
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
    return _meta_answer(out)


def _include_goff() -> bool:
    from engine.common.config import load_common_config
    return bool(load_common_config().products.include_goff)


__all__ = ["router", "tile_at_route", "create_scan", "granules_route", "scans_enabled", "earthdata_present"]
