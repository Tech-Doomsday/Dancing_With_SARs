"""§5.6 new engine tests: tile-at, scans (happy/disabled/credentials/busy/stale), cache rules for
scans, D10 stale-run reset. Offline: the retriever is injected (FakeRetriever), the event
provider is the test_api fixture provider, the heavy 3.17 lane gets the same offline prep patches
test_api uses — no network anywhere."""
from __future__ import annotations

import math
import time

import pytest

from tests.test_api import client, data_dir  # noqa: F401  (pytest fixtures)

LON, LAT = -118.5, 34.0          # inside the fixture tile 11SLT66 (tests/common_fixtures.py)


# ----------------------------------------------------------------------------- tile-at (§5.2)
def test_tile_at_known_values(client):
    c, _ = client
    r = c.get("/api/tile-at", params={"lon": LON, "lat": LAT})
    assert r.status_code == 200 and r.json()["ok"] is True
    tile = r.json()["tile"]
    assert tile["id"] == "11SLT66"                      # the value tests/common_fixtures uses
    assert tile["epsg"] == 32611
    w, s, e, n = tile["bbox_lonlat"]
    assert w <= tile["center_lonlat"][0] <= e and s <= tile["center_lonlat"][1] <= n
    ring = tile["polygon_lonlat"]
    assert ring[0] == ring[-1] and len(ring) >= 5       # closed ring


def test_tile_at_edges_and_errors(client):
    c, _ = client
    # polar rejection is the ENGINE's text, verbatim (mgrs.band_of raises ValueError)
    r = c.get("/api/tile-at", params={"lon": 0, "lat": -80.5})
    assert r.status_code == 400
    assert "outside the UTM/MGRS band range (-80..84)" in r.json()["detail"]["error"]
    r = c.get("/api/tile-at", params={"lon": 0, "lat": 84.5})
    assert r.status_code == 400
    # band edges: +84 resolves; exactly -80 has no MGRS cell (the tile centre would sit outside)
    r = c.get("/api/tile-at", params={"lon": 0, "lat": -80})
    assert r.status_code == 400
    assert "outside the MGRS grid" in r.json()["detail"]["error"]
    assert c.get("/api/tile-at", params={"lon": 0, "lat": 84}).status_code == 200
    # +-180 longitude wraps into range (utm_zone normalises)
    assert c.get("/api/tile-at", params={"lon": 180, "lat": 0}).status_code == 200
    assert c.get("/api/tile-at", params={"lon": -180, "lat": 0}).status_code == 200
    r = c.get("/api/tile-at", params={"lon": 181, "lat": 0})
    assert r.status_code == 400 and "outside [-180, 180]" in r.json()["detail"]["error"]
    r = c.get("/api/tile-at", params={"lon": 0, "lat": 91})
    assert r.status_code == 400 and "outside [-90, 90]" in r.json()["detail"]["error"]


def test_tile_at_by_tile_id(client):
    """§7.7 URL restore: tile=<MGRS id> alone returns the same geometry as the point form —
    the UI never inverts ids itself."""
    c, _ = client
    r = c.get("/api/tile-at", params={"tile": "11SLT66"})
    assert r.status_code == 200 and r.json()["ok"] is True
    by_id = r.json()["tile"]
    by_pt = c.get("/api/tile-at", params={"lon": LON, "lat": LAT}).json()["tile"]
    assert by_id == by_pt                       # id -> geometry round-trips to the point answer
    # lowercase + spaces are normalised (same grammar as tile_from_id)
    assert c.get("/api/tile-at", params={"tile": "11slt66"}).json()["tile"]["id"] == "11SLT66"
    # errors: unknown id text is the engine's ValueError, verbatim; no mode -> 400
    r = c.get("/api/tile-at", params={"tile": "NOT_A_TILE"})
    assert r.status_code == 400 and "not an MGRS tile id" in r.json()["detail"]["error"]
    r = c.get("/api/tile-at", params={})
    assert r.status_code == 400 and "either lon+lat or a tile id" in r.json()["detail"]["error"]


# ----------------------------------------------------------------------------- scan guards
def test_scan_disabled_403(client, monkeypatch):
    c, _ = client
    monkeypatch.delenv("NISAR_ENABLE_SCANS", raising=False)
    r = c.post("/api/scans", json={"lon": LON, "lat": LAT})
    assert r.status_code == 403
    assert r.json()["detail"]["error"] == "scans are disabled on this engine (set NISAR_ENABLE_SCANS=1)"


def test_scan_without_credentials_503(client, monkeypatch):
    c, _ = client
    monkeypatch.setenv("NISAR_ENABLE_SCANS", "1")
    for var in ("EARTHDATA_TOKEN", "EARTHDATA_USERNAME", "EARTHDATA_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    r = c.post("/api/scans", json={"lon": LON, "lat": LAT})
    assert r.status_code == 503
    assert "EARTHDATA_TOKEN" in r.json()["detail"]["error"]
    assert "secret" not in r.json()["detail"]["error"].lower()


def test_scan_busy_409_names_the_active_run(client, monkeypatch):
    import threading

    c, _ = client
    monkeypatch.setenv("NISAR_ENABLE_SCANS", "1")
    monkeypatch.setenv("EARTHDATA_TOKEN", "test-token-present")
    _patch_retriever_factory(monkeypatch)
    bundles = c.get("/api/bundles").json()["bundles"]
    started, release = threading.Event(), threading.Event()

    from engine.api import jobs as api_jobs
    real_execute = api_jobs.JobRunner._execute

    def slow_execute(self, *a, **k):
        started.set()
        release.wait(30)
        return real_execute(self, *a, **k)

    monkeypatch.setattr(api_jobs.JobRunner, "_execute", slow_execute)
    r = c.post("/api/runs", json={"bundle_id": bundles[0]["bundle_id"],
                                  "pipelines": ["earthquake_coseismic"], "params": {}})
    assert r.status_code == 200
    assert started.wait(10)
    r2 = c.post("/api/scans", json={"lon": LON, "lat": LAT})
    assert r2.status_code == 409, r2.text
    assert r.json()["run_id"] in r2.json()["detail"]["error"]     # "engine busy with run X"
    release.set()


# ----------------------------------------------------------------------------- scan happy path
def _patch_retriever_factory(monkeypatch, retriever=None):
    """The injectable factory seam: tests inject FakeRetriever over the fixture scene."""
    from tests.common_fixtures import FakeRetriever, make_scene, default_tile

    specs = make_scene(default_tile(), n_epochs=4, pairs=((0, 1), (1, 2), (2, 3)), dlos=0.05)

    def factory():
        return FakeRetriever(retriever.specs.values() if retriever is not None else specs)

    import engine.api.jobs as api_jobs
    monkeypatch.setattr(api_jobs, "_default_retriever_factory", lambda: factory)


@pytest.fixture()
def scan_env(monkeypatch):
    monkeypatch.setenv("NISAR_ENABLE_SCANS", "1")
    monkeypatch.setenv("EARTHDATA_TOKEN", "test-token-present")
    _patch_retriever_factory(monkeypatch)


def _wait_run(c, run_id, timeout_s=120.0):
    deadline = time.monotonic() + timeout_s
    state = {}
    while time.monotonic() < deadline:
        state = c.get(f"/api/runs/{run_id}").json()
        if state["status"] in ("ok", "error"):
            return state
        time.sleep(0.1)
    raise AssertionError(f"run {run_id} did not finish: {state}")


@pytest.fixture()
def offline_tsunami():
    """The heavy 3.17 lane offline (same patches as tests/test_api.py::test_heavy_...)."""
    import numpy as np

    from engine.pipelines.tsunami_ocean_evidence import prep as t17_prep

    EVENT = {"id": "us6000-scan", "magnitude": 7.1, "place": "offshore test",
             "time": "2026-08-01T12:00:00Z", "lat": 38.3, "lon": 142.4, "depth_km": 20.0,
             "url": None, "nodal_planes": ((200.0, 15.0, 90.0),)}

    def fake_event(bundle, params, providers=None):
        return {"event": EVENT, "degraded": False, "error": None, "provenance": []}

    def fake_domain(event, params, providers=None):
        n = 31
        return {"bathy": {"grid": {"res_arcmin": 2, "lat0": EVENT["lat"] - 0.25,
                                   "lon0": EVENT["lon"] - 0.25, "nx": n, "ny": n,
                                   "bed": [[-3000.0] * n for _ in range(n)]},
                          "degraded": False, "error": None, "provenance_records": []},
                "bbox": (EVENT["lon"] - 0.25, EVENT["lat"] - 0.25,
                         EVENT["lon"] + 0.25, EVENT["lat"] + 0.25),
                "res_arcmin": 2, "notes": []}

    def fake_stations(event, params, providers=None):
        return [{"id": "21418", "lat": EVENT["lat"] + 0.3, "lon": EVENT["lon"] + 0.4,
                 "name": "x", "region": "x", "dist_km": 55.0}]

    def fake_records(stations, origin_ms, providers=None):
        from engine.pipelines.tsunami_ocean_evidence.types import StationDetection
        t0 = origin_ms - 12 * 3_600_000.0
        t = t0 + np.arange(96) * (15 * 60_000.0)
        v = 0.5 * np.sin(2 * math.pi * (t - t0) / (12.4 * 3_600_000.0))
        det = StationDetection(station_id=stations[0]["id"], lat=stations[0]["lat"],
                               lon=stations[0]["lon"], dist_km=55.0, kind="fixture",
                               availability={"state": "available"})
        det._series = {"t": t.tolist(), "v": v.tolist()}
        det._provenance = []
        return [det]

    saved = [(t17_prep, n, getattr(t17_prep, n))
             for n in ("pick_event", "build_domain", "pick_stations", "fetch_station_records")]
    for m, n, _ in saved:
        setattr(m, n, {"pick_event": fake_event, "build_domain": fake_domain,
                       "pick_stations": fake_stations, "fetch_station_records": fake_records}[n])
    yield EVENT
    for m, n, fn in saved:
        setattr(m, n, fn)


def test_scan_happy_path_is_a_run_with_tiers_and_detections(client, scan_env, offline_tsunami):
    c, _ = client
    r = c.post("/api/scans", json={"lon": LON, "lat": LAT,
                                   "as_of": "2026-08-20T00:00:00Z", "lookback_days": 120})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tile"]["id"] == "11SLT66"
    run_id = body["run_id"]
    state = _wait_run(c, run_id)
    assert state["status"] == "ok", state.get("error")
    # a scan is a run: same state shape + audit fields (§5.3 item 8)
    assert state["kind"] == "scan" and state["tile"] == "11SLT66" and state["bundle_id"]
    # DEC8: every manifest ran; 15 of 17 are honest NOT_MODELLED/NOT_APPLICABLE/...
    names = {res["pipeline"] for res in state["results"]}
    assert len(names) == 17
    # D4: every run-state result carries the 4.2 tier (the UI never re-derives it)
    from engine.verdict.calibrate import TIER_LABEL
    assert all(res.get("tier") in TIER_LABEL for res in state["results"])
    eq = next(res for res in state["results"] if res["pipeline"] == "earthquake_coseismic")
    assert eq["status"] in ("SUPPORTED", "UNCATALOGUED_CANDIDATE", "NOT_SUPPORTED", "NO_SIGNAL",
                            "INSUFFICIENT_EVIDENCE")
    # engine defaults (a scan takes no overrides): detail.params equals EqParams' own defaults
    from engine.pipelines.earthquake_coseismic.types import EqParams
    assert eq["detail"]["params"] == EqParams.from_triggers({}).to_dict()
    # D8: detections carry per-cluster geometry; the counts match the summary's clusters
    if eq["detail"].get("pairs_analysed"):
        selected = next(p for p in eq["detail"]["pairs_analysed"] if p.get("selected"))
        if not selected.get("skipped"):
            assert len(eq["detail"]["detections"]) == selected["clusters"]["count"]
            for det in eq["detail"]["detections"]:
                w, s_, e, n = det["bbox_lonlat"]
                assert -180 <= w < e <= 180 and -90 <= s_ < n <= 90
                assert det["id"].startswith("earthquake_coseismic:")
            assert eq["detail"]["detections_truncated"] is False

    # slim mode strips grids but keeps metadata
    slim = c.get(f"/api/runs/{run_id}", params={"slim": 1}).json()
    slim_eq = next(res for res in slim["results"] if res["pipeline"] == "earthquake_coseismic")
    payload = slim_eq["detail"]["layers"]["displacement"]
    assert "data_b64" not in payload and "data" not in payload
    assert payload["shape"] and payload["bounds_lonlat"]
    full = c.get(f"/api/runs/{run_id}").json()
    full_eq = next(res for res in full["results"] if res["pipeline"] == "earthquake_coseismic")
    assert full_eq["detail"]["layers"]["displacement"]["data_b64"]

    # D5: the pipeline-scoped layer route answers; a wrong pipeline 404s with the inventory
    r = c.get(f"/api/runs/{run_id}/pipelines/earthquake_coseismic/layers/displacement")
    assert r.status_code == 200 and r.json()["pipeline"] == "earthquake_coseismic"
    assert r.json()["layer"]["data_b64"]
    r = c.get(f"/api/runs/{run_id}/pipelines/tsunami_ocean_evidence/layers/displacement")
    assert r.status_code == 404 and "displacement" in r.json()["detail"]["available"]


def test_scan_run_state_carries_scan_request_for_audit(client, scan_env, offline_tsunami):
    c, data_dir = client
    r = c.post("/api/scans", json={"lon": LON, "lat": LAT,
                                   "as_of": "2026-08-20T00:00:00Z", "lookback_days": 120})
    assert r.status_code == 200
    state = _wait_run(c, r.json()["run_id"])
    assert state["status"] == "ok", state.get("error")
    from engine.api.state import get_runner
    row = get_runner().store.get_run(r.json()["run_id"])
    params = __import__("json").loads(row["params"])
    assert params["scan"] == {"lon": LON, "lat": LAT,
                              "as_of": "2026-08-20T00:00:00Z", "lookback_days": 120}


def test_scan_is_cached_and_custom_params_are_not(client, scan_env, offline_tsunami):
    from engine.api.state import get_runner
    from engine.verdict.cloud_cache import CloudCache

    from tests.test_cloud_cache import KEY, FakePostgrest
    pg = FakePostgrest()
    try:
        c, _ = client
        get_runner().cache = CloudCache(pg.url, KEY)
        r = c.post("/api/scans", json={"lon": LON, "lat": LAT,
                                       "as_of": "2026-08-20T00:00:00Z", "lookback_days": 120})
        assert r.status_code == 200
        state = _wait_run(c, r.json()["run_id"])
        assert state["status"] == "ok", state.get("error")
        assert state["cache"]["written"] >= 1, state["cache"]     # engine defaults -> cached
        n_after_scan = len(pg.requests)
        assert n_after_scan >= 1

        # a custom-params BUNDLE run is still never cached
        bundle_id = state["bundle_id"]
        r = c.post("/api/runs", json={"bundle_id": bundle_id,
                                      "pipelines": ["earthquake_coseismic"],
                                      "params": {"significance_k": 4.0}})
        assert r.status_code == 200
        state2 = _wait_run(c, r.json()["run_id"])
        assert state2["status"] == "ok" and state2["cache"]["written"] == 0
        assert len(pg.requests) == n_after_scan
    finally:
        pg.close()


def test_scan_error_is_a_run_status_not_a_500(client, scan_env, monkeypatch):
    """A tile with no data / a failing chain is the registry's own honest verdict or an error
    status — never an unhandled exception."""
    c, _ = client
    r = c.post("/api/scans", json={"lon": 12.0, "lat": 55.0,          # outside the fixture scene
                                   "as_of": "2026-08-20T00:00:00Z", "lookback_days": 120})
    assert r.status_code == 200
    state = _wait_run(c, r.json()["run_id"])
    # the fixture retriever answers every tile, so this is the honest empty-tile outcome
    assert state["status"] == "ok", state.get("error")
    assert state["results"] and all(res.get("tier") for res in state["results"])


# ----------------------------------------------------------------------------- D10 stale reset
def test_stale_run_is_reset_on_start_not_409_forever(client, tmp_path):
    """A crashed engine leaves a queued/running row: the NEXT start marks it error with the
    engine's own reason and proceeds; a genuinely live run is kept (single-flight intact)."""
    from engine.api.state import get_runner

    c, _ = client
    runner = get_runner()
    # simulate a crash: a queued row with no live state in this process
    runner.store.create_run("run-stale-crashed", "b", "11SLT66", ["earthquake_coseismic"], {},
                            kind="bundle")
    active_before = runner.store.active_run()
    assert active_before and active_before["id"] == "run-stale-crashed"

    bundles = c.get("/api/bundles").json()["bundles"]
    r = c.post("/api/runs", json={"bundle_id": bundles[0]["bundle_id"],
                                  "pipelines": ["earthquake_coseismic"], "params": {}})
    assert r.status_code == 200, r.text                      # NOT 409-forever
    row = runner.store.get_run("run-stale-crashed")
    assert row["status"] == "error"
    assert row["error"] == "engine restarted before this run finished"
    _wait_run(c, r.json()["run_id"])
