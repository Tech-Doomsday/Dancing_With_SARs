"""§1 gating + §4.5/§5/§6 — the gated Background routes under /api/background/*.

GATE (stopgap, documented): every route here requires `Authorization: Bearer
<NISAR_BACKGROUND_TOKEN>`. When the env var is unset the routes answer 503 naming the variable;
a missing/wrong token answers 401. The Background tab exposes internal traces and real data
fetches, so it must not be public. Real authentication is an owner action (see docs).

Envelope convention (the engine's own): success {ok: true, ...}; failure an HTTPException whose
detail is {ok: false, error: "<verbatim reason>"}. Rate limits answer 429 + Retry-After.

EXECUTION MODEL (two helpers, chosen per route):
  _run_bg    provider-backed routes (granule, preview, probe, stack-pair). Rate limit, then the
             Earthdata-credentials check (503 NO_EARTHDATA_MESSAGE), then the SAME single worker pool
             (`runner._pool`, max_workers=1) that runs scans and the granule search.
  _run_local the four local-data routes (runs/{id}/preprocessing, runs/{id}/arrays/{aid},
             .../arrays/{aid}/probe, runs/{id}/pipelines/{name}). They read in-memory traces or cached
             bundles only: NO Earthdata check and NOT on `runner._pool`, so they work without provider
             credentials and never queue behind a running scan. They run inline in the request's own
             threadpool thread (the handlers are plain `def`, so the event loop is not blocked).
Both still run after `require_token` and share the `_BG_HITS` rate limit (60/min).

Endpoints (all read-only; the first four run on the shared single worker pool, the last four do not):
  GET /api/background/granule?id=                       decoded fields + layer inventory + group
  GET /api/background/preview?id=&layer=&box&max_px&cmap(auto|…)&scale(auto|db|linear)[&format=json]   PNG (+ X-Img-Meta) or meta JSON
  GET /api/background/probe?id=&layer=&lon=&lat=        value at a point
  GET /api/background/stack-pair?id=                    the GCOV granules at a GUNW's two ends
  GET /api/background/runs/{id}/preprocessing[?bundle_id=]   §5 chain trace / bundle records
  GET /api/background/runs/{id}/arrays/{aid}[?cmap=&lo=&hi=&max_px=]   §5 array PNG
  GET /api/background/runs/{id}/arrays/{aid}/probe?fx=&fy=             §5 pixel probe
  GET /api/background/runs/{id}/pipelines/{name}        §6 input/process/output

Tests inject a fake source through _SOURCE_FACTORY (the same seam pattern the granule search
uses) and a fake registry through the runner; production always builds the real NisarSource.
"""
from __future__ import annotations

import hmac
import os
import re
import threading
from pathlib import Path

from concurrent.futures import TimeoutError as FuturesTimeout

from fastapi import APIRouter, Header, HTTPException, Query, Response

from . import background as bg
from .background import BackgroundError
from .jobs import NO_EARTHDATA_MESSAGE, earthdata_present
from .state import get_runner

router = APIRouter(prefix="/api/background")

# rate limits (sliding windows, the bbox-search pattern): metadata reads are cheap-ish, previews
# and probes each move real bytes from the provider, so they get their own tighter window.
_BG_RATE = {"max": 60, "window_s": 60.0}
_BG_HITS: list[float] = []
_PREVIEW_RATE = {"max": 12, "window_s": 60.0}
_PREVIEW_HITS: list[float] = []
# the handlers run in a threadpool; the check-and-append below must be atomic (one lock for both
# windows — the windows themselves stay global, there is no per-client logic)
_RATE_LOCK = threading.Lock()

# the retriever seam (§0 honesty: production builds the real client; tests inject a fake)
_SOURCE_FACTORY = bg.NisarSource


def _rate_limited(hits: list[float], rate: dict) -> int | None:
    import time as _time
    with _RATE_LOCK:
        now = _time.monotonic()
        while hits and now - hits[0] > rate["window_s"]:
            hits.pop(0)
        if len(hits) >= rate["max"]:
            return max(1, int(rate["window_s"] - (now - hits[0])) + 1)
        hits.append(now)
    return None


def _raise_429(retry_after: int):
    raise HTTPException(status_code=429, detail={
        "ok": False,
        "error": ("background endpoint rate limit hit — these routes read real provider data; "
                  "retry after the window resets")},
        headers={"Retry-After": str(retry_after)})


def _token_matches(authorization: str | None, expected: str) -> bool:
    """`Bearer <token>`: case-sensitive scheme, exactly one space, constant-time compare on UTF-8."""
    if not authorization or not authorization.startswith("Bearer "):
        return False
    presented = authorization[len("Bearer "):]
    if not presented or presented[0] == " ":      # "Bearer" alone / two spaces
        return False
    return hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8"))


def require_token(authorization: str | None) -> None:
    """The §1 gate. 503 (unset env var, message names it) or 401 (missing/wrong bearer)."""
    expected = os.environ.get("NISAR_BACKGROUND_TOKEN")
    if not expected:
        raise HTTPException(status_code=503, detail={
            "ok": False,
            "error": ("background endpoints are gated and no gate is configured: set "
                      "NISAR_BACKGROUND_TOKEN (a stopgap until real authentication exists)")})
    if not _token_matches(authorization, expected):
        raise HTTPException(status_code=401, detail={
            "ok": False,
            "error": "missing or wrong background token — send 'Authorization: Bearer <NISAR_BACKGROUND_TOKEN>'"})


def _default_source():
    return _SOURCE_FACTORY()


def _run_bg(fn, *args, preview_window: bool = False, **kwargs):
    """Rate limit + credentials check + single-pool execution shared by the data routes."""
    retry = _rate_limited(_PREVIEW_HITS if preview_window else _BG_HITS,
                          _PREVIEW_RATE if preview_window else _BG_RATE)
    if retry is not None:
        _raise_429(retry)
    if not earthdata_present():
        raise HTTPException(status_code=503, detail={"ok": False, "error": NO_EARTHDATA_MESSAGE})
    runner = get_runner()
    fut = runner._meta_pool.submit(fn, *args, **kwargs)     # metadata lane: never queued behind a scan/retrieval
    try:
        return fut.result(timeout=25.0)
    except BackgroundError as ex:
        raise HTTPException(status_code=ex.status, detail={"ok": False, "error": str(ex)}) from ex
    except FuturesTimeout as ex:
        raise HTTPException(status_code=504, detail={
            "ok": False, "error": ("this read from NASA did not finish within 25 s (the engine keeps working on it; "
                                   "ask again in a moment and it will usually be served from cache)")}) from ex


def _run_local(fn, *args, **kwargs):
    """Rate limit + inline execution for routes that only read in-memory traces / cached bundles.

    No Earthdata check (nothing here touches the provider) and NOT `runner._pool` (a running scan
    would otherwise starve a read of its own trace). The work is light: trace arrays are bounded
    in-memory thumbnails (<=512 px), a probe is one pixel, and the two bundle-reading routes do a
    JSON read + read-only mmap + a manifest scan — not scan-class work. Inline in the request thread
    therefore needs no executor and no timeout.
    """
    retry = _rate_limited(_BG_HITS, _BG_RATE)
    if retry is not None:
        _raise_429(retry)
    try:
        return fn(*args, **kwargs)
    except BackgroundError as ex:
        raise HTTPException(status_code=ex.status, detail={"ok": False, "error": str(ex)}) from ex
    except TimeoutError as ex:
        raise HTTPException(status_code=504, detail={
            "ok": False, "error": "the background worker did not answer in time"}) from ex


# --------------------------------------------------------------------------- #
# §4.5 data endpoints
# --------------------------------------------------------------------------- #
@router.get("/granule")
def background_granule(id: str = Query(..., min_length=1, max_length=400),
                       authorization: str | None = Header(default=None)):
    require_token(authorization)
    return _run_bg(_granule_worker, id)


def _granule_worker(id: str):
    source = _default_source()
    try:
        return bg.granule_detail(source, id)
    finally:
        source.close()


@router.get("/preview")
def background_preview(id: str = Query(..., min_length=1, max_length=400),
                       layer: str = Query(..., min_length=1, max_length=300),
                       west: float = Query(...), south: float = Query(...),
                       east: float = Query(...), north: float = Query(...),
                       max_px: int = Query(256), cmap: str = Query("auto"),
                       scale: str = Query("auto"),
                       fmt: str | None = Query(None, alias="format"),
                       authorization: str | None = Header(default=None)):
    require_token(authorization)
    if scale not in bg._SCALE_CHOICES:
        raise HTTPException(status_code=400, detail={
            "ok": False, "error": f"scale {scale!r} not in {'|'.join(bg._SCALE_CHOICES)}"})
    if cmap not in bg._CMAP_CHOICES:
        raise HTTPException(status_code=400, detail={
            "ok": False, "error": f"cmap {cmap!r} not in {'|'.join(bg._CMAP_CHOICES)}"})
    for name, v, lo, hi in (("west", west, -180.0, 180.0), ("east", east, -180.0, 180.0),
                            ("south", south, -90.0, 90.0), ("north", north, -90.0, 90.0)):
        if not (lo <= v <= hi):
            raise HTTPException(status_code=400, detail={
                "ok": False, "error": f"{name} {v} outside [{lo}, {hi}]"})
    if west >= east:
        raise HTTPException(status_code=400, detail={
            "ok": False,
            "error": ("box crosses the antimeridian (west > east); split it at ±180 — "
                      "the preview clip takes west < east")})
    if south >= north:
        raise HTTPException(status_code=400, detail={
            "ok": False, "error": f"south {south} must be below north {north}"})
    out = _run_bg(_preview_worker, id=id, layer=layer, bbox=(west, south, east, north),
                  max_px=max_px, cmap=cmap, scale=scale, preview_window=True)
    if fmt == "json":
        return {"ok": True, "meta": out["meta"]}
    import json as _json
    return Response(content=out["png"], media_type="image/png",
                    headers={"X-Img-Meta": _json.dumps(out["meta"]),
                             "Cache-Control": "private, max-age=300"})


def _preview_worker(*, id: str, layer: str, bbox: tuple, max_px: int, cmap: str, scale: str = "auto"):
    source = _default_source()
    try:
        return bg.preview(source, gid=id, layer=layer, bbox=bbox, max_px=max_px, cmap=cmap, scale=scale)
    finally:
        source.close()


@router.get("/probe")
def background_probe(id: str = Query(..., min_length=1, max_length=400),
                     layer: str = Query(..., min_length=1, max_length=300),
                     lon: float = Query(...), lat: float = Query(...),
                     authorization: str | None = Header(default=None)):
    require_token(authorization)
    if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
        raise HTTPException(status_code=400, detail={
            "ok": False, "error": "lon must be in [-180, 180] and lat in [-90, 90]"})
    return _run_bg(_probe_worker, id=id, layer=layer, lon=lon, lat=lat, preview_window=True)


def _probe_worker(*, id: str, layer: str, lon: float, lat: float):
    source = _default_source()
    try:
        return bg.probe(source, gid=id, layer=layer, lon=lon, lat=lat)
    finally:
        source.close()


@router.get("/stack-pair")
def background_stack_pair(id: str = Query(..., min_length=1, max_length=400),
                          authorization: str | None = Header(default=None)):
    require_token(authorization)
    return _run_bg(_stack_pair_worker, id)


def _stack_pair_worker(id: str):
    source = _default_source()
    try:
        return bg.stack_pair(source, gid=id)
    finally:
        source.close()


# --------------------------------------------------------------------------- #
# §5 preprocessing
# --------------------------------------------------------------------------- #
@router.get("/runs/{run_id}/preprocessing")
def run_preprocessing(run_id: str, bundle_id: str | None = Query(default=None),
                      authorization: str | None = Header(default=None)):
    require_token(authorization)
    return _run_local(_preprocessing_worker, run_id, bundle_id)


_BUNDLE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def _bundle_400(why: str, bundle_id: str):
    shown = bundle_id if len(bundle_id) <= 48 else bundle_id[:48] + "…"
    raise HTTPException(status_code=400, detail={
        "ok": False, "error": f"invalid bundle_id {shown!r}: {why}"})


def safe_bundle_dir(bundles_dir: Path, bundle_id: str) -> Path:
    """A user-supplied bundle id -> the bundle directory, or a 400. Never a path outside bundles_dir."""
    if not _BUNDLE_ID_RE.fullmatch(bundle_id) or ".." in bundle_id:
        _bundle_400("expected 1-128 characters of [A-Za-z0-9._-], starting with a letter or digit", bundle_id)
    root = Path(bundles_dir).resolve()
    resolved = (root / bundle_id).resolve()       # follows a symlink to its real target
    if resolved.parent != root or not resolved.is_relative_to(root):
        _bundle_400("it does not resolve to a directory directly inside the bundles store", bundle_id)
    return resolved


def _preprocessing_worker(run_id: str, bundle_id: str | None):
    runner = get_runner()
    if bundle_id:
        bundle_dir = safe_bundle_dir(runner.bundles_dir, bundle_id)
        try:
            from engine.common.bundle_io import load_bundle
            bundle = load_bundle(bundle_dir)
        except Exception as ex:
            raise HTTPException(status_code=404, detail={
                "ok": False, "error": f"bundle {bundle_id!r} could not be loaded: "
                                      f"{type(ex).__name__}: "
                                      f"{bg.scrub_paths(str(ex), runner.bundles_dir)}"}) from ex
        out = bg.bundle_preprocessing(bundle)
        return {"ok": True,
                "run": {"id": None, "bundle_id": bundle_id, "tile": bundle.tile.id,
                        "kind": "bundle", "status": "cached"},
                "trace": out, "trace_arrays": False,
                "note": "records read from the cached bundle itself"}
    state = runner.get_state(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail={"ok": False, "error": f"unknown run: {run_id}"})
    trace = runner.get_trace(run_id)
    if trace is None:
        return {
            "ok": True,
            "run": {"id": run_id, "status": state.get("status"), "tile": state.get("tile"),
                    "kind": state.get("kind"), "bundle_id": state.get("bundle_id")},
            "trace": None,
            "empty_reason": ("no recorded preprocessing trace for this run — runs started before "
                             "tracing (or restored from the store after a restart) carry none; "
                             "run a fresh scan to see one"),
        }
    return {"ok": True,
            "run": {"id": run_id, "status": state.get("status"), "tile": state.get("tile"),
                    "kind": state.get("kind"), "bundle_id": state.get("bundle_id")},
            "trace": trace, "trace_arrays": True}


@router.get("/runs/{run_id}/arrays/{aid}")
def run_trace_array(run_id: str, aid: str, cmap: str = Query("auto"),
                    lo: float | None = Query(default=None), hi: float | None = Query(default=None),
                    max_px: int = Query(320), authorization: str | None = Header(default=None)):
    require_token(authorization)
    if cmap not in ("auto", "viridis", "diverging", "magma", "gray", "categorical"):
        raise HTTPException(status_code=400, detail={
            "ok": False,
            "error": f"cmap {cmap!r} not in auto|viridis|diverging|magma|gray|categorical"})
    import json as _json
    body, meta = _run_local(_trace_array_worker, run_id, aid, cmap, lo, hi, max_px)
    return Response(content=body, media_type="image/png",
                    headers={"X-Img-Meta": _json.dumps(meta), "Cache-Control": "private, max-age=300"})


def _trace_array_worker(run_id: str, aid: str, cmap: str, lo, hi, max_px: int):
    runner = get_runner()
    rec = runner.get_trace_array(run_id, aid)
    if rec is None:
        raise HTTPException(status_code=404, detail={
            "ok": False,
            "error": f"no trace array {aid!r} for run {run_id} (traces live in memory and are "
                     "evicted with the run)"})
    return bg.trace_array_png(rec, cmap, lo, hi, max_px)


@router.get("/runs/{run_id}/arrays/{aid}/probe")
def run_trace_array_probe(run_id: str, aid: str, fx: float = Query(...), fy: float = Query(...),
                          authorization: str | None = Header(default=None)):
    require_token(authorization)
    return _run_local(_trace_probe_worker, run_id, aid, fx, fy)


def _trace_probe_worker(run_id: str, aid: str, fx: float, fy: float):
    runner = get_runner()
    rec = runner.get_trace_array(run_id, aid)
    if rec is None:
        raise HTTPException(status_code=404, detail={
            "ok": False, "error": f"no trace array {aid!r} for run {run_id}"})
    return bg.trace_array_probe(rec, fx, fy)


# --------------------------------------------------------------------------- #
# §6 pipelines
# --------------------------------------------------------------------------- #
@router.get("/runs/{run_id}/pipelines/{name}")
def run_pipeline_detail(run_id: str, name: str,
                        authorization: str | None = Header(default=None)):
    require_token(authorization)
    return _run_local(_pipeline_worker, run_id, name)


def _pipeline_worker(run_id: str, name: str):
    from engine.registry import Registry
    runner = get_runner()
    return bg.pipeline_detail(runner, Registry(bundles_dir=runner.bundles_dir), run_id, name)


__all__ = ["router", "require_token"]
