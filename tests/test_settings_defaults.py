"""B2 / B3: one source for the numbers the UI shows (lookback, caps, poller), and the manifest says what was cut."""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from engine.api.main import create_app  # noqa: E402
from engine.retrieval.scheduler.granule_poller import poll_lookback_days  # noqa: E402


def test_defaults_endpoint_returns_the_real_numbers_not_the_word_default():
    d = TestClient(create_app()).get("/api/settings/defaults").json()
    assert d["ok"] and (d["lookback_days"], d["max_gcov_epochs"], d["max_gunw_pairs"]) == (120, 24, 60)
    assert d["poll_lookback_days"] == d["lookback_days"]                  # was 45: three different windows in one UI
    assert d["tile_cell_m"] == 10_000 and d["earthquake"]["scan_pairs_per_stack"] == 4
    assert "config/common.yaml" in d["sources"]["lookback_days"]


def test_environment_changes_the_one_value_everywhere(monkeypatch):
    monkeypatch.setenv("NISAR_COMMON_WINDOW_LOOKBACK_DAYS", "90")
    monkeypatch.setenv("NISAR_COMMON_WINDOW_MAX_GUNW_PAIRS", "30")
    from engine.common.config import load_common_config
    load_common_config.cache_clear() if hasattr(load_common_config, "cache_clear") else None
    d = TestClient(create_app()).get("/api/settings/defaults").json()
    assert (d["lookback_days"], d["max_gunw_pairs"], d["poll_lookback_days"]) == (90, 30, 90)


def test_the_poller_has_its_own_value_only_when_one_is_configured():
    class Cfg:
        poll = {"lookback_days": 14}
    assert poll_lookback_days(Cfg()) == 14.0
    Cfg.poll = {"lookback_days": None}
    assert poll_lookback_days(Cfg()) == 120.0


def test_the_ui_source_has_no_private_lookback_numbers():
    """B2: no hard-coded 60 / 120 left in the UI; everything reads /api/settings/defaults."""
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "app-ui" / "src"
    bad = []
    for f in root.rglob("*.ts*"):
        t = f.read_text(encoding="utf-8")
        if re.search(r"DEFAULT_LOOKBACK_DAYS\s*=\s*\d+", t) or re.search(r"lookback\w*\s*[:=]\s*(60|120|45)\b", t):
            bad.append(str(f.relative_to(root)))
    assert bad == [], bad


def test_totals_tell_cache_hits_from_downloads():
    """B5: '0.00 MB of 2009.8 MB done' means every granule came from the local cache."""
    from engine.api.retrievals import RetrievalManager
    job = {"granules": [
        {"state": "stored", "source": "clip-cache", "bytes_planned": 1000, "bytes_done": 0},
        {"state": "stored", "source": "memory", "bytes_planned": 500, "bytes_done": 0},
        {"state": "stored", "source": "network", "bytes_planned": 200, "bytes_done": 200},
        {"state": "failed", "bytes_planned": 10, "bytes_done": 0}]}
    RetrievalManager._tally(job)
    t = job["totals"]
    assert (t["stored"], t["stored_from_cache"], t["stored_from_network"], t["bytes_done"], t["bytes_planned"]) == (3, 2, 1, 200, 1710)
