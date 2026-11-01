"""B2/B3: the 'default' numbers the UI shows come from config, one source, env-overridable."""
from engine.common.defaults import public_defaults


def test_defaults_mirror_the_shipped_config():
    d = public_defaults()
    assert d["lookback_days"] == 120 and d["max_gcov_epochs"] == 24 and d["max_gunw_pairs"] == 60
    assert d["scan_max_pairs"] >= 1 and d["detection_floor_mm"] == 10
    assert d["poll_lookback_days"] in (45, None)          # the poller keeps its own window; None only if retrieval deps are missing


def test_defaults_follow_env_overrides(monkeypatch):
    monkeypatch.setenv("NISAR_COMMON_WINDOW_LOOKBACK_DAYS", "90")
    monkeypatch.setenv("NISAR_COMMON_WINDOW_MAX_GUNW_PAIRS", "30")
    d = public_defaults()
    assert d["lookback_days"] == 90 and d["max_gunw_pairs"] == 30


def test_incomplete_search_text_is_tidied_and_short():
    """B4: asf_search glues sentences ('results.Expected 17, got 15'); the flag text must not."""
    from engine.common.p01_granule_resolution import incomplete_detail
    raw = "CMR returned fewer results.Expected 17 granules, got 15\n(page 2 missing)"
    out = incomplete_detail(raw)
    assert "results. Expected 17" in out and "\n" not in out
    assert len(incomplete_detail("x" * 500)) == 160
