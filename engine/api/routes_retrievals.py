"""UI-build-3 — /api/retrievals/*: Resolve -> Plan -> Retrieve -> Inspect -> Preprocess as separate stages.

  POST /api/retrievals                    start (mode "plan" = bytes only, "retrieve" = plan + download)  -> 202 {id}
  GET  /api/retrievals                    recent retrievals
  GET  /api/retrievals/{id}               live state = the manifest (per-granule state, bytes, plan, flags)
  POST /api/retrievals/{id}/cancel        stop asking; in-flight ranges finish; stored clips stay usable
  POST /api/retrievals/{id}/preprocess    P01..P13 from the STORE (zero network) -> {run_id}
  GET  /api/retrievals/{id}/clips         stored granule/layer inventory                       (token)
  GET  /api/retrievals/{id}/clips/image   decimated PNG of a stored array (stride is reported)  (token)
  GET  /api/retrievals/{id}/clips/stats   measured min/max/mean/std + histogram                (token)
  GET  /api/retrievals/{id}/clips/probe   value at a stored pixel                              (token)
  GET  /api/retrievals/{id}/export.npz    one granule's stored layers as .npz                  (token)
  GET  /api/retrievals/{id}/download.zip  manifest + every stored granule as .npz              (token)
No handler waits on the retrieval worker: start returns at once, everything else reads the manifest or the store.
"""
from __future__ import annotations

import io
import json
import zipfile

import numpy as np
from fastapi import APIRouter, Header, HTTPException, Query, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .jobs import ScanError
from .retrievals import (ClipReader, RetrievalError, RetrievalManager, StoredRetriever, array_stats, clip_corners, displayable)
from .routes_background import require_token
from .state import get_runner

router = APIRouter(prefix="/api/retrievals")


def manager() -> RetrievalManager:
    r = get_runner()
    m = getattr(r, "_retrievals", None)
    if m is None:
        m = r._retrievals = RetrievalManager(r)
    return m


def _err(ex: Exception):
    status = getattr(ex, "status", 500)
    return HTTPException(status_code=status, detail={"ok": False, "error": str(ex)})


def _manifest(rid: str) -> dict:
    try:
        j = manager().get(rid)
    except RetrievalError as ex:
        raise _err(ex) from ex
    if j is None:
        raise HTTPException(status_code=404, detail={"ok": False, "error": f"unknown retrieval: {rid}"})
    return j


def _clips_dir():
    from engine.retrieval.settings import load_settings
    return load_settings().clips_dir


class RetrievalBody(BaseModel):
    lon: float = Field(..., ge=-180, le=180)
    lat: float = Field(..., ge=-90, le=90)
    mode: str = "retrieve"
    as_of: str | None = None
    lookback_days: float | None = Field(default=None, gt=0, le=3650)
    max_gcov_epochs: int | None = Field(default=None, ge=1, le=500)
    max_gunw_pairs: int | None = Field(default=None, ge=1, le=500)
    products: list[str] | None = None
    pols: list[str] | None = None


@router.post("", status_code=202)
def start_retrieval(body: RetrievalBody):
    try:
        rid = manager().start(**body.model_dump())
    except (ScanError, RetrievalError) as ex:
        raise _err(ex) from ex
    return {"ok": True, "id": rid, "status": "queued", "poll": f"/api/retrievals/{rid}"}


@router.get("")
def list_retrievals():
    return {"ok": True, "retrievals": manager().list()}


@router.get("/{rid}")
def get_retrieval(rid: str):
    return {"ok": True, **_manifest(rid)}


@router.post("/{rid}/cancel")
def cancel_retrieval(rid: str):
    _manifest(rid)
    try:
        return manager().cancel(rid)
    except RetrievalError as ex:
        raise _err(ex) from ex


@router.post("/{rid}/preprocess", status_code=202)
def preprocess_retrieval(rid: str):
    j = _manifest(rid)
    if not any(g.get("state") == "stored" for g in j["granules"]):
        raise HTTPException(status_code=409, detail={"ok": False, "error": "nothing is stored in this retrieval yet"})
    runner = get_runner()
    rq = j["request"]
    try:
        run_id = runner.start_scan(lon=rq["lon"], lat=rq["lat"], as_of=j["window"]["as_of"],
                                   lookback_days=j["window"]["lookback_days"],
                                   retriever_factory=lambda: StoredRetriever(j, _clips_dir()),
                                   require_credentials=False, retrieval_id=rid)
    except ScanError as ex:
        raise _err(ex) from ex
    except RuntimeError as ex:          # single-flight
        raise HTTPException(status_code=409, detail={"ok": False, "error": str(ex)}) from ex
    return {"ok": True, "run_id": run_id, "retrieval_id": rid,
            "note": "preprocessing from the store: no network calls"}


# ------------------------------------------------------------------ inspect
def _reader(rid: str, authorization):
    require_token(authorization)
    return ClipReader(_manifest(rid), _clips_dir())


@router.get("/{rid}/clips")
def clips(rid: str, authorization: str | None = Header(default=None)):
    rd = _reader(rid, authorization)
    return {"ok": True, "clips": rd.listing(),
            "note": "arrays are read from the engine's store; nothing here re-downloads"}


@router.get("/{rid}/clips/image")
def clip_image(rid: str, granule: str, grid: str, layer: str, max_px: int = Query(320, ge=16, le=1024),
               authorization: str | None = Header(default=None)):
    from . import background_img as bimg
    rd = _reader(rid, authorization)
    try:
        a, grp = rd.array(granule, grid, layer)
        v = displayable(a)
        corners, corners_note = clip_corners(grp)
    except RetrievalError as ex:
        raise _err(ex) from ex
    view = bimg.finite_view(v)
    if not np.isfinite(view).any():
        raise HTTPException(status_code=422, detail={"ok": False, "error": "no valid pixels in this stored array"})
    shown, stride = bimg.decimate(view, max_px)
    lo, hi = bimg.auto_range(shown, symmetric=False)
    png = bimg.render(shown, "viridis", lo, hi)
    meta = {"lo": lo, "hi": hi, "cmap": "viridis", "full_shape": list(v.shape), "shown_shape": list(shown.shape),
            "decimation_stride": int(stride),
            "corners_lonlat": corners, "corners_note": corners_note,
            "label": (f"decimated by {int(stride)}x (every {int(stride)}th pixel, values not resampled)"
                      if stride > 1 else "full resolution")}
    return Response(content=png, media_type="image/png",
                    headers={"X-Img-Meta": json.dumps(meta), "Cache-Control": "private, max-age=300"})


@router.get("/{rid}/clips/stats")
def clip_stats(rid: str, granule: str, grid: str, layer: str, authorization: str | None = Header(default=None)):
    rd = _reader(rid, authorization)
    try:
        a, _ = rd.array(granule, grid, layer)
        return {"ok": True, **array_stats(a)}
    except RetrievalError as ex:
        raise _err(ex) from ex


@router.get("/{rid}/clips/probe")
def clip_probe(rid: str, granule: str, grid: str, layer: str, row: int = Query(..., ge=0), col: int = Query(..., ge=0),
               authorization: str | None = Header(default=None)):
    rd = _reader(rid, authorization)
    try:
        a, grp = rd.array(granule, grid, layer)
        v = displayable(a)
    except RetrievalError as ex:
        raise _err(ex) from ex
    if row >= v.shape[0] or col >= v.shape[1]:
        raise HTTPException(status_code=400, detail={"ok": False, "error": f"pixel outside {list(v.shape)}"})
    x, y = np.asarray(grp["x"][:]), np.asarray(grp["y"][:])
    val = float(v[row, col])
    return {"ok": True, "row": row, "col": col, "value": val if np.isfinite(val) else None,
            "x": float(x[col]), "y": float(y[row]), "epsg": int(grp.attrs["epsg"])}


def _npz_bytes(rd: ClipReader, gid: str) -> bytes:
    arrays = {}
    for row in rd.listing():
        if row["granule_id"] == gid:
            a, _ = rd.array(gid, row["grid"], row["layer"])
            arrays[f"{row['grid']}::{row['layer']}"] = a
    if not arrays:
        raise RetrievalError(404, f"nothing stored for granule {gid!r}")
    buf = io.BytesIO()
    np.savez_compressed(buf, **arrays)
    return buf.getvalue()


@router.get("/{rid}/export.npz")
def export_npz(rid: str, granule: str, authorization: str | None = Header(default=None)):
    rd = _reader(rid, authorization)
    try:
        body = _npz_bytes(rd, granule)
    except RetrievalError as ex:
        raise _err(ex) from ex
    return Response(content=body, media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{granule}.npz"'})


@router.get("/{rid}/download.zip")
def download_zip(rid: str, authorization: str | None = Header(default=None)):
    rd = _reader(rid, authorization)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        z.writestr("manifest.json", json.dumps(rd.manifest, indent=1, default=str))
        for g in rd.manifest["granules"]:
            if g.get("state") == "stored":
                try:
                    z.writestr(f"clips/{g['granule_id']}.npz", _npz_bytes(rd, g["granule_id"]))
                except RetrievalError:
                    continue
    return Response(content=buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{rid}.zip"'})
