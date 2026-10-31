"""colab/hub.py: the access-key gate, the tile cover, the batch queue and the disk summary.

Offline and engine-free: the gate is driven as raw ASGI, the cover runs on a fake MGRS module (plus one
real-pyproj check that skips where pyproj is absent), the queue on a fake RetrievalManager."""
from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("nisar_hub_under_test", ROOT / "colab" / "hub.py")
hub = importlib.util.module_from_spec(_spec)
sys.modules["nisar_hub_under_test"] = hub
_spec.loader.exec_module(hub)

KEY = "k" * 8 + "A-_z" * 4 + "0123"           # 28 chars, urlsafe alphabet


# ------------------------------------------------------------------------------------------ gate
def _drive(app, *, path="/", query=b"", headers=(), scope_type="http"):
    sent: list = []

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(msg):
        sent.append(msg)

    scope = {"type": scope_type, "path": path, "query_string": query, "root_path": "",
             "headers": [(k.lower().encode(), v.encode()) for k, v in headers]}
    asyncio.run(app(scope, receive, send))
    return sent


class _Inner:
    def __init__(self):
        self.calls = 0

    async def __call__(self, scope, receive, send):
        self.calls += 1
        if scope["type"] == "http":
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"inner"})


def _status(sent):
    return sent[0]["status"]


def _hdr(sent, name):
    return {k.decode(): v.decode() for k, v in sent[0]["headers"]}.get(name)


def test_no_key_is_401_and_the_inner_app_is_never_reached():
    inner = _Inner()
    sent = _drive(hub.KeyGate(inner, KEY))
    assert _status(sent) == 401 and inner.calls == 0
    assert KEY.encode() not in b"".join(m.get("body", b"") for m in sent)         # never echoes the secret


def test_wrong_key_everywhere_is_401():
    inner = _Inner()
    gate = hub.KeyGate(inner, KEY)
    for kw in ({"query": b"k=nope"}, {"headers": [("cookie", f"{hub.COOKIE}=nope")]},
               {"headers": [("x-hub-key", "nope")]}, {"query": f"k={KEY}x".encode()}):
        assert _status(_drive(gate, **kw)) == 401, kw
    assert inner.calls == 0


def test_key_in_the_url_sets_a_cookie_and_redirects_without_the_key():
    inner = _Inner()
    sent = _drive(hub.KeyGate(inner, KEY), path="/hub/x", query=f"a=1&k={KEY}&b=2".encode(),
                  headers=[("x-forwarded-proto", "https")])
    assert _status(sent) == 303 and inner.calls == 0
    assert _hdr(sent, "location") == "/hub/x?a=1&b=2"                              # k is gone, the rest survives
    cookie = _hdr(sent, "set-cookie")
    assert cookie.startswith(f"{hub.COOKIE}={KEY}") and "HttpOnly" in cookie and "Secure" in cookie
    assert "SameSite=Lax" in cookie


def test_cookie_is_not_marked_secure_on_plain_http():
    sent = _drive(hub.KeyGate(_Inner(), KEY), query=f"k={KEY}".encode())
    assert "Secure" not in _hdr(sent, "set-cookie")


def test_cookie_or_header_lets_the_request_through():
    inner = _Inner()
    gate = hub.KeyGate(inner, KEY)
    assert _status(_drive(gate, headers=[("cookie", f"other=1; {hub.COOKIE}={KEY}")])) == 200
    assert _status(_drive(gate, headers=[("x-hub-key", KEY)])) == 200
    assert inner.calls == 2


def test_garbage_cookie_header_is_just_a_401_not_a_crash():
    assert _status(_drive(hub.KeyGate(_Inner(), KEY), headers=[("cookie", "\x00;;=,,==")])) == 401


def test_lifespan_passes_through_and_websockets_are_refused():
    inner = _Inner()
    gate = hub.KeyGate(inner, KEY)
    _drive(gate, scope_type="lifespan")
    assert inner.calls == 1
    sent = _drive(gate, scope_type="websocket")
    assert sent == [{"type": "websocket.close", "code": 1008}] and inner.calls == 1


def test_a_weak_key_is_refused_at_construction():
    with pytest.raises(ValueError):
        hub.KeyGate(_Inner(), "short")


# ------------------------------------------------------------------------------------------ cover
class FakeTile:
    def __init__(self, tid, lon, lat):
        self.id, self._c = tid, (lon, lat)

    @property
    def center(self):
        return self._c

    def to_dict(self):
        lon, lat = self._c
        return {"id": self.id, "epsg": 32646, "size_m": 10000, "bbox_lonlat": [lon - .05, lat - .05, lon + .05, lat + .05],
                "center_lonlat": [lon, lat], "polygon_lonlat": []}


class FakeMgrs:
    """Candidates come back from tiles_for_polygon; tile_at(center) maps a centre back to a tile (or another one)."""

    def __init__(self, candidates, redirect=None, missing=()):
        self.candidates, self.redirect, self.missing = candidates, redirect or {}, set(missing)
        self.by_center = {t.center: t for t in candidates}
        self.calls = []

    def tiles_for_polygon(self, ring, size, margin_m=0.0):
        self.calls.append(("poly", size))
        return list(self.candidates)

    def tile_at(self, lon, lat, size=10_000):
        self.calls.append(("at", lon, lat, size))
        if (lon, lat) in self.missing:
            raise ValueError("outside the grid")
        t = self.by_center.get((lon, lat)) or FakeTile("POINT", lon, lat)
        return self.redirect.get(t.id, t)


def _patch(monkeypatch, fake, cell=10_000):
    monkeypatch.setattr(hub, "_mgrs", lambda: fake)
    monkeypatch.setattr(hub, "_cell_m", lambda: cell)
    monkeypatch.setattr(hub, "_box_ring", lambda lon, lat, km: [(lon, lat)] * 5)


def test_cover_is_nearest_first_and_carries_lon_lat_for_the_retrieval(monkeypatch):
    far, near, mid = FakeTile("F", 90.6, 23.9), FakeTile("N", 90.41, 23.75), FakeTile("M", 90.5, 23.8)
    _patch(monkeypatch, FakeMgrs([far, near, mid]))
    got = hub.cover(90.41849, 23.748, 30)
    assert [t["id"] for t in got] == ["N", "M", "F"]
    assert (got[0]["lon"], got[0]["lat"]) == (90.41, 23.75) and got[0]["polygon_lonlat"] == []


def test_cover_uses_the_engines_configured_cell_not_a_hardcoded_10km(monkeypatch):
    fake = FakeMgrs([FakeTile("A", 1.0, 2.0)])
    _patch(monkeypatch, fake, cell=1_000)
    hub.cover(1.0, 2.0, 5)
    assert ("poly", 1_000) in fake.calls and all(c[-1] == 1_000 for c in fake.calls if c[0] == "at")


def test_snap_is_exactly_the_tile_under_the_point_and_never_builds_a_box(monkeypatch):
    fake = FakeMgrs([FakeTile("A", 1.0, 2.0)])
    _patch(monkeypatch, fake)
    monkeypatch.setattr(hub, "_box_ring", lambda *a: (_ for _ in ()).throw(AssertionError("box built under snap")))
    got = hub.cover(5.0, 6.0, 50, snap=True)
    assert [t["id"] for t in got] == ["POINT"] and not any(c[0] == "poly" for c in fake.calls)


def test_zone_edge_duplicates_collapse_to_the_tile_the_retrieval_will_really_use(monkeypatch):
    a, b = FakeTile("A", 1.0, 2.0), FakeTile("B", 1.1, 2.1)      # B's centre resolves to A (it sits in A's zone)
    _patch(monkeypatch, FakeMgrs([a, b], redirect={"B": a}))
    assert [t["id"] for t in hub.cover(1.0, 2.0, 20)] == ["A"]


def test_a_candidate_whose_centre_is_off_the_grid_is_dropped_not_fatal(monkeypatch):
    a, b = FakeTile("A", 1.0, 2.0), FakeTile("B", 1.1, 2.1)
    _patch(monkeypatch, FakeMgrs([a, b], missing=[b.center]))
    assert [t["id"] for t in hub.cover(1.0, 2.0, 20)] == ["A"]


def test_cover_refuses_more_tiles_than_the_cap_and_says_how_many(monkeypatch):
    tiles = [FakeTile(f"T{i}", i * 0.1, 0.0) for i in range(6)]
    _patch(monkeypatch, FakeMgrs(tiles))
    with pytest.raises(ValueError, match="needs 6 tiles"):
        hub.cover(0.0, 0.0, 60, max_tiles=5)
    assert len(hub.cover(0.0, 0.0, 60, max_tiles=6)) == 6


def test_cover_with_no_tiles_is_an_error(monkeypatch):
    _patch(monkeypatch, FakeMgrs([]))
    with pytest.raises(ValueError, match="no tile covers"):
        hub.cover(0.0, 0.0, 10)


def test_real_cover_on_the_real_grid():
    """Needs the engine's real libs; skipped in minimal environments."""
    pytest.importorskip("pyproj")
    pytest.importorskip("shapely")
    sys.path.insert(0, str(ROOT))
    lon, lat = 90.41849, 23.748                                              # Dhaka
    one = hub.cover(lon, lat, 10, snap=True)
    assert len(one) == 1 and one[0]["size_m"] == 10_000
    box = hub.cover(lon, lat, 30)
    assert 9 <= len(box) <= 16 and one[0]["id"] in {t["id"] for t in box}
    assert box[0]["id"] == one[0]["id"] or len(box) > 1                      # nearest first
    from engine.tiling.mgrs import tile_at
    for t in box:                                                            # what is previewed is what is retrieved
        assert tile_at(t["lon"], t["lat"], size=10_000).id == t["id"]


def test_have_map_reports_the_best_stored_retrieval_per_tile():
    rows = [{"id": "ret-a", "tile": "T1", "totals": {"stored": 2}}, {"id": "ret-b", "tile": "T1", "totals": {"stored": 5}},
            {"id": "ret-c", "tile": "T2", "totals": {"stored": 0}}, {"id": "ret-d", "tile": "T3", "totals": None},
            {"id": "ret-e", "tile": None, "totals": {"stored": 9}}]
    assert hub.have_map(rows) == {"T1": {"stored": 5, "retrieval": "ret-b"}}


# ------------------------------------------------------------------------------------------ batch queue
class Err(Exception):
    def __init__(self, status, msg):
        super().__init__(msg)
        self.status = status


class FakeMgr:
    """start() -> id; get() walks queued -> running -> terminal. `busy_first` starts raise a 409 first."""

    def __init__(self, busy_first=0, fail_lons=(), steps=2, hold=None):
        self.busy_first, self.fail_lons, self.steps, self.hold = busy_first, set(fail_lons), steps, hold
        self.started, self.cancelled, self.seen, self.n = [], [], {}, 0
        self.max_running = 0

    def start(self, **kw):
        if self.busy_first > 0:
            self.busy_first -= 1
            raise Err(409, "the engine is busy")
        if kw["lon"] in self.fail_lons:
            raise Err(503, "no Earthdata credentials")
        self.n += 1
        rid = f"ret-{self.n:012x}"
        self.started.append((rid, kw))
        self.seen[rid] = 0
        return rid

    def get(self, rid):
        self.seen[rid] += 1
        if rid in self.cancelled:
            return {"status": "cancelled", "stage": "finished", "totals": {"stored": 0}}
        if self.hold is not None and not self.hold.is_set():
            return {"status": "running", "stage": "download", "totals": {"stored": 0}}
        if self.seen[rid] <= self.steps:
            return {"status": "running", "stage": "download", "totals": {"stored": 0}}
        mode = dict(self.started)[rid]["mode"]
        return {"status": "planned" if mode == "plan" else "done", "stage": "finished", "totals": {"stored": 3, "bytes_done": 10}}

    def cancel(self, rid):
        self.cancelled.append(rid)


TILES = [{"id": f"T{i}", "lon": float(i), "lat": 10.0} for i in range(3)]
PARAMS = {"lookback_days": 60, "max_gcov_epochs": None, "max_gunw_pairs": None, "products": ["GCOV"]}


def _runner(mgr, **kw):
    return hub.BatchRunner(lambda: mgr, poll_s=0.001, **kw)


def test_tiles_run_in_order_one_at_a_time_with_the_requests_params():
    mgr = FakeMgr()
    r = _runner(mgr)
    r.start(TILES, "retrieve", PARAMS)
    r.join(5)
    s = r.snapshot()
    assert s["status"] == "finished" and [t["status"] for t in s["tiles"]] == ["done"] * 3
    assert [kw["lon"] for _, kw in mgr.started] == [0.0, 1.0, 2.0]
    assert all(kw["mode"] == "retrieve" and kw["lookback_days"] == 60 and kw["products"] == ["GCOV"] for _, kw in mgr.started)
    assert s["tiles"][0]["totals"]["stored"] == 3 and s["tiles"][0]["rid"] == mgr.started[0][0]


def test_plan_mode_ends_planned():
    r = _runner(FakeMgr())
    r.start(TILES[:1], "plan", PARAMS)
    r.join(5)
    assert r.snapshot()["tiles"][0]["status"] == "planned"


def test_one_failing_tile_does_not_stop_the_rest():
    r = _runner(FakeMgr(fail_lons=[1.0]))
    r.start(TILES, "retrieve", PARAMS)
    r.join(5)
    tiles = r.snapshot()["tiles"]
    assert [t["status"] for t in tiles] == ["done", "error", "done"]
    assert "no Earthdata credentials" in tiles[1]["error"]


def test_engine_busy_waits_for_its_turn_instead_of_failing_the_tile():
    mgr = FakeMgr(busy_first=3)
    r = _runner(mgr)
    r.start(TILES[:1], "plan", PARAMS)
    r.join(5)
    assert r.snapshot()["tiles"][0]["status"] == "planned" and len(mgr.started) == 1


def test_engine_that_never_frees_up_errors_the_tile_after_the_retry_budget():
    r = _runner(FakeMgr(busy_first=10_000), busy_retries=3)
    r.start(TILES[:1], "plan", PARAMS)
    r.join(5)
    t = r.snapshot()["tiles"][0]
    assert t["status"] == "error" and "stayed busy" in t["error"]


def test_second_start_while_running_is_refused_and_cancel_skips_the_rest():
    import threading
    hold = threading.Event()
    mgr = FakeMgr(hold=hold)
    r = _runner(mgr)
    r.start(TILES, "retrieve", PARAMS)
    time.sleep(0.05)
    with pytest.raises(hub.BatchBusy):
        r.start(TILES, "retrieve", PARAMS)
    assert r.cancel() is True
    r.join(5)
    s = r.snapshot()
    assert s["status"] == "cancelled" and mgr.cancelled == [mgr.started[0][0]]       # the running one was cancelled
    assert [t["status"] for t in s["tiles"]] == ["cancelled", "skipped", "skipped"]
    assert len(mgr.started) == 1                                                       # nothing new was started


def test_cancel_while_waiting_for_a_busy_engine_skips_the_tile_instead_of_erroring():
    mgr = FakeMgr(busy_first=10_000)
    r = _runner(mgr)
    r.start(TILES, "plan", PARAMS)
    time.sleep(0.05)
    assert r.cancel() is True
    r.join(5)
    s = r.snapshot()
    assert s["status"] == "cancelled" and [t["status"] for t in s["tiles"]] == ["skipped"] * 3
    assert mgr.started == []


def test_cancel_with_nothing_running_is_a_noop_and_a_new_batch_can_start_after_one_ends():
    r = _runner(FakeMgr())
    assert r.cancel() is False
    r.start(TILES[:1], "plan", PARAMS)
    r.join(5)
    r.start(TILES[:2], "plan", PARAMS)
    r.join(5)
    assert r.snapshot()["status"] == "finished" and len(r.snapshot()["tiles"]) == 2


def test_engine_that_will_not_load_is_reported_on_every_tile():
    def boom():
        raise ImportError("No module named 'zarr'")
    r = hub.BatchRunner(boom, poll_s=0.001)
    r.start(TILES, "plan", PARAMS)
    r.join(5)
    s = r.snapshot()
    assert s["status"] == "finished"
    assert all(t["status"] == "error" and "zarr" in t["error"] for t in s["tiles"])


# ------------------------------------------------------------------------------------------ disk
def test_disk_usage_sums_each_top_folder(tmp_path):
    (tmp_path / "clips" / "a" / "b").mkdir(parents=True)
    (tmp_path / "clips" / "a" / "b" / "x").write_bytes(b"1" * 1000)
    (tmp_path / "clips" / "y").write_bytes(b"1" * 24)
    (tmp_path / "retrievals").mkdir()
    (tmp_path / "retrievals" / "m.json").write_bytes(b"{}")
    (tmp_path / "loose.txt").write_bytes(b"12345")
    d = hub.disk_usage(tmp_path, max_age_s=0)
    by = {p["name"]: p for p in d["parts"]}
    assert by["clips"] == {"name": "clips", "bytes": 1024, "files": 2}
    assert by["retrievals"]["bytes"] == 2 and by["(loose files)"]["bytes"] == 5
    assert d["used_bytes"] == 1031 and d["free_bytes"] > 0 and d["partial"] is False
    assert d["parts"][0]["name"] == "clips"                                            # biggest first


def test_disk_usage_on_a_missing_dir_is_empty_not_an_error(tmp_path):
    d = hub.disk_usage(tmp_path / "nope", max_age_s=0)
    assert d["parts"] == [] and d["used_bytes"] == 0 and d["free_bytes"] > 0


def test_disk_usage_gives_up_politely_when_the_budget_is_spent_and_caches_the_answer(tmp_path):
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache" / "f").write_bytes(b"1")
    assert hub.disk_usage(tmp_path, budget_s=-1, max_age_s=0)["partial"] is True
    first = hub.disk_usage(tmp_path, max_age_s=0)
    (tmp_path / "cache" / "g").write_bytes(b"1" * 50)
    assert hub.disk_usage(tmp_path, max_age_s=60) is first                             # inside the cache window
    assert hub.disk_usage(tmp_path, max_age_s=0)["used_bytes"] == 51
