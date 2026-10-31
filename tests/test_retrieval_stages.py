"""UI-build-3: retrieval stages — bounded granule concurrency, cancel/resume, metadata lane, health.busy,
preprocessing from a stored retrieval with the network disabled. Offline: fakes over the fixture scene
(tests/common_fixtures.py). Everything here that looks like data is a TEST FIXTURE, not engine output."""
from __future__ import annotations

import asyncio
import socket
import threading
import time

import pytest

from tests.test_api import client, data_dir  # noqa: F401  (pytest fixtures)
from tests.common_fixtures import FakeRetriever, build_clip, default_tile, make_scene

LON, LAT = -118.5, 34.0
AS_OF = "2026-08-20T00:00:00Z"


@pytest.fixture()
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("NISAR_ENABLE_SCANS", "1")
    monkeypatch.setenv("EARTHDATA_TOKEN", "test-token-present")
    monkeypatch.setenv("NISAR_DATA_DIR", str(tmp_path))   # clips_dir == tmp_path/"clips"
    return tmp_path


class StoreFake(FakeRetriever):
    """FakeRetriever + the parts a real NisarClient adds: a real ClipStore, cancel flag, plan, states."""

    def __init__(self, specs, clips_dir, on_granule=None):
        super().__init__(specs)
        from engine.retrieval.clients.cache import ClipStore
        self.clips = ClipStore(clips_dir, None)
        self.cancel_event = threading.Event()
        self.network_fetches = 0
        self.states: dict = {}
        self.on_granule = on_granule
        self.closed = False

    def plan_tile(self, refs, bbox, layers):
        return [{"granule_id": r.granule_id, "product": r.product, "grid": "g", "layer": "L", "chunks": 2,
                 "bytes": 1000, "shape": [10, 10], "chunk_shape": [5, 5], "dtype": "float32",
                 "window": [0, 10, 0, 10], "fast": True} for r in refs]

    def fetch_tile(self, tile, t0=None, t1=None, product=None, layers=None, maturity=None, granules=None,
                   margin_m=None):
        from engine.retrieval.clients.types import FetchResult
        bbox = tuple(tile.bbox(300.0 if margin_m is None else margin_m))
        res = FetchResult([], {}, {})
        for g in granules or []:
            if self.cancel_event.is_set():
                res.skipped[g.granule_id] = "cancelled before it started"
                self.states[g.granule_id] = {"state": "cancelled"}
                continue
            key = self.clips.key(g.granule_id, bbox, [layers])
            hit = self.clips.get(key)
            if hit:
                from engine.retrieval.clients.nisar_client import NisarClient
                res.clips.append(NisarClient._from_zarr(g, bbox, hit[0], "clip-cache"))
                self.states[g.granule_id] = {"state": "stored", "source": "clip-cache", "bytes_done": 0}
                continue
            self.network_fetches += 1
            self.fetches.append({"gid": g.granule_id})
            clip = build_clip(tile, self.specs[g.granule_id], layers)
            if not clip.grids:
                res.skipped[g.granule_id] = "bbox does not overlap this granule's grids"
                continue
            self.clips.put(key, clip.grids, {"granule": g.granule_id})
            res.clips.append(clip)
            self.states[g.granule_id] = {"state": "stored", "source": "network", "bytes_done": 1000,
                                         "bytes_planned": 1000}
            if self.on_granule:
                self.on_granule(self, g)
        return res

    def progress_snapshot(self):
        return {"granules": {"states": dict(self.states)}, "net": {}, "cache": {}}

    def close(self):
        self.closed = True


def _wait(mgr, rid, status, t=20):
    end = time.time() + t
    while time.time() < end:
        j = mgr.get(rid)
        if j and j["status"] in status:
            return j
        time.sleep(0.05)
    raise AssertionError(f"retrieval {rid} did not reach {status}: {mgr.get(rid)}")


def _mgr(client):
    from engine.api.routes_retrievals import manager
    return manager()


def _factory(env, holder, **kw):
    specs = make_scene(default_tile(), n_epochs=4, pairs=((0, 1), (1, 2), (2, 3)), dlos=0.05)

    def f():
        r = StoreFake(specs, env / "clips", **kw)
        holder.append(r)
        return r
    return f


def _start(client, factory, mode="retrieve"):
    mgr = _mgr(client)
    return mgr, mgr.start(lon=LON, lat=LAT, mode=mode, as_of=AS_OF, lookback_days=120, retriever_factory=factory)


# ----------------------------------------------------------------------------- stages
def test_plan_mode_downloads_nothing_and_reports_bytes(client, env):
    c, _ = client
    held: list = []
    mgr, rid = _start(c, _factory(env, held), mode="plan")
    j = _wait(mgr, rid, ("planned", "error"))
    assert j["status"] == "planned", j.get("error")
    assert held[0].fetches == [] and held[0].network_fetches == 0          # no data chunk requested
    assert j["granules"] and all(g["bytes_planned"] == 1000 for g in j["granules"])   # one plan row x 1000 B per granule
    assert j["totals"]["bytes_planned"] == 1000 * len(j["granules"])
    assert held[0].closed


def test_retrieve_stores_then_resume_makes_zero_network_calls(client, env):
    c, _ = client
    held: list = []
    mgr, rid = _start(c, _factory(env, held))
    j = _wait(mgr, rid, ("done", "error", "failed"))
    assert j["status"] == "done", j.get("error")
    stored = [g for g in j["granules"] if g["state"] == "stored"]
    assert stored and held[0].network_fetches == len(stored)
    # resume: same retrieval request again -> every granule is a clip-cache hit, zero network
    held2: list = []
    mgr2, rid2 = _start(c, _factory(env, held2))
    j2 = _wait(mgr2, rid2, ("done", "error", "failed"))
    assert j2["status"] == "done"
    assert held2[0].network_fetches == 0
    assert {g["source"] for g in j2["granules"] if g["state"] == "stored"} == {"clip-cache"}
    # the manifest is on disk and readable without the in-memory job
    assert (mgr.root / rid / "manifest.json").exists()


def test_cancel_leaves_stored_granules_usable(client, env):
    c, _ = client
    held: list = []

    def cancel_after_first(fake, g):
        from engine.api.routes_retrievals import manager
        m = manager()
        if not m.get(m.busy()["run_id"])["cancel_requested"]:
            m.cancel(m.busy()["run_id"])

    mgr, rid = _start(c, _factory(env, held, on_granule=cancel_after_first))
    j = _wait(mgr, rid, ("cancelled", "done", "error"))
    assert j["status"] == "cancelled"
    states = [g["state"] for g in j["granules"]]
    assert "stored" in states and "cancelled" in states
    # what is stored is readable from the store, no network
    from engine.api.retrievals import ClipReader
    rd = ClipReader(j, env / "clips")
    assert rd.listing(), "stored clips must be listed"
    row = rd.listing()[0]
    arr, _ = rd.array(row["granule_id"], row["grid"], row["layer"])
    assert arr.size > 0


def test_preprocess_from_manifest_makes_zero_network_calls(client, env, monkeypatch):
    c, _ = client
    held: list = []
    mgr, rid = _start(c, _factory(env, held))
    j = _wait(mgr, rid, ("done",))
    from engine.api.retrievals import StoredRetriever
    from engine.common.chain import build_scene_bundle
    from engine.common.config import load_common_config

    def no_net(*a, **k):
        raise AssertionError("network used during preprocessing from a stored retrieval")
    monkeypatch.setattr(socket.socket, "connect", no_net)
    monkeypatch.setattr(socket, "create_connection", no_net)
    sr = StoredRetriever(j, env / "clips")
    bundle = build_scene_bundle(default_tile(), sr, as_of=AS_OF, cfg=load_common_config(), lookback_days=120)
    assert bundle.stacks, "a bundle must be built from the stored clips alone"
    assert any(i.source == "retrieval-store" for i in bundle.provenance.inputs)
    assert held[0].network_fetches >= 1          # the retrieval itself did "fetch"; preprocessing did not


def test_api_preprocess_route_and_inspect_routes(client, env, monkeypatch):
    c, _ = client
    monkeypatch.setenv("NISAR_BACKGROUND_TOKEN", "t")
    held: list = []
    import engine.api.jobs as api_jobs
    monkeypatch.setattr(api_jobs, "_default_retriever_factory", lambda: _factory(env, held))
    r = c.post("/api/retrievals", json={"lon": LON, "lat": LAT, "as_of": AS_OF, "lookback_days": 120})
    assert r.status_code == 202, r.text
    rid = r.json()["id"]
    _wait(_mgr(c), rid, ("done",))
    assert c.get(f"/api/retrievals/{rid}").json()["status"] == "done"
    H = {"Authorization": "Bearer t"}
    assert c.get(f"/api/retrievals/{rid}/clips").status_code == 401      # stored data is gated
    cl = c.get(f"/api/retrievals/{rid}/clips", headers=H).json()["clips"]
    assert cl
    q = {"granule": cl[0]["granule_id"], "grid": cl[0]["grid"], "layer": cl[0]["layer"]}
    img = c.get(f"/api/retrievals/{rid}/clips/image", params=q, headers=H)
    assert img.status_code == 200 and img.content[:4] == b"\x89PNG" and "decimation" in img.headers["x-img-meta"]
    st = c.get(f"/api/retrievals/{rid}/clips/stats", params=q, headers=H).json()
    assert st["valid"] > 0 and len(st["hist"]["counts"]) == 32
    pr = c.get(f"/api/retrievals/{rid}/clips/probe", params={**q, "row": 0, "col": 0}, headers=H)
    assert pr.status_code == 200 and "value" in pr.json()
    z = c.get(f"/api/retrievals/{rid}/download.zip", headers=H)
    assert z.status_code == 200 and z.content[:2] == b"PK"
    # preprocess from the store (no credentials needed, no network)
    monkeypatch.delenv("EARTHDATA_TOKEN")
    p = c.post(f"/api/retrievals/{rid}/preprocess")
    assert p.status_code == 202, p.text
    run_id = p.json()["run_id"]
    end = time.time() + 60
    while time.time() < end:
        st = c.get(f"/api/runs/{run_id}").json()
        if st["status"] in ("ok", "error", "done"):
            break
        time.sleep(0.2)
    assert st["status"] != "error", st.get("error")


# ----------------------------------------------------------------------------- lanes / health
def test_bbox_search_and_health_are_not_queued_behind_a_blocking_retrieval(client, env, monkeypatch):
    c, _ = client
    release, started = threading.Event(), threading.Event()
    held: list = []

    def block(fake, g):
        started.set()
        release.wait(30)                       # the "slow retrieval" holds the retrieval worker

    import engine.api.jobs as api_jobs
    monkeypatch.setattr(api_jobs, "_default_retriever_factory", lambda: _factory(env, held, on_granule=block))
    mgr, rid = _start(c, _factory(env, held, on_granule=block))
    try:
        assert started.wait(15), "retrieval never started fetching"
        t = time.monotonic()
        h = c.get("/api/health")
        dt_health = time.monotonic() - t
        assert h.status_code == 200 and dt_health < 0.2, dt_health
        busy = h.json()["busy"]
        assert busy and busy["kind"] == "retrieval" and busy["run_id"] == rid

        class Meta:
            def search(self, bbox, t0=None, t1=None, product=None, **k):
                return [], False

            def close(self):
                pass
        monkeypatch.setattr(api_jobs, "_default_retriever_factory", lambda: Meta)
        t = time.monotonic()
        r = c.get("/api/granules", params={"west": -118.6, "south": 33.9, "east": -118.4, "north": 34.1,
                                           "level": "GUNW"})
        assert r.status_code == 200, r.text
        assert time.monotonic() - t < 3.0
        # a new retrieval is refused honestly while one runs
        r2 = c.post("/api/retrievals", json={"lon": LON, "lat": LAT})
        assert r2.status_code == 409 and rid in r2.json()["detail"]["error"]
    finally:
        release.set()
    _wait(mgr, rid, ("done", "cancelled", "error"))
    assert c.get("/api/health").json()["busy"] is None


def test_slow_bbox_search_answers_202_then_polls(client, env, monkeypatch):
    c, _ = client
    import engine.api.jobs as api_jobs
    release = threading.Event()

    class Slow:
        def search(self, bbox, t0=None, t1=None, product=None, **k):
            release.wait(30)
            return [], False

        def close(self):
            pass
    monkeypatch.setattr(api_jobs, "_default_retriever_factory", lambda: Slow)
    monkeypatch.setattr(api_jobs, "META_WAIT_S", 0.3)
    t = time.monotonic()
    r = c.get("/api/granules", params={"west": -118.6, "south": 33.9, "east": -118.4, "north": 34.1, "level": "GUNW"})
    assert r.status_code == 202 and time.monotonic() - t < 3
    job = r.json()["job_id"]
    assert c.get(f"/api/granules/jobs/{job}").json()["pending"] is True
    release.set()
    end = time.time() + 10
    while time.time() < end:
        p = c.get(f"/api/granules/jobs/{job}").json()
        if not p["pending"]:
            break
        time.sleep(0.1)
    assert p["pending"] is False and p["ok"] is True


# ----------------------------------------------------------------------------- the real client's scheduling
def _real_client(tmp_path, monkeypatch):
    monkeypatch.setenv("NISAR_DATA_DIR", str(tmp_path / "d"))
    from engine.retrieval.clients.nisar_client import NisarClient
    from engine.retrieval.settings import load_settings
    s = load_settings()
    s.net["granule_concurrency"] = 3
    return NisarClient(s, auth=object())


def _refs(n):
    from engine.retrieval.clients.types import GranuleRef
    return [GranuleRef(f"g{i}", f"https://example.invalid/g{i}.h5", "GCOV") for i in range(n)]


def test_granule_concurrency_is_bounded_and_granules_finish_incrementally(tmp_path, monkeypatch):
    cl = _real_client(tmp_path, monkeypatch)
    try:
        active = peak = 0
        finished_while_others_active: list = []

        async def fake_clip(g, bbox, layers):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.05)
            active -= 1
            finished_while_others_active.append(active)
            from engine.retrieval.clients.types import GranuleClip
            return GranuleClip(g, tuple(bbox), {}, "network")
        cl._granule_clip = fake_clip
        res = asyncio.run(cl._fetch_many(_refs(9), (0, 0, 1, 1), None))
        assert len(res.clips) == 9
        assert peak <= 3, f"more than granule_concurrency granules active at once: {peak}"
        assert peak == 3
        assert any(a > 0 for a in finished_while_others_active), "granules must complete while others still run"
        assert all(s["state"] == "stored" for s in cl.granule_states.values())
    finally:
        cl.close()


def test_real_client_cancel_stops_queued_granules(tmp_path, monkeypatch):
    cl = _real_client(tmp_path, monkeypatch)
    try:
        done: list = []

        async def fake_clip(g, bbox, layers):
            await asyncio.sleep(0.05)
            done.append(g.granule_id)
            if len(done) == 2:
                cl.cancel_event.set()
            from engine.retrieval.clients.types import GranuleClip
            return GranuleClip(g, tuple(bbox), {}, "network")
        cl._granule_clip = fake_clip
        res = asyncio.run(cl._fetch_many(_refs(9), (0, 0, 1, 1), None))
        assert 2 <= len(res.clips) < 9
        assert res.skipped and all("cancel" in v for v in res.skipped.values())
        assert {s["state"] for s in cl.granule_states.values()} <= {"stored", "cancelled"}
    finally:
        cl.close()
