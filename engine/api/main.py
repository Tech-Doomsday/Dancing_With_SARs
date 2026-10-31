"""5 — FastAPI app entrypoint.

    python -m uvicorn engine.api.main:app --port 8900

Serving slice for the unified UI (docs/IMPLEMENTATION_PLAN.md 10 + the port design's API set):
    GET  /api/health                   presence-only health (+ scan/credential/cache/granule flags)
    GET  /api/pipelines                manifest catalogue + implemented flag + lanes
    GET  /api/bundles[/{id}]           ingested SceneBundles (metadata only)
    GET  /api/tiles[/{id}/verdicts]    canonical verdicts from verdicts.db (4.5)
    GET  /api/events                   live ComCat context (R1 adapter)
    POST /api/runs                     start a single-flight run (normal lane inline, heavy via jobs)
    GET  /api/runs[/{id}]              run state + PipelineResults (slim=1: layer metadata only)
    GET  /api/runs/{id}/layers/{name}  evidence layer payload (b64 + bounds) — deprecated, first match
    GET  /api/runs/{id}/pipelines/{p}/layers/{name}   pipeline-scoped layer payload (D5)
    GET  /api/jobs/{id}                heavy-lane job state (progress + cache)
    GET  /api/tile-at                  lat/lon -> the MGRS tile that contains it (§5.2)
    POST /api/scans                    scan one tile by location (§5.3; NISAR_ENABLE_SCANS=1)
    GET  /api/granules                 granule availability: per-tile (lon/lat, §5.5) or bbox
                                       (west/south/east/north, globe task E8 — the Granule
                                       Explorer; antimeridian box -> 400, 429 + Retry-After);
                                       metadata only
    GET  /api/background/*             GATED background routes (UI-build-2 §1/§4.5/§5/§6):
                                       bearer NISAR_BACKGROUND_TOKEN required (unset -> 503);
                                       granule / preview / probe / stack-pair + run
                                       preprocessing traces and per-pipeline detail
"""
from __future__ import annotations

import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from engine.common.config import data_root
from engine.registry import Registry

from .jobs import earthdata_present, scans_enabled
from .routes_background import router as background_router
from .routes_bundles import router as bundles_router
from .routes_events import router as events_router
from .routes_jobs import router as jobs_router
from .routes_pipelines import router as pipelines_router
from .routes_runs import router as runs_router
from .routes_retrievals import router as retrievals_router
from .routes_scanner import router as scanner_router
from .routes_tiles import router as tiles_router

# Without a root handler every INFO line from nisar.* (scan steps, ASF search, byte heartbeat) is dropped.
# NISAR_LOG_LEVEL=WARNING restores the old quiet behaviour.
if not logging.getLogger().handlers:
    logging.basicConfig(level=os.environ.get("NISAR_LOG_LEVEL", "INFO").upper(),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")

_LOCAL_ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000"]


def _ui_origins() -> list[str]:
    """D9: the two local dev origins plus NISAR_UI_ORIGINS (comma-separated). No wildcard —
    the engine API can start scans, so an open CORS policy would be an abuse vector."""
    extra = [o.strip() for o in os.environ.get("NISAR_UI_ORIGINS", "").split(",") if o.strip()]
    out: list[str] = []
    for o in [*extra, *_LOCAL_ORIGINS]:
        if o not in out:
            out.append(o)
    return out


def create_app() -> FastAPI:
    app = FastAPI(title="nisar-hazard-engine", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_ui_origins(),
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(pipelines_router)
    app.include_router(bundles_router)
    app.include_router(tiles_router)
    app.include_router(runs_router)
    app.include_router(events_router)
    app.include_router(jobs_router)
    app.include_router(scanner_router)
    app.include_router(background_router)
    app.include_router(retrievals_router)

    @app.get("/api/health")
    def health():
        data_dir = data_root()
        bundles_dir = Registry().bundles_dir
        cache_enabled = True
        cache_reason = None
        try:
            from .state import get_runner
            cache = get_runner().cache
            cache_enabled, cache_reason = cache.enabled, cache.disabled_reason
        except Exception as ex:  # a broken singleton must not take health down
            cache_enabled, cache_reason = False, f"{type(ex).__name__}: {ex}"
        try:
            from engine.common.sqlite_util import resolve_db_dir
            db_dir = str(resolve_db_dir(data_dir))
        except Exception as ex:
            db_dir = f"unavailable: {type(ex).__name__}: {ex}"
        creds = "present" if earthdata_present() else "absent"
        granule_search = {
            "status": "enabled" if creds == "present" else "disabled",
            "reason": None if creds == "present" else
            "Earthdata credentials absent — ASF search needs EARTHDATA_TOKEN (or EARTHDATA_USERNAME + EARTHDATA_PASSWORD)",
        }
        try:
            from .state import get_runner as _gr
            busy = _gr().busy_info()
        except Exception:
            busy = None
        return {
            "ok": True,
            "busy": busy,     # {kind, run_id, step, started_at} | None — never touches the worker pool
            "data_dir": str(data_dir),
            "data_dir_exists": data_dir.exists(),
            "db_dir": db_dir,
            "bundles_dir": str(bundles_dir),
            "bundles_dir_exists": bundles_dir.exists(),
            "scans_enabled": scans_enabled(),
            "earthdata_credentials": creds,
            "cache_write": {"status": "enabled" if cache_enabled else "disabled",
                            "reason": cache_reason},
            "granule_search": granule_search,
            "note": "presence-only health; it never implies verdicts",
        }

    return app


app = create_app()
