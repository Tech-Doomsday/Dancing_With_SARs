"""§5.5 granule-availability endpoint tests (D16) — offline, injected FakeRetriever with known
fixture granules. Covers: multiple stacks, a flipped pair, an unresolved pair, a degraded search,
a tile with zero granules, and a GUNW pair's dt_days matching a hand-computed value."""
from __future__ import annotations

from datetime import timedelta

import pytest

from tests.common_fixtures import (
    GranuleSpec, dt, gunw_id, gcov_id, ref_for, default_tile, make_scene,
)
from tests.test_api import client, data_dir  # noqa: F401  (pytest fixtures)

LON, LAT = -118.5, 34.0


def multi_stack_specs() -> list[GranuleSpec]:
    """Stack A005 (pairs + epochs) + stack D013 (descending) + a flipped pair + an unresolved pair."""
    tile = default_tile()
    a = make_scene(tile, n_epochs=4, pairs=((0, 1), (1, 2)), track=5, direction="A")
    d = make_scene(tile, n_epochs=3, pairs=((0, 1),), track=13, direction="D", start="2026-07-03")
    # flipped pair: the id carries the times in reversed order -> P01 marks flipped and corrects
    days = [dt("2026-07-01") + timedelta(days=12 * i) for i in range(4)]
    flipped_id = gunw_id(days[2], days[0])                      # "ref" slot = later, "sec" = earlier
    flipped = GranuleSpec(ref_for(flipped_id, "GUNW", days[0], 5, "ASCENDING"),
                          "GUNW", ref_time=days[2], sec_time=days[0])
    # unresolved pair: a GUNW id with no parseable timestamps -> kept, resolved=False
    unresolved_id = "NISAR_L2_PR_GUNW_001_005_A_219_4005_SHNA_notimes_X05010_N_F_J_001"
    unresolved = GranuleSpec(ref_for(unresolved_id, "GUNW", days[0], 5, "ASCENDING"),
                             "GUNW", ref_time=days[0])
    return a + d + [flipped, unresolved]


@pytest.fixture()
def granule_env(monkeypatch):
    import engine.api.jobs as api_jobs
    specs = multi_stack_specs()

    def factory():
        from tests.common_fixtures import FakeRetriever
        return FakeRetriever(specs)

    monkeypatch.setattr(api_jobs, "_default_retriever_factory", lambda: factory)
    monkeypatch.setenv("EARTHDATA_TOKEN", "test-token-present")
    return specs


def test_granules_multiple_stacks_pairs_and_epochs(client, granule_env):
    c, _ = client
    r = c.get("/api/granules", params={"lon": LON, "lat": LAT})
    assert r.status_code == 200 and r.json()["ok"] is True
    body = r.json()
    assert body["tile"]["id"] == "11SLT66"
    stacks = body["stacks"]
    assert set(stacks) >= {"A005", "D013"}
    a = stacks["A005"]
    assert a["direction"] == "A" and a["track"] == 5
    # GUNW pair groups with REAL ref/sec times; dt_days matches the hand-computed 24 d
    assert a["gunw"], "the A005 stack must carry pair groups"
    dts = {(p["ref_time"], p["sec_time"]): p["dt_days"] for p in a["gunw"] if p["resolved"]}
    assert 24.0 in {round(v, 6) for v in dts.values()}
    # the flipped pair is corrected (ref < sec) and marked
    flipped = [p for p in a["gunw"] if p["flipped"]]
    assert flipped and all(p["resolved"] and p["ref_time"] < p["sec_time"] for p in flipped)
    assert round(flipped[0]["dt_days"], 6) == 24.0
    # the unresolved pair is kept but never assigned dates
    unresolved = [p for p in a["gunw"] if not p["resolved"]]
    assert unresolved and unresolved[0]["ref_time"] is None and unresolved[0]["dt_days"] is None
    # granule ids + footprint + frame come from GranuleRef (ASF's outer ring when present)
    some = next(p for p in a["gunw"] if p["resolved"])
    assert some["granule_ids"] and some["frame"] == 219
    # GCOV epochs (near-instantaneous) exist for A005; the descending stack has its own
    assert a["gcov"] and stacks["D013"]["gcov"]
    assert stacks["D013"]["direction"] == "D"
    # window echoes the request/default (common.yaml lookback_days = 120)
    assert body["window"]["t0"] < body["window"]["t1"]


def test_granules_from_to_window_reaches_the_search(client, granule_env):
    c, _ = client
    r = c.get("/api/granules", params={"lon": LON, "lat": LAT, "product": "GUNW",
                                       **{"from": "2026-07-05T00:00:00Z"},
                                       "to": "2026-08-10T00:00:00Z"})
    assert r.status_code == 200
    assert r.json()["window"] == {"t0": "2026-07-05T00:00:00Z", "t1": "2026-08-10T00:00:00Z"}
    # the search itself received the window (FakeRetriever records its calls)
    searches = c.get("/api/granules", params={"lon": LON, "lat": LAT, "product": "GCOV"}).json()
    assert searches["ok"] is True


def test_granules_degraded_search_surfaces_p01_flag_text(client, monkeypatch):
    import engine.api.jobs as api_jobs

    def factory():
        from tests.common_fixtures import FakeRetriever, make_scene, default_tile
        return FakeRetriever(make_scene(default_tile(), n_epochs=3, pairs=((0, 1),)), degraded=True)

    monkeypatch.setattr(api_jobs, "_default_retriever_factory", lambda: factory)
    monkeypatch.setenv("EARTHDATA_TOKEN", "test-token-present")
    c, _ = client
    r = c.get("/api/granules", params={"lon": LON, "lat": LAT})
    assert r.status_code == 200
    body = r.json()
    assert body["search_degraded"] is True
    assert any(f["code"] == "search_degraded" and "stale cache (ASF unreachable)" in f["message"]
               for f in body["flags"])


def test_granules_zero_granules_is_a_calm_answer(client, monkeypatch):
    import engine.api.jobs as api_jobs

    def factory():
        from tests.common_fixtures import FakeRetriever
        return FakeRetriever([])

    monkeypatch.setattr(api_jobs, "_default_retriever_factory", lambda: factory)
    monkeypatch.setenv("EARTHDATA_TOKEN", "test-token-present")
    c, _ = client
    r = c.get("/api/granules", params={"lon": LON, "lat": LAT})
    assert r.status_code == 200
    body = r.json()
    assert body["stacks"] == {}
    assert any(f["code"] == "no_granules" for f in body["flags"])


def test_granules_search_error_is_reported_not_raised(client, monkeypatch):
    import engine.api.jobs as api_jobs

    def factory():
        from tests.common_fixtures import FakeRetriever, make_scene, default_tile
        return FakeRetriever(make_scene(default_tile(), n_epochs=2, pairs=((0, 1),)),
                             fail_search="GCOV")

    monkeypatch.setattr(api_jobs, "_default_retriever_factory", lambda: factory)
    monkeypatch.setenv("EARTHDATA_TOKEN", "test-token-present")
    c, _ = client
    r = c.get("/api/granules", params={"lon": LON, "lat": LAT})
    assert r.status_code == 200
    body = r.json()
    assert "ConnectionError: ASF unreachable" in body["search_errors"]["GCOV"]
    assert any(f["code"] == "search_error" for f in body["flags"])


def test_granules_need_credentials(client, monkeypatch):
    c, _ = client
    for var in ("EARTHDATA_TOKEN", "EARTHDATA_USERNAME", "EARTHDATA_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    r = c.get("/api/granules", params={"lon": LON, "lat": LAT})
    assert r.status_code == 503 and "EARTHDATA_TOKEN" in r.json()["detail"]["error"]


def test_granules_reject_bad_product_and_coords(client, monkeypatch):
    monkeypatch.setenv("EARTHDATA_TOKEN", "test-token-present")
    c, _ = client
    r = c.get("/api/granules", params={"lon": LON, "lat": LAT, "product": "SLC"})
    assert r.status_code == 400
    r = c.get("/api/granules", params={"lon": 200, "lat": LAT})
    assert r.status_code == 400
