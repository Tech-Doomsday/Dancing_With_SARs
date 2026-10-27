"""Bundle registry routes — GET /api/bundles, /api/bundles/{bundle_id}.

Presence-only metadata from the content-addressed bundle store
(<data>/bundles/<bundle_id>/bundle.json). No arrays are served here.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException

from engine.registry import Registry

router = APIRouter(prefix="/api")


def _bundles_dir() -> Path:
    return Registry().bundles_dir


def _meta(bundle_dir: Path) -> dict | None:
    meta_path = bundle_dir / "bundle.json"
    if not meta_path.exists():
        return None
    try:
        return json.loads(meta_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


@router.get("/bundles")
def list_bundles():
    root = _bundles_dir()
    out = []
    if root.is_dir():
        for d in sorted(root.iterdir()):
            if not d.is_dir():
                continue
            meta = _meta(d)
            if meta is None:
                continue
            tile = meta.get("tile") or {}
            out.append({
                "bundle_id": meta.get("bundle_id") or d.name,
                "tile": tile.get("id"),
                "epsg": tile.get("epsg"),
                "center_lonlat": tile.get("center_lonlat"),
                "bbox_lonlat": tile.get("bbox_lonlat"),
                "epoch": meta.get("epoch"),
                "status": meta.get("status"),
                "created_at": meta.get("created_at") or (meta.get("provenance") or {}).get("created_at"),
            })
    return {"ok": True, "bundles": out}


@router.get("/bundles/{bundle_id}")
def get_bundle(bundle_id: str):
    d = _bundles_dir() / bundle_id
    meta = _meta(d)
    if meta is None:
        raise HTTPException(status_code=404, detail={"ok": False, "error": f"unknown bundle: {bundle_id}"})
    tile = meta.get("tile") or {}
    depth = meta.get("depth") or {}
    routing = meta.get("routing") or {}
    return {
        "ok": True,
        "bundle": {
            "bundle_id": meta.get("bundle_id") or bundle_id,
            "tile": tile, "epoch": meta.get("epoch"), "status": meta.get("status"),
            "depth": {k: depth.get(k) for k in ("n_gunw_pairs", "n_gunw_dates", "n_gcov_epochs", "cold_start")},
            "routing_decisions": routing.get("decisions"),
            "applicable": routing.get("applicable"),
            "flags": [f.get("code") for f in (meta.get("flags") or [])][:20],
        },
    }
