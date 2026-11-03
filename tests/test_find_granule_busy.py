"""find_granule must locate a granule by its id even when the provider's global time-window list is
busy and gets cut at max_results (the 'not found by the provider search' error on the map's SAR layer).
Offline: a window-aware fake provider that sorts newest-first and truncates like asf_search does."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.api.background import BackgroundError, find_granule  # noqa: E402
from engine.retrieval.clients.types import GranuleRef  # noqa: E402

TARGET = ("NISAR_L2_PR_GUNW_030_069_A_013_031_4000_SH_20260910T232058_20260910T232132_"
          "20260922T232058_20260922T232132_P05023_N_F_J_001")


def _t(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _ref(gid: str, start: datetime) -> GranuleRef:
    return GranuleRef(gid, f"https://example.invalid/{gid}.h5", "GUNW",
                      start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                      (start + timedelta(seconds=34)).strftime("%Y-%m-%dT%H:%M:%SZ"), 69, 13, "ASCENDING")


class BusyProvider:
    """Many GUNW granules around the target's time (other frames / pairs); search() is window-aware,
    newest first, and cut at max_results — exactly what a global bbox-less asf_search does."""

    def __init__(self, n_decoys=120, by_name=True, name_lookup_raises=False, target_start=None):
        t = _t("2026-09-10T23:20:58Z")
        self.target = _ref(TARGET, _t(target_start) if target_start else t)
        self.items = [self.target]
        for i in range(n_decoys):                           # decoys spread over +/- 2 h, never the target's instant
            off = timedelta(seconds=-7200 + 120 * i + 7)
            self.items.append(_ref(f"NISAR_L2_PR_GUNW_030_0{i % 90:02d}_A_0{i % 99:02d}_031_4000_SH_decoy{i:04d}", t + off))
        self.by_name, self.name_lookup_raises = by_name, name_lookup_raises
        self.last_search_truncated = False
        self.search_calls: list = []

    def search(self, bbox, t0, t1, product, fresh=False, max_results=50):
        self.search_calls.append((t0, t1, max_results))
        a, b = _t(t0), _t(t1)
        hit = [r for r in self.items if a <= _t(r.start) <= b]
        hit.sort(key=lambda r: r.start, reverse=True)
        self.last_search_truncated = len(hit) > max_results
        return hit[:max_results], False

    def find_by_id(self, gid):
        if self.name_lookup_raises:
            raise TypeError("granule_list not supported")
        return self.target if (self.by_name and gid == TARGET) else None


def test_target_found_in_busy_window_via_narrow_search():
    src = BusyProvider()
    ref, dec = find_granule(src, TARGET)
    assert ref.granule_id == TARGET and dec["product_decoded"] == "GUNW"
    # the first (narrow) search already finds it; the old +/- 2 h search would have cut it off at 50
    assert len(src.search_calls) == 1
    t0, t1, _ = src.search_calls[0]
    assert (_t(t1) - _t(t0)) <= timedelta(minutes=10)
    wide = BusyProvider()
    refs, _ = wide.search(None, "2026-09-10T21:20:58Z", "2026-09-11T01:20:58Z", "GUNW", max_results=50)
    assert TARGET not in [r.granule_id for r in refs]       # the failure mode this guards against


def test_target_found_by_name_when_windows_do_not_list_it():
    src = BusyProvider(target_start="2026-09-05T00:00:00Z")   # provider's temporal extent is not the id's start
    ref, _ = find_granule(src, TARGET)
    assert ref.granule_id == TARGET


def test_name_lookup_failure_falls_back_to_wide_window_with_full_budget():
    src = BusyProvider(n_decoys=0, name_lookup_raises=True, target_start="2026-09-10T22:00:00Z")  # only the wide window reaches it
    ref, _ = find_granule(src, TARGET)
    assert ref.granule_id == TARGET
    assert src.search_calls[-1][2] == 250


def test_not_found_says_what_was_tried_and_flags_truncation():
    src = BusyProvider(by_name=False)
    src.items = [r for r in src.items if r.granule_id != TARGET] + [
        _ref(f"NISAR_L2_PR_GUNW_030_001_A_001_031_4000_SH_pad{i:04d}", _t("2026-09-10T23:20:58Z") + timedelta(seconds=i % 100))
        for i in range(400)]
    with pytest.raises(BackgroundError) as ei:
        find_granule(src, TARGET)
    msg = str(ei.value)
    assert ei.value.status == 404 and "not found by the provider search" in msg
    assert "by granule name" in msg and "list truncated" in msg and "result limit" in msg


def test_unknown_layout_still_refused_with_decoder_text():
    with pytest.raises(BackgroundError) as ei:
        find_granule(BusyProvider(), "totally-unknown-id")
    assert ei.value.status == 404 and "no verified filename layout" in str(ei.value)
