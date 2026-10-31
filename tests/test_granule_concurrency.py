"""Bounded granule concurrency + incremental completion + cancel. Offline: _granule_clip is a FAKE
(test fixture) that sleeps; no network, no retriever construction."""
from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine.retrieval.clients.nisar_client import NisarClient  # noqa: E402


def _client(clip_fn):
    c = NisarClient.__new__(NisarClient)           # skip __init__: it builds network/auth state
    c._prog_lock = threading.Lock()
    c.progress = {"cum_total": 0, "cum_done": 0, "recent": [], "active": {}, "product": "GCOV"}
    c._granule_clip = clip_fn
    return c


def _g(i):
    return SimpleNamespace(granule_id=f"G{i:02d}")


def test_never_more_than_n_granules_active_and_they_complete_incrementally(monkeypatch):
    monkeypatch.setenv("NISAR_GRANULE_CONCURRENCY", "3")
    state = {"active": 0, "peak": 0, "done_seen_midway": []}

    async def fake(g, bbox, layers):
        state["active"] += 1
        state["peak"] = max(state["peak"], state["active"])
        await asyncio.sleep(0.02)
        state["active"] -= 1
        return SimpleNamespace(granule=g, source="net")

    c = _client(fake)
    granules = [_g(i) for i in range(12)]

    async def run():
        task = asyncio.ensure_future(c._fetch_many(granules, (0, 0, 1, 1), None))
        await asyncio.sleep(0.05)                          # midway: some finished, not all started
        state["done_seen_midway"] = [c.progress["done"], c.progress["total"], len(c.progress["active"])]
        return await task

    res = asyncio.run(run())
    assert state["peak"] == 3
    done, total, active = state["done_seen_midway"]
    assert 0 < done < total == 12 and active <= 3          # "0/12 done · 12 in flight" can no longer happen
    assert [x.granule.granule_id for x in res.clips] == [g.granule_id for g in granules]


def test_cancel_stops_new_granules_and_keeps_finished_ones(monkeypatch):
    monkeypatch.setenv("NISAR_GRANULE_CONCURRENCY", "2")
    c = _client(None)
    started = []

    async def fake(g, bbox, layers):
        started.append(g.granule_id)
        await asyncio.sleep(0.02)
        if len(started) == 2:
            c.cancel_requested = True                      # cancel while the first two are in flight
        return SimpleNamespace(granule=g, source="net")

    c._granule_clip = fake
    res = asyncio.run(c._fetch_many([_g(i) for i in range(8)], (0, 0, 1, 1), None))
    assert len(res.clips) == 2 and len(started) == 2       # in-flight ones finished and are kept
    assert len(res.skipped) == 6 and all("cancelled" in v for v in res.skipped.values())
