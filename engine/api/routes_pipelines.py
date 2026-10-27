"""Pipeline catalogue route — GET /api/pipelines.

Manifest-driven (Registry.describe()) plus an honest `implemented` flag:
a pipeline is implemented when its verdict module defines run() — the same
test the reference app used for its engine catalog.
"""
from __future__ import annotations

import importlib

from fastapi import APIRouter

from engine.registry import Registry

router = APIRouter(prefix="/api")


@router.get("/pipelines")
def list_pipelines():
    registry = Registry()
    out = []
    for entry in registry.describe():
        implemented = False
        try:
            mod = importlib.import_module(registry.module_for(entry["name"]))
            implemented = callable(getattr(mod, "run", None))
        except Exception:
            implemented = False
        out.append({**entry, "implemented": implemented})
    return {"ok": True, "pipelines": out,
            "implemented_count": sum(1 for p in out if p["implemented"])}
