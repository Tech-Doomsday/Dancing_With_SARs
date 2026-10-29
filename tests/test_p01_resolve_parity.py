"""P01.run (the chain step) and GET /api/granules (engine/api/jobs.py::_granule_search_sync) must give
the same answer for the same retriever: both go through p01.resolve(). Pins that they cannot drift
apart again (they used to be two copies of one loop, one of them calling P01's private helpers)."""
from __future__ import annotations

import pytest

from tests.common_fixtures import FakeRetriever, default_tile, make_scene
from tests.test_api import client, data_dir  # noqa: F401  (pytest fixtures)

LON, LAT = -118.5, 34.0
T0, T1 = "2026-06-01T00:00:00Z", "2026-10-01T00:00:00Z"


def _specs():
    return make_scene(default_tile(), n_epochs=4, pairs=((0, 1), (1, 2)), track=5, direction="A")


CASES = {
    "populated": lambda: FakeRetriever(_specs()),
    "empty": lambda: FakeRetriever([]),
    "degraded": lambda: FakeRetriever(_specs(), degraded=True),
    "search_error": lambda: FakeRetriever(_specs(), fail_search="GCOV"),
}


def _run_p01(make_retriever):
    from datetime import datetime, timezone

    from engine.common import p01_granule_resolution as p01
    from engine.common.config import load_common_config
    from engine.common.context import BuildContext, BuildRequest, parse_time
    from engine.common.grids import make_tile_grids
    from engine.retrieval.settings import load_settings
    from engine.tiling.mgrs import tile_at

    tile = tile_at(LON, LAT, size=int(load_settings().tiling.get("cell_size_m", 10_000)))
    cfg = load_common_config()
    as_of = datetime.now(timezone.utc).replace(microsecond=0)
    ctx = BuildContext(BuildRequest(tile, as_of, parse_time(T0), parse_time(T1)), cfg,
                       make_tile_grids(tile, cfg.grids), retriever=make_retriever())
    p01.run(ctx)
    return ctx


@pytest.mark.parametrize("case", sorted(CASES))
def test_endpoint_and_p01_run_agree(case, client, monkeypatch):  # noqa: F811
    import engine.api.jobs as api_jobs

    monkeypatch.setattr(api_jobs, "_default_retriever_factory", lambda: CASES[case])
    monkeypatch.setenv("EARTHDATA_TOKEN", "test-token-present")
    c, _ = client
    body = c.get("/api/granules", params={"lon": LON, "lat": LAT, "from": T0, "to": T1}).json()
    ctx = _run_p01(CASES[case])
    res = ctx.resolved

    flags = lambda fs: [(f["code"], f["severity"], f["step"], f["message"]) for f in fs]   # noqa: E731
    assert flags(body["flags"]) == flags(
        {"code": f.code, "severity": f.severity, "step": f.step, "message": f.message} for f in ctx.flags)
    assert body["search_errors"] == res.search_errors
    assert body["search_degraded"] == res.search_degraded
    assert body["dropped_older"] == res.dropped_older
    assert set(body["stacks"]) == set(res.gunw) | set(res.gcov) | set(res.goff)
    for key, s in body["stacks"].items():
        assert len(s["gunw"]) == len(res.gunw.get(key, []))
        assert len(s["gcov"]) == len(res.gcov.get(key, []))
