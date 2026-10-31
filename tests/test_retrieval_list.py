"""RetrievalManager.list(): newest first by created_at (ids are random, so name order is arbitrary), an
optional limit, and the request / window / bbox a 'what do I have' view needs. Needs no network, no
fastapi, no zarr: the manager only reads manifest.json files."""
from __future__ import annotations

import random
from types import SimpleNamespace

from engine.api.retrievals import RetrievalManager


def _manager(tmp_path, n: int) -> RetrievalManager:
    m = RetrievalManager(SimpleNamespace(data_dir=tmp_path))
    rng = random.Random(3)
    for t in range(n):
        m._save({"id": f"ret-{rng.getrandbits(48):012x}", "status": "done", "mode": "retrieve", "tile": f"T{t}",
                 "created_at": 1000 + t, "stage": "finished", "totals": {"stored": 1},
                 "request": {"lon": 90.4, "lat": 23.7}, "window": {"lookback_days": 60}, "bbox": [0, 0, 1, 1]})
    return m


def test_default_is_the_30_newest_not_an_arbitrary_30(tmp_path):
    got = _manager(tmp_path, 40).list()
    assert len(got) == 30
    assert [r["tile"] for r in got] == [f"T{t}" for t in range(39, 9, -1)]          # newest first


def test_limit_none_returns_everything_and_limit_n_truncates(tmp_path):
    m = _manager(tmp_path, 40)
    assert len(m.list(None)) == 40
    assert [r["tile"] for r in m.list(3)] == ["T39", "T38", "T37"]
    assert m.list(0) == []


def test_rows_carry_what_a_datasets_view_needs(tmp_path):
    row = _manager(tmp_path, 1).list()[0]
    assert row["request"] == {"lon": 90.4, "lat": 23.7}
    assert row["window"] == {"lookback_days": 60} and row["bbox"] == [0, 0, 1, 1]
    assert {"id", "status", "mode", "tile", "created_at", "stage", "totals", "seconds", "error"} <= set(row)


def test_empty_store_and_foreign_directories_are_ignored(tmp_path):
    m = RetrievalManager(SimpleNamespace(data_dir=tmp_path))
    assert m.list() == []
    (m.root / "not-a-retrieval").mkdir(parents=True)
    (m.root / "ret-000000000000").mkdir()                       # right shape of name, no manifest
    assert m.list() == []
