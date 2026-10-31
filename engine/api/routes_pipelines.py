"""Pipeline catalogue route — GET /api/pipelines.

Manifest-driven (Registry.describe()) plus an honest `implemented` flag:
a pipeline is implemented when its verdict module defines run() — the same
test the reference app used for its engine catalog.
"""
from __future__ import annotations

from fastapi import APIRouter

from engine.registry import Registry

router = APIRouter(prefix="/api")


@router.get("/pipelines")
def list_pipelines():
    registry = Registry()
    out = [{**entry, "implemented": registry.is_modelled(entry["name"])} for entry in registry.describe()]
    n = sum(1 for p in out if p["implemented"])
    # declared_count / modelled_count are computed live from the manifests and the registry's own
    # entry-function check; implemented_count is kept for older callers (same number as modelled_count)
    return {"ok": True, "pipelines": out, "implemented_count": n,
            "declared_count": len(out), "modelled_count": n}
