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
