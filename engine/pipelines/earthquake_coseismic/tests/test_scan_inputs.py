"""A1-A4, A6-A11: what the earthquake verdict really uses, and says it uses.

Real engine chain on the synthetic fixture scenes (no network): scan mode (no event window), several pairs, two stacks,
a short ASF listing, the detection floor, the assumed radar conventions and the reference ring."""
from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("pyproj")

from engine.common import DirectoryAncillary, build_scene_bundle  # noqa: E402
from engine.common.frozen import FrozenDict  # noqa: E402
from engine.common.scene_bundle import Flag  # noqa: E402
from engine.pipelines.earthquake_coseismic import fetch as eq_fetch  # noqa: E402
from engine.pipelines.earthquake_coseismic import inputs  # noqa: E402
from engine.pipelines.earthquake_coseismic import prep  # noqa: E402
from engine.pipelines.earthquake_coseismic.types import EqParams  # noqa: E402
from engine.pipelines.earthquake_coseismic.verdict import _edge_touch, _status_meaning  # noqa: E402
from engine.pipelines.earthquake_coseismic.verdict import run as eq_run  # noqa: E402
from tests.common_fixtures import FakeRetriever, GeomSpec, default_tile, dt, make_scene, write_ancillary  # noqa: E402

from .fixtures_eq import make_null_provider  # noqa: E402

AS_OF = dt("2026-08-10")
TILE = default_tile()


@pytest.fixture(autouse=True)
def _null_events():
    eq_fetch.set_event_provider(make_null_provider())
    yield
    eq_fetch.reset_event_provider()


def _bundle(tmp_path, specs, *, event_window=None, triggers=None, flags=()):
    b = build_scene_bundle(TILE, FakeRetriever(specs), as_of=AS_OF, event_window=event_window, triggers=triggers,
                           ancillary=DirectoryAncillary(write_ancillary(tmp_path / "anc", TILE)))
    return dataclasses.replace(b, flags=tuple(b.flags) + tuple(flags)) if flags else b


def _quiet_specs():
    return make_scene(TILE, n_epochs=4, pairs=((0, 1), (1, 2), (2, 3)), dlos=0.0, event=False)


def _old_signal_new_quiet_specs():
    """The quake sits in an OLDER pair (epochs 2->3); a newer pair (3->4) is quiet. Newest-pair-only analysis misses it."""
    start = "2026-06-20"                                                   # five 12-day epochs end 2026-08-07, before as_of
    older = make_scene(TILE, n_epochs=4, pairs=((0, 1), (1, 2), (2, 3)), dlos=0.05, start=start)
    newer = [s for s in make_scene(TILE, n_epochs=5, pairs=((3, 4),), dlos=0.0, event=False, start=start) if s.kind == "GUNW"]
    return older + newer


def _pairs(result):
    return [r for r in result["detail"]["pairs_analysed"] if not r.get("skipped")]


# ------------------------------------------------------------------------------------------ A2 / A3
def test_scan_mode_analyses_several_pairs_not_only_the_newest(tmp_path):
    bundle = _bundle(tmp_path, _quiet_specs())
    assert not bundle.request.event_window
    many = eq_run(bundle).to_dict()
    assert len(_pairs(many)) == 3                                          # config earthquake.scan_pairs_per_stack = 4
    one = eq_run(_bundle(tmp_path, _quiet_specs(), triggers={"eq_params": {"scan_pairs_per_stack": 1}})).to_dict()
    assert len(_pairs(one)) == 1                                           # the old behaviour is one setting away


def test_a_quake_in_an_older_pair_is_found_only_when_more_than_the_newest_pair_is_used(tmp_path):
    specs = _old_signal_new_quiet_specs()
    newest_only = eq_run(_bundle(tmp_path, specs, triggers={"eq_params": {"scan_pairs_per_stack": 1}})).to_dict()
    assert newest_only["status"] == "NO_SIGNAL"                            # the false negative the old code produced
    wider = eq_run(_bundle(tmp_path, specs)).to_dict()
    assert wider["status"] != "NO_SIGNAL"
    sel = wider["detail"]["inputs"]["selected_pair"]
    assert sel["sec_time"].startswith("2026-07-26")                        # the older pair carried the movement
    assert any(r["selected"] and r["detected_pixels"] > 0 for r in _pairs(wider))


def test_both_stacks_are_analysed_and_the_one_with_the_signal_drives_the_verdict(tmp_path):
    asc = make_scene(TILE, n_epochs=6, pairs=((0, 1), (1, 2)), dlos=0.0, event=False)            # lots of GCOV, quiet GUNW
    desc = make_scene(TILE, n_epochs=4, pairs=((0, 1), (1, 2), (2, 3)), dlos=0.05, track=12, direction="D",
                      start="2026-07-03", geom=GeomSpec(heading_deg=190.0, look="left", incidence_deg=35.0))
    bundle = _bundle(tmp_path, asc + desc)
    assert set(bundle.stacks) == {"A005", "D012"}
    res = eq_run(bundle).to_dict()
    stacks = res["detail"]["inputs"]["stacks"]
    assert stacks["analysed"] == ["A005", "D012"] and stacks["verdict_stack"] == "D012"
    assert {r["stack_key"] for r in _pairs(res)} == {"A005", "D012"}
    assert any(e["id"] == "inputs.used" and "stack D012" in e["claim"] for e in res["evidence"])
    # one stack only (setting): only the best-quality stack is analysed, and it is said
    single = eq_run(_bundle(tmp_path, asc + desc, triggers={"eq_params": {"analyse_all_stacks": False}})).to_dict()
    assert len(single["detail"]["inputs"]["stacks"]["analysed"]) == 1


def test_primary_stack_is_chosen_by_gunw_quality_not_by_gcov_epochs(tmp_path):
    many_gcov = make_scene(TILE, n_epochs=8, pairs=((0, 1),), dlos=0.0, event=False)            # 8 epochs + 1 pair
    many_gunw = make_scene(TILE, n_epochs=2, pairs=((0, 1),), dlos=0.0, event=False, track=12, direction="D",
                           start="2026-07-03", geom=GeomSpec(heading_deg=190.0, look="left", incidence_deg=35.0))
    many_gunw += [s for s in make_scene(TILE, n_epochs=5, pairs=((1, 2), (2, 3), (3, 4)), dlos=0.0, event=False, track=12,
                                        direction="D", start="2026-07-03",
                                        geom=GeomSpec(heading_deg=190.0, look="left", incidence_deg=35.0))
                  if s.kind == "GUNW"]
    b = _bundle(tmp_path, many_gcov + many_gunw)
    n = lambda k: len(b.stacks[k].interferometric.pairs)                                       # noqa: E731
    assert n("D012") > n("A005") and len(b.stacks["A005"].backscatter.epochs) > len(b.stacks["D012"].backscatter.epochs)
    assert b.primary_key == "D012"                                         # the old pairs+epochs score picked A005
    assert b.stack_keys_by_quality()[0] == "D012"


# ------------------------------------------------------------------------------------------ A4
def _flag(product, expected=17, got=15):
    return Flag("search_incomplete", "warn", "P01", f"{product} listing may be missing granules: "
                f"CMR returned page of incomplete results. Expected {expected} results, got {got}.",
                FrozenDict(product=product, expected=expected, got=got))


def test_a_short_gunw_listing_turns_a_quiet_verdict_into_insufficient_evidence(tmp_path):
    clean = eq_run(_bundle(tmp_path, _quiet_specs())).to_dict()
    assert clean["status"] == "NO_SIGNAL"
    res = eq_run(_bundle(tmp_path, _quiet_specs(), flags=(_flag("GUNW"),))).to_dict()
    assert res["status"] == "INSUFFICIENT_EVIDENCE" and res["detail"]["decision"]["rule"] == "search_incomplete"
    assert res["detail"]["decision"]["downgraded_from"] == "NO_SIGNAL" and res["raw_p"] is None
    claim = next(e["claim"] for e in res["evidence"] if e["id"] == "input.listing_incomplete")
    assert "expected 17, got 15" in claim and "may therefore not be the newest" in claim
    assert res["detail"]["inputs"]["listing"]["complete"] is False
    assert "incomplete" in res["summary"].lower()


def test_a_short_gcov_listing_does_not_touch_a_gunw_only_pipeline(tmp_path):
    res = eq_run(_bundle(tmp_path, _quiet_specs(), flags=(_flag("GCOV"),))).to_dict()
    assert res["status"] == "NO_SIGNAL" and res["detail"]["inputs"]["listing"]["complete"] is True


# ------------------------------------------------------------------------------------------ A6 / A1 / B9
def test_no_signal_text_names_the_window_that_was_really_analysed(tmp_path):
    res = eq_run(_bundle(tmp_path, _quiet_specs())).to_dict()
    assert res["status"] == "NO_SIGNAL"
    win = res["detail"]["inputs"]["window"]
    assert win["n_pairs"] == 3 and win["covered_days"] == pytest.approx(36.0, abs=0.5) and win["search_days"] == pytest.approx(120.0, abs=0.5)
    claim = next(e["claim"] for e in res["evidence"] if e["id"] == "signal.none")
    assert "this tile in this window" not in claim and "3 interferograms analysed" in claim
    assert "36 of the 120-day search window" in claim and "slow movement" in claim
    assert "3 interferograms analysed" in res["summary"]
    used = next(e for e in res["evidence"] if e["id"] == "inputs.used")
    assert "display values, not inputs" in used["claim"] and used["support"]["selected"]["stack"] == "A005"


def test_window_summary_merges_overlaps_and_handles_nothing():
    recs = [{"stack_key": "A", "ref_time": "2026-07-01T00:00:00Z", "sec_time": "2026-07-13T00:00:00Z"},
            {"stack_key": "D", "ref_time": "2026-07-05T00:00:00Z", "sec_time": "2026-07-17T00:00:00Z"},
            {"stack_key": "A", "skipped": True, "ref_time": "x", "sec_time": "y"}]
    w = inputs.window_summary(recs, SimpleNamespace(t0="2026-05-01T00:00:00Z", t1="2026-08-29T00:00:00Z"))
    assert w["n_pairs"] == 2 and w["covered_days"] == 16.0 and w["search_days"] == 120.0
    assert inputs.window_summary([], None)["text"] == "no interferogram window could be analysed"


# ------------------------------------------------------------------------------------------ A7
def test_the_floor_is_stated_as_a_rate_and_is_a_setting(tmp_path):
    res = eq_run(_bundle(tmp_path, _quiet_specs())).to_dict()
    floor = next(e for e in res["evidence"] if e["id"] == "detection.floor")
    sup = floor["support"]
    assert sup["floor_mm"] == 10.0 and sup["pair_days"] == pytest.approx(12.0, abs=0.1)
    assert 250 < sup["min_detectable_rate_mm_per_year"] < 600
    assert "swelling volcano" in floor["claim"] and "earthquake.detection_floor_m" in floor["claim"]
    low = eq_run(_bundle(tmp_path, _quiet_specs(), triggers={"eq_params": {"detection_floor_m": 0.003}})).to_dict()
    assert next(e for e in low["evidence"] if e["id"] == "detection.floor")["support"]["floor_mm"] == 3.0


def test_settings_come_from_the_config_file_and_env_not_from_code(monkeypatch):
    base = EqParams.defaults()
    assert (base.detection_floor_m, base.far_field_margin, base.scan_pairs_per_stack) == (0.01, 0.12, 4)
    monkeypatch.setenv("NISAR_COMMON_EARTHQUAKE_DETECTION_FLOOR_M", "0.02")
    monkeypatch.setenv("NISAR_COMMON_EARTHQUAKE_REFERENCE_STRATEGY", "fullFrameMedian")
    monkeypatch.setenv("NISAR_COMMON_EARTHQUAKE_SCAN_PAIRS_PER_STACK", "7")
    got = EqParams.from_triggers(None)
    assert (got.detection_floor_m, got.reference_strategy, got.scan_pairs_per_stack) == (0.02, "fullFrameMedian", 7)
    assert EqParams.from_triggers({"eq_params": {"detection_floor_m": 0.005}}).detection_floor_m == 0.005   # request wins


# ------------------------------------------------------------------------------------------ A8
def test_edge_touch_is_the_detected_share_of_the_reference_ring():
    ring = np.zeros((10, 10), bool)
    ring[0, :] = ring[-1, :] = True
    hit = np.zeros((10, 10), np.uint8)
    hit[0, :5] = 1
    a = SimpleNamespace(reference_mask=ring)
    assert _edge_touch(a, {"detected": hit}, EqParams()) == pytest.approx(5 / 20)
    assert _edge_touch(a, {"detected": hit}, EqParams(reference_strategy="fullFrameMedian")) is None
    assert _edge_touch(SimpleNamespace(reference_mask=np.zeros((3, 3), bool)), {"detected": hit}, EqParams()) is None


def test_edge_warning_fires_when_the_ring_holds_signal_and_names_the_remedy(tmp_path):
    res = eq_run(_bundle(tmp_path, _quiet_specs(), triggers={"eq_params": {"edge_touch_max_fraction": -1.0}})).to_dict()
    ev = next(e for e in res["evidence"] if e["id"] == "reference.edge_signal")
    assert "lower bounds" in ev["claim"] and ev["support"]["reference_strategy"] == "farField"
    assert any("fullFrameMedian" in w for w in res["warnings"])
    quiet = eq_run(_bundle(tmp_path, _quiet_specs())).to_dict()
    assert not any(e["id"] == "reference.edge_signal" for e in quiet["evidence"])
    assert quiet["detail"]["inputs"]["reference"]["strategy"] == "farField"


# ------------------------------------------------------------------------------------------ A10 / A11
def test_assumptions_sit_next_to_the_verdict(tmp_path):
    res = eq_run(_bundle(tmp_path, _quiet_specs())).to_dict()
    ev = next(e for e in res["evidence"] if e["id"] == "assumptions.radar")
    assert ev["level"] == "caveat" and "phase sign -1" in ev["claim"] and "100 effective looks" in ev["claim"]
    assert "look side unknown" in ev["claim"] and "0.2384" in ev["claim"]
    assert ev["support"]["look_side"] is None and ev["support"]["recorded"]          # the bundle's own assumption lines
    assert res["detail"]["inputs"]["assumptions"]["phase_sign"] == -1


def test_status_words_say_what_they_mean():
    sup = _status_meaning("SUPPORTED", None)
    assert "Two screens passed" in sup and "factor of 3" in sup and "0.25" in sup and "not a probability" in sup
    assert "not a probability" in eq_run.__globals__["_narrative"]("NO_SIGNAL", SimpleNamespace(
        detected_pixels=0, max_toward_m=0, max_away_m=0, valid_pixels=1, valid_fraction=1, null_screen={}), {}, None,
        EqParams())["note"]


# ------------------------------------------------------------------------------------------ A9
def test_steps_nobody_asked_for_are_skipped_and_say_why(tmp_path):
    only_m1 = build_scene_bundle(TILE, FakeRetriever(make_scene(TILE, n_epochs=4, pairs=((0, 1), (1, 2)), event=False)),
                                 as_of=AS_OF, active_codes={"M1"},
                                 ancillary=DirectoryAncillary(write_ancillary(tmp_path / "a1", TILE)))
    steps = {s.code: s for s in only_m1.provenance.steps}
    assert steps["P08"].status == "skipped" and steps["P11"].status == "skipped"
    assert "no active pipeline needs it" in steps["P11"].message
    assert steps["P10"].status != "skipped"                                # depth gating (min_epochs) still needs P10
    wants_m2 = build_scene_bundle(TILE, FakeRetriever(make_scene(TILE, n_epochs=4, pairs=((0, 1),), event=False)),
                                  as_of=AS_OF, active_codes={"M1", "M2"},
                                  ancillary=DirectoryAncillary(write_ancillary(tmp_path / "a2", TILE)))
    assert {s.code: s.status for s in wants_m2.provenance.steps}["P11"] != "skipped"


def test_registry_only_lets_working_pipelines_decide_what_is_fetched():
    from engine.common.config import CommonConfig, RegistryCfg
    from engine.registry import Registry
    reg = Registry()
    assert reg.cfg.registry.fetch_for_unmodelled is False
    codes = reg.active_codes()
    assert "M1" in codes and "M2" not in codes and "M3" not in codes       # earthquake needs M1; the M2 pipelines are NOT_MODELLED
    wide = Registry(cfg=dataclasses.replace(CommonConfig(), registry=RegistryCfg(fetch_for_unmodelled=True)))
    assert {"M1", "M2", "M3"} <= wide.active_codes()
