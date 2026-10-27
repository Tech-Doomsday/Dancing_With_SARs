"""5 — FastAPI app entrypoint.

    python -m uvicorn engine.api.main:app --port 8900

Serving slice for the unified UI (docs/IMPLEMENTATION_PLAN.md 10):
    GET  /api/health                   presence-only health
    GET  /api/pipelines                manifest catalogue + implemented flag
    GET  /api/bundles[/{id}]           ingested SceneBundles (metadata only)
    GET  /api/events                   live ComCat context (R1 adapter)
    POST /api/runs                     start a single-flight run
    GET  /api/runs[/{id}]              run state + PipelineResults
    GET  /api/runs/{id}/layers/{name}  evidence layer payload (b64 + bounds)
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from engine.common.config import data_root
from engine.registry import Registry

from .routes_bundles import router as bundles_router
from .routes_events import router as events_router
from .routes_pipelines import router as pipelines_router
from .routes_runs import router as runs_router


def create_app() -> FastAPI:
    app = FastAPI(title="nisar-hazard-engine", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(pipelines_router)
    app.include_router(bundles_router)
    app.include_router(runs_router)
    app.include_router(events_router)

    @app.get("/api/health")
    def health():
        data_dir = data_root()
        bundles_dir = Registry().bundles_dir
        return {
            "ok": True,
            "data_dir": str(data_dir),
            "data_dir_exists": data_dir.exists(),
            "bundles_dir": str(bundles_dir),
            "bundles_dir_exists": bundles_dir.exists(),
            "note": "presence-only health; it never implies verdicts",
        }

    return app


app = create_app()
