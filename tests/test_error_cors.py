"""An unexpected engine error must still reach the browser as readable JSON WITH CORS headers.
Without them the browser reports only 'Failed to fetch' and the real cause is hidden."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.common_fixtures import FakeRetriever  # noqa: E402
from tests.test_granules_bbox import (EAST, NORTH, SOUTH, WEST, _clean_bbox_rate_window, client, data_dir)  # noqa: F401,E402

ORIGIN = "http://localhost:3000"


class Exploding(FakeRetriever):
    def search(self, *a, **k):
        raise RuntimeError("asf exploded: connection pool is closed")


def test_unexpected_search_error_is_json_with_cors(client, monkeypatch):
    monkeypatch.setenv("EARTHDATA_TOKEN", "test-token-present")
    from engine.api import jobs as api_jobs
    boom = Exploding([])
    monkeypatch.setattr(api_jobs, "_default_retriever_factory", lambda: (lambda: boom))
    c, _ = client
    r = c.get(f"/api/granules?west={WEST}&south={SOUTH}&east={EAST}&north={NORTH}&level=GUNW,GCOV",
              headers={"Origin": ORIGIN})
    assert r.status_code >= 500
    assert r.headers.get("access-control-allow-origin") == ORIGIN, dict(r.headers)
    assert "asf exploded" in r.json()["detail"]["error"]


GCOV_ID = ("NISAR_L2_PR_GCOV_031_034_A_019_4005_SHNA_A_20260920T130900_20260920T130930_P05023_N_F_J_001")


def test_dual_pol_granule_with_list_polarization_groups_without_crashing(client, monkeypatch):
    """Real bug (user screenshot): provider metadata mainBandPolarization is a LIST for dual-pol
    granules; the grouping code put it in a set -> TypeError: unhashable type: 'list'."""
    monkeypatch.setenv("EARTHDATA_TOKEN", "test-token-present")
    from engine.api import jobs as api_jobs
    from tests.test_granules_bbox import _spec
    fake = FakeRetriever([
        _spec(GCOV_ID, "GCOV", "2026-09-20T13:09:00", track=34, direction="ASCENDING", frame=19,
              extra={"mainBandPolarization": ["HH", "HV"]}),
        _spec(GCOV_ID.replace("_4005_", "_4000_"), "GCOV", "2026-09-20T13:09:00", track=34,
              direction="ASCENDING", frame=19, extra={"mainBandPolarization": ["HH"]}),
    ])
    monkeypatch.setattr(api_jobs, "_default_retriever_factory", lambda: (lambda: fake))
    c, _ = client
    r = c.get(f"/api/granules?west={WEST}&south={SOUTH}&east={EAST}&north={NORTH}&level=GCOV")
    assert r.status_code == 200, r.text
    grp = r.json()["groups"][0]
    blob = repr(grp)
    assert "polarization" in blob           # the differing fact is reported, as before
