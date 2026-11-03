"""B3 / B5 end to end through the real RetrievalManager (fake network only): a cap that cuts granules is recorded as numbers
in the manifest, and a resumed retrieval says its granules came from the local cache at 0 bytes."""
from __future__ import annotations

from tests.test_api import client, data_dir  # noqa: F401  (fixtures)
from tests.test_retrieval_stages import AS_OF, LAT, LON, _factory, _mgr, _wait, _start, env  # noqa: F401


def test_a_cap_that_cuts_granules_is_recorded_with_the_numbers(client, env):
    c, _ = client
    held: list = []
    mgr = _mgr(c)
    rid = mgr.start(lon=LON, lat=LAT, mode="plan", as_of=AS_OF, lookback_days=120, max_gunw_pairs=2,
                    retriever_factory=_factory(env, held))
    j = _wait(mgr, rid, ("planned", "error"))
    assert j["status"] == "planned", j.get("error")
    cuts = [x for x in j["cuts"] if x["code"] == "pairs_truncated"]
    assert cuts and cuts[0]["product"] == "GUNW" and (cuts[0]["kept"], cuts[0]["found"], cuts[0]["cap"]) == (2, 3, 2)
    assert any("older pair(s) not used" in f["message"] for f in j["flags"])
    assert j["caps"]["max_gunw_pairs"] == 2                                  # the effective cap, not the word "default"


def test_without_a_cap_nothing_is_reported_cut(client, env):
    c, _ = client
    held: list = []
    mgr, rid = _start(c, _factory(env, held), mode="plan")
    assert _wait(mgr, rid, ("planned", "error"))["cuts"] == []


def test_a_resumed_retrieval_says_everything_came_from_the_cache(client, env):
    c, _ = client
    held: list = []
    mgr, rid = _start(c, _factory(env, held))
    first = _wait(mgr, rid, ("done", "error", "failed"))
    assert first["totals"]["stored_from_network"] == first["totals"]["stored"] > 0
    held2: list = []
    mgr2, rid2 = _start(c, _factory(env, held2))
    j = _wait(mgr2, rid2, ("done", "error", "failed"))
    t = j["totals"]
    assert t["bytes_done"] == 0 and t["stored_from_cache"] == t["stored"] > 0 and t["stored_from_network"] == 0
