"""GET /api/settings/defaults: the numbers the engine really uses when a request leaves them out.

The UI shows these instead of the word "default" and instead of numbers of its own (one source: config/*.yaml + env)."""
from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter

router = APIRouter(prefix="/api/settings")


@router.get("/defaults")
def defaults():
    from engine.common.config import load_common_config
    from engine.retrieval.scheduler.granule_poller import poll_lookback_days
    from engine.retrieval.settings import load_settings, tile_cell_size_m

    cfg, rs = load_common_config(), load_settings()
    w = cfg.window
    return {
        "ok": True,
        "lookback_days": w.lookback_days, "max_gcov_epochs": w.max_gcov_epochs, "max_gunw_pairs": w.max_gunw_pairs,
        "poll_lookback_days": poll_lookback_days(rs), "poll_products": list(rs.poll.get("products") or []),
        "products": ["GCOV", "GUNW", "GOFF"], "tile_cell_m": tile_cell_size_m(),
        "earthquake": asdict(cfg.earthquake),
        "sources": {"lookback_days": "config/common.yaml window.lookback_days (env NISAR_COMMON_WINDOW_LOOKBACK_DAYS)",
                    "max_gcov_epochs": "config/common.yaml window.max_gcov_epochs",
                    "max_gunw_pairs": "config/common.yaml window.max_gunw_pairs",
                    "poll_lookback_days": "config/retrieval.yaml poll.lookback_days (null = window.lookback_days)",
                    "earthquake": "config/common.yaml earthquake"},
    }
