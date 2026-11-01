"""The numbers the UI shows as "default" — read from config, never typed into the UI.

One function, used by GET /api/health. Sources: config/common.yaml (window.*, earthquake.*), config/retrieval.yaml
(poll.*), each overridable by its env var. The UI shows these values and omits them from a request unless the
user changed them, so the engine's own config stays the single source of truth."""
from __future__ import annotations


def public_defaults() -> dict:
    from .config import load_common_config
    cfg = load_common_config()
    out: dict = {
        "lookback_days": cfg.window.lookback_days,        # the search window of every scan and retrieval
        "max_gcov_epochs": cfg.window.max_gcov_epochs,    # newest N backscatter epochs kept per stack
        "max_gunw_pairs": cfg.window.max_gunw_pairs,      # newest N interferograms kept per stack
        "scan_max_pairs": cfg.earthquake.scan_max_pairs,  # earthquake scan: newest N pairs analysed per stack
        "detection_floor_mm": cfg.earthquake.detection_floor_mm,
        "poll_lookback_days": None,
    }
    try:                       # the granule poller has its own window (late-processed granules); shown, not merged
        from ..retrieval.settings import load_settings
        out["poll_lookback_days"] = (load_settings().poll or {}).get("lookback_days")
    except Exception:          # retrieval deps missing: leave it null, never guess
        pass
    return out
