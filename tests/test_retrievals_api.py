"""Stage 3 routes. Offline: FakeClient is a labelled TEST FIXTURE that mimics the retrieval client's
per-granule hook; no network, no real data."""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.test_granules_bbox import _clean_bbox_rate_window, client, data_dir  # noqa: F401,E402


class FakeClient:
    def __init__(self, n=6, gate=None, fail=()):
        self.refs = [SimpleNamespace(granule_id=f"G{i}", product="GCOV", start=f"2026-09-{10 + i:02d}T00:00:00",
                                     track=5, direction="ASCENDING", frame=1) for i in range(n)]
        self.gate, self.fail = gate, set(fail)
        self.cancel_requested = False
        self.granule_hook = None
        self.last_search_incomplete = False
        self.fetch_calls = 0
        self.clips = SimpleNamespace(key=lambda gid, bbox, layers: f"key-{gid}")

    def search(self, bbox, t0, t1, product, **k):
        return list(self.refs), False

    def fetch(self, bbox, t0, t1, product, layers, granules):
        self.fetch_calls += 1
        errors, clips = {}, []
        for g in granules:
            if self.cancel_requested:
                self.granule_hook(g.granule_id, "skipped", 0.0, "cancelled before it started")
                continue
            self.granule_hook(g.granule_id, "fetching", 0.0, "")
            if self.gate is not None:
                self.gate.wait(10)
            if g.granule_id in self.fail:
                errors[g.granule_id] = "OSError: boom"
                self.granule_hook(g.granule_id, "failed", 0.1, "OSError: boom")
            else:
                self.granule_hook(g.granule_id, "net", 0.1, "")
                clips.append(g)
        return SimpleNamespace(clips=clips, errors=errors, skipped={}, degraded=False)

    def close(self):
        pass


@pytest.fixture()
def ctl(client, monkeypatch):
    c, d = client
    monkeypatch.setenv("EARTHDATA_TOKEN", "test-token-present")
    monkeypatch.setenv("NISAR_ENABLE_SCANS", "1")
    from engine.api import jobs as api_jobs
    from engine.api import routes_retrievals as rr
    rr.get_retrievals.cache_clear()
    box = {}
    monkeypatch.setattr(api_jobs, "_default_retriever_factory", lambda: (lambda: box["fake"]))
    yield c, box, d
    rr.get_retrievals.cache_clear()


def _wait(c, rid, pred, t=10):
    end = time.time() + t
    while time.time() < end:
        j = c.get(f"/api/retrievals/{rid}").json()
        if pred(j):
            return j
        time.sleep(0.05)
    raise AssertionError(f"timeout; last={j}")


BODY = {"lon": -118.5, "lat": 34.0, "products": ["GCOV"], "lookback_days": 60}


def test_retrieval_runs_to_done_and_writes_a_manifest(ctl):
    c, box, d = ctl
    box["fake"] = FakeClient(n=4, fail={"G2"})
    r = c.post("/api/retrievals", json=BODY)
    assert r.status_code == 202, r.text
    rid = r.json()["retrieval_id"]
    j = _wait(c, rid, lambda j: j["status"] == "done")
    assert j["counts"]["stored"] == 3 and j["counts"]["failed"] == 1 and j["total"] == 4
    bad = next(g for g in j["granules"] if g["state"] == "failed")
    assert bad["error"] == "OSError: boom"
    assert all(g["clip_key"] for g in j["granules"])
    assert all(g["bytes_done"] is None for g in j["granules"])          # never estimated
    assert (Path(d) / "retrievals" / rid / "manifest.json").is_file()


def test_caps_keep_the_newest_and_say_so(ctl):
    c, box, _ = ctl
    box["fake"] = FakeClient(n=6)
    rid = c.post("/api/retrievals", json={**BODY, "max_granules": 2}).json()["retrieval_id"]
    j = _wait(c, rid, lambda j: j["status"] == "done")
    assert [g["granule_id"] for g in j["granules"]] == ["G5", "G4"]
    assert any("newest 2" in f for f in j["flags"])


def test_cancel_keeps_finished_and_health_shows_the_retrieval(ctl):
    c, box, _ = ctl
    gate = threading.Event()
    box["fake"] = FakeClient(n=5, gate=gate)
    rid = c.post("/api/retrievals", json=BODY).json()["retrieval_id"]
    _wait(c, rid, lambda j: j["counts"]["fetching"] == 1)
    busy = c.get("/api/health").json()["busy"]
    assert busy["kind"] == "retrieval" and busy["run_id"] == rid
    assert c.post("/api/retrievals", json=BODY).status_code == 409        # one at a time
    assert c.post(f"/api/retrievals/{rid}/cancel").status_code == 200
    gate.set()
    j = _wait(c, rid, lambda j: j["status"] == "cancelled")
    assert j["counts"]["stored"] == 1 and j["counts"]["cancelled"] == 4   # the in-flight one finished + stayed
    assert c.post(f"/api/retrievals/{rid}/cancel").status_code == 409


def test_second_retrieval_gets_a_new_id_and_unknown_ids_404(ctl):
    c, box, _ = ctl
    box["fake"] = FakeClient(n=2)
    a = c.post("/api/retrievals", json=BODY).json()["retrieval_id"]
    _wait(c, a, lambda j: j["status"] == "done")
    b = c.post("/api/retrievals", json=BODY).json()["retrieval_id"]
    assert a != b
    assert c.get("/api/retrievals/ret-nope").status_code == 404


def test_bad_input_is_a_400_with_the_reason(ctl):
    c, box, _ = ctl
    box["fake"] = FakeClient()
    assert c.post("/api/retrievals", json={**BODY, "products": ["XYZ"]}).status_code == 400
    assert c.post("/api/retrievals", json={"products": ["GCOV"]}).status_code == 400
    assert c.post("/api/retrievals", json={**BODY, "lookback_days": 9999}).status_code == 400
