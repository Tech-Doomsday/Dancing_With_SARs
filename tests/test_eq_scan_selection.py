"""A2/A3/A4/A6/A7/A8/A10 — the pure pieces of the earthquake verdict (no pyproj, no bundle build).

A fake bundle exposes only what prep/verdict read: stack(key), interferometric_stack_keys(), pairs_spanning(),
request.event_window, flags. The real SceneBundle methods are called unbound on a stand-in object."""
from types import SimpleNamespace as NS

import numpy as np
import pytest

from engine.common.config import CommonConfig, load_common_config
from engine.common.scene_bundle import SceneBundle
from engine.pipelines.earthquake_coseismic import prep, verdict
from engine.pipelines.earthquake_coseismic.types import EqParams


def _pair(ref, sec, resolved=True):
    return NS(ref_time=ref, sec_time=sec, times_resolved=resolved, granule_ids=(f"g_{ref}_{sec}",))


def _stack(key, pairs, n_epochs=0):
    return NS(key=key, interferometric=NS(pairs=pairs) if pairs is not None else None,
              backscatter=NS(epochs=[object()] * n_epochs) if n_epochs else None)


def _bundle(stacks, event_window=None, flags=()):
    ns = NS(stacks={s.key: s for s in stacks}, request=NS(event_window=event_window), flags=tuple(flags),
            _stack_score=getattr(SceneBundle, "_stack_score", None))
    ns.stack = lambda key=None: ns.stacks.get(key or SceneBundle.primary_key.fget(ns))
    ns.interferometric_stack_keys = lambda: SceneBundle.interferometric_stack_keys(ns)
    ns.pairs_spanning = lambda t0, t1: SceneBundle.pairs_spanning(ns, t0, t1)
    return ns


def _chain(n, start_day=1, step=12, prefix="2026-0"):
    """n consecutive 12-day pairs as ISO strings, oldest first."""
    from datetime import datetime, timedelta
    t0 = datetime(2026, 5, 1)
    out = []
    for i in range(n):
        a, b = t0 + timedelta(days=step * i), t0 + timedelta(days=step * (i + 1))
        out.append(_pair(a.strftime("%Y-%m-%dT00:00:00Z"), b.strftime("%Y-%m-%dT00:00:00Z")))
    return out


# ------------------------------------------------------------------ A2: scan mode uses more than the newest pair
def test_scan_mode_analyses_the_newest_n_pairs_not_just_one():
    b = _bundle([_stack("A115", _chain(10))])
    picked, reasons = prep.select_pairs(b, EqParams(scan_max_pairs=4))
    assert reasons == [] and len(picked) == 4
    secs = [b.stack(k).interferometric.pairs[i].sec_time for k, i in picked]
    assert secs == sorted(secs) and picked[-1] == ("A115", 9)          # the newest pair is among them, last
    assert picked[0] == ("A115", 6)                                    # and the 4 newest, not the oldest


def test_scan_mode_default_comes_from_config_and_covers_the_lookback():
    p = EqParams.from_triggers(None)
    assert p.scan_max_pairs == load_common_config().earthquake.scan_max_pairs >= 8
    picked, _ = prep.select_pairs(_bundle([_stack("A115", _chain(12))]), p)
    assert len(picked) == p.scan_max_pairs > 1


def test_unresolved_pairs_are_never_picked():
    pairs = _chain(3) + [_pair("2026-09-01T00:00:00Z", "2026-09-13T00:00:00Z", resolved=False)]
    picked, _ = prep.select_pairs(_bundle([_stack("A115", pairs)]), EqParams(scan_max_pairs=8))
    assert ("A115", 3) not in picked and len(picked) == 3


# ------------------------------------------------------------------ A3: both stacks, GUNW quality decides the primary
def test_scan_mode_analyses_every_stack_with_interferograms():
    b = _bundle([_stack("A115", _chain(5)), _stack("D108", _chain(5))])
    picked, _ = prep.select_pairs(b, EqParams(scan_max_pairs=2))
    assert {k for k, _ in picked} == {"A115", "D108"} and len(picked) == 4


def test_event_mode_finds_a_spanning_pair_that_only_the_other_stack_has():
    # D108 has the pair spanning the event; A115 (more pairs) does not. The old code only looked at one stack.
    a = _stack("A115", _chain(6))
    d = _stack("D108", [_pair("2026-07-01T00:00:00Z", "2026-08-20T00:00:00Z")])
    picked, reasons = prep.select_pairs(_bundle([a, d], event_window=("2026-07-20T00:00:00Z", "2026-07-25T00:00:00Z")),
                                        EqParams())
    assert picked == [("D108", 0)] and reasons == []


def test_primary_key_ranks_interferograms_before_gcov_epochs():
    many_gcov = _stack("D108", [_pair("2026-07-01T00:00:00Z", "2026-07-13T00:00:00Z")], n_epochs=20)
    many_gunw = _stack("A115", _chain(4), n_epochs=1)
    b = _bundle([many_gcov, many_gunw])
    assert SceneBundle.primary_key.fget(b) == "A115"          # old score: 1+20 beat 4+1 and picked D108
    assert b.interferometric_stack_keys() == ["A115", "D108"]


def test_a_gcov_only_stack_is_not_an_interferometric_stack():
    b = _bundle([_stack("A115", None, n_epochs=9), _stack("D108", _chain(2))])
    assert b.interferometric_stack_keys() == ["D108"]


# ------------------------------------------------------------------ A6: the window in the text is the real one
def test_analysed_window_and_phrase_state_dates_days_and_pair_count():
    an = [NS(ref_time="2026-07-25T00:00:00Z", sec_time="2026-08-06T00:00:00Z"),
          NS(ref_time="2026-07-13T00:00:00Z", sec_time="2026-07-25T00:00:00Z")]
    w = prep.analysed_window(an)
    assert (w["first"], w["last"], w["days"], w["n_pairs"], w["longest_pair_days"]) == ("2026-07-13", "2026-08-06", 24.0, 2, 12.0)
    assert prep.window_phrase(w) == "2026-07-13 .. 2026-08-06 (24 days, 2 interferograms)"
    one = prep.analysed_window(an[:1])
    assert prep.window_phrase(one).endswith("(12 days, 1 interferogram)")


def test_no_signal_text_names_the_analysed_window_not_the_whole_lookback():
    class _Summary(NS):
        def __getattr__(self, name):       # the narrative reads many numbers; every one we did not set is 0
            return 0.0
    s = _Summary(null_screen={"detected_fraction_mean": 0.0}, clusters={"count": 0, "min_pixels": 4})
    txt = verdict._headline("NO_SIGNAL", s, {"window_text": "2026-07-25 .. 2026-08-06 (12 days, 1 interferogram)"})
    assert "2026-07-25 .. 2026-08-06" in txt and "12 days" in txt
    assert "in the window and" not in txt
    nar = verdict._narrative("NO_SIGNAL", s, {"window_text": "W"}, {}, EqParams())
    assert "W" in str(nar)


# ------------------------------------------------------------------ A4: an incomplete GUNW listing is seen
def _flag(code, product=None):
    return NS(code=code, detail={"product": product} if product else {})


def test_gunw_listing_incomplete_reads_the_p01_flag():
    assert verdict.gunw_listing_incomplete(_bundle([], flags=[_flag("search_incomplete", "GUNW")]))
    assert verdict.gunw_listing_incomplete(_bundle([], flags=[_flag("search_incomplete")]))     # product unknown: assume it applies
    assert not verdict.gunw_listing_incomplete(_bundle([], flags=[_flag("search_incomplete", "GCOV")]))
    assert not verdict.gunw_listing_incomplete(_bundle([], flags=[_flag("fetch_error")]))
    assert not verdict.gunw_listing_incomplete(_bundle([]))


# ------------------------------------------------------------------ A7: the floor is stated and configurable
def test_floor_caveat_states_the_slowest_visible_rate_and_follows_the_config():
    w = {"longest_pair_days": 12.0}
    t10 = verdict.floor_caveat(EqParams(detection_floor_m=0.010), w)
    assert "10 mm" in t10 and "0.83 mm/day" in t10 and "volcano" in t10
    t5 = verdict.floor_caveat(EqParams(detection_floor_m=0.005), w)
    assert "5 mm" in t5 and "0.42 mm/day" in t5


def test_detection_floor_default_is_read_from_config_and_env_and_request_wins(monkeypatch):
    assert EqParams.from_triggers(None).detection_floor_m == pytest.approx(0.010)
    monkeypatch.setenv("NISAR_COMMON_EARTHQUAKE_DETECTION_FLOOR_MM", "4")
    assert EqParams.from_triggers(None).detection_floor_m == pytest.approx(0.004)
    assert EqParams.from_triggers({"eq_params": {"detection_floor_m": 0.02}}).detection_floor_m == pytest.approx(0.02)
    monkeypatch.setenv("NISAR_COMMON_EARTHQUAKE_SCAN_MAX_PAIRS", "3")
    assert EqParams.from_triggers(None).scan_max_pairs == 3
    assert EqParams.from_triggers({"eq_params": {"scan_max_pairs": 0}}).scan_max_pairs == 1      # bounded


def test_earthquake_section_is_not_part_of_the_bundle_hash():
    assert "earthquake" not in CommonConfig().chain_params()


# ------------------------------------------------------------------ A8: signal that touches the reference band warns
def _grid(n=40):
    ref = np.zeros((n, n), bool)
    ref[:5, :] = ref[-5:, :] = ref[:, :5] = ref[:, -5:] = True
    return np.zeros((n, n), np.uint8), ref, np.ones((n, n), bool)


def test_edge_contact_quiet_for_a_centred_signal():
    det, ref, valid = _grid()
    det[15:25, 15:25] = 1
    out = prep.signal_edge_contact(det, ref, valid, 0.10, 0.50)
    assert out["warn"] is False and out["in_reference_fraction"] == 0.0 and out["touches_edge"] is False


def test_edge_contact_warns_when_detections_sit_in_the_reference_band():
    det, ref, valid = _grid()
    det[15:25, 15:25] = 1
    det[:5, 10:30] = 2                                         # 100 px of 200 inside the band
    out = prep.signal_edge_contact(det, ref, valid, 0.10, 0.50)
    assert out["warn"] and out["in_reference_fraction"] == pytest.approx(0.5, abs=0.01)
    assert "reference band" in out["reasons"][0]


def test_edge_contact_warns_on_edge_touch_and_on_tile_wide_signal():
    det, ref, valid = _grid()
    det[0, 20] = 1
    assert prep.signal_edge_contact(det, np.zeros_like(ref), valid, 0.10, 0.50)["touches_edge"] is True
    det2 = np.ones((40, 40), np.uint8)
    assert "cover" in " ".join(prep.signal_edge_contact(det2, ref, valid, 0.99, 0.50)["reasons"])
    assert prep.signal_edge_contact(np.zeros((40, 40)), ref, valid, 0.1, 0.5)["warn"] is False


# ------------------------------------------------------------------ A10: assumptions are spelled out
def test_radar_assumptions_text_names_sign_looks_wavelength_and_the_missing_look_side():
    t = verdict.radar_assumptions_text()
    assert "phase sign -1" in t and "no ground-truth check" in t and "effective looks 100" in t
    assert "0.2384" in t and "look side unknown" in t and "flipped" in t


def test_supported_meaning_states_the_two_checks_and_is_not_a_probability():
    t = verdict.supported_meaning({"event_id": "us1", "amplitude_ratio": 1.1, "pattern_correlation": 0.8}, "SUPPORTED")
    assert "two checks" in t and "amplitude ratio 1.10" in t and "pattern correlation 0.80" in t and "not a probability" in t
    assert "did not pass both" in verdict.supported_meaning({"event_id": "x", "amplitude_ratio": 9.0, "pattern_correlation": 0.1}, "NOT_SUPPORTED")
