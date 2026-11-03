"""API tests: the serving slice over a persisted bundle (offline; the EQ pipeline runs with an
injected event provider, the heavy 3.17 runs through the jobs lane)."""
from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from engine.common import DirectoryAncillary  # noqa: E402
from engine.common.chain import build_scene_bundle  # noqa: E402
from engine.common.bundle_io import save_bundle  # noqa: E402
from tests.common_fixtures import (  # noqa: E402
    FakeRetriever, default_tile, make_scene, write_ancillary)

AS_OF = "2026-08-20T00:00:00Z"
# the fixture stack's pairs are 12 days apart from 2026-07-01; the event window must sit inside
# the LAST pair's span (07-25 -> 08-06) or P12 routing answers "no" for the EQ pipeline
WINDOW = ("2026-07-26T00:00:00Z", "2026-08-05T00:00:00Z")
from datetime import datetime, timezone as _tz
_EVENT_TIME = "2026-08-01T12:00:00Z"
EVENT = {"id": "us6000-api", "magnitude": 7.1, "place": "offshore test", "time": _EVENT_TIME,
         "lat": 38.3, "lon": 142.4, "depth_km": 20.0, "url": None,
         "nodal_planes": ((200.0, 15.0, 90.0),)}
ORIGIN_MS = datetime.fromisoformat(_EVENT_TIME.replace("Z", "+00:00")).timestamp() * 1000.0


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory):
    """One data dir with a persisted bundle; the EQ event provider is injected."""
    tmp = tmp_path_factory.mktemp("api")
    tile = default_tile()
    specs = make_scene(tile, n_epochs=4, pairs=((0, 1), (1, 2), (2, 3)), dlos=0.05)
    anc_root = write_ancillary(tmp / "ancillary", tile)
    bundle = build_scene_bundle(tile, FakeRetriever(specs), as_of=AS_OF,
                                ancillary=DirectoryAncillary(anc_root), event_window=WINDOW,
                                triggers={"quake": {"id": EVENT["id"]}})
    save_bundle(bundle, tmp / "bundles")

    from engine.pipelines.earthquake_coseismic import fetch as eq_fetch

    def provider(bbox, t0, t1):
        # the EQ pipeline ranks events by distance to the TILE and screens by pattern correlation:
        # use the EQ test suite's CALIBRATED fixture event (grid-searched so its Okada forward
        # prediction matches the truth bowl: ratio ~1.1, r ~+0.78) at its anchored epicentre
        from pyproj import Transformer
        t = default_tile()
        tr = Transformer.from_crs(t.epsg, 4326, always_xy=True)
        lon, lat = tr.transform(t.e0 + 4000.0 - 3000.0, t.n0 + 5500.0 - 3000.0)
        ev = {**EVENT, "magnitude": 5.6, "time": "2026-08-01T00:00:00Z",
              "lat": float(lat), "lon": float(lon), "depth_km": 5.0,
              "nodal_planes": ((90.0, 40.0, -90.0),)}
        return {"source": "fixture", "events": [ev], "degraded": False, "error": None}
    eq_fetch.set_event_provider(provider)
    yield tmp
    eq_fetch.reset_event_provider()


@pytest.fixture()
def client(data_dir, monkeypatch):
    from fastapi.testclient import TestClient
    from engine.api import state as api_state
    from engine.api.main import create_app
    import engine.common.config as engine_config

    # patch at the source so BOTH the singletons and Registry() see the test data dir
    # (modules that did `from ... import data_root` hold their own reference)
    import engine.api.state as _state
    import engine.common.config as engine_config
    import engine.registry as engine_registry
    for mod in (engine_config, engine_registry, _state):
        monkeypatch.setattr(mod, "data_root", lambda: Path(data_dir))
    api_state.get_runner.cache_clear()
    app = create_app()
    with TestClient(app) as c:
        yield c, data_dir
    api_state.get_runner.cache_clear()


def test_health_and_pipelines(client):
    c, _ = client
    r = c.get("/api/health")
    assert r.status_code == 200 and r.json()["ok"] is True
    r = c.get("/api/pipelines")
    assert r.status_code == 200
    names = {p["name"] for p in r.json()["pipelines"]}
    assert "earthquake_coseismic" in names and "tsunami_ocean_evidence" in names
    eq = next(p for p in r.json()["pipelines"] if p["name"] == "tsunami_ocean_evidence")
    assert eq["execution"] == "heavy" and eq["needs_sar"] is False


def test_bundles_and_tiles_routes(client):
    c, _ = client
    r = c.get("/api/bundles")
    assert r.status_code == 200 and len(r.json()["bundles"]) >= 1
    r = c.get("/api/tiles")
    assert r.status_code == 200      # empty before any run is fine (no 404 on the list)


def test_run_equake_then_tile_verdicts(client):
    c, data_dir = client
    bundles = c.get("/api/bundles").json()["bundles"]
    bundle_id = bundles[0]["bundle_id"]
    r = c.post("/api/runs", json={"bundle_id": bundle_id,
                                  "pipelines": ["earthquake_coseismic"], "params": {}})
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]
    for _ in range(100):
        state = c.get(f"/api/runs/{run_id}").json()
        if state["status"] in ("ok", "error"):
            break
        time.sleep(0.1)
    assert state["status"] == "ok", state.get("error")
    eq = next(v for v in state["results"] if v["pipeline"] == "earthquake_coseismic")
    assert eq["status"] == "SUPPORTED"
    assert any(e.get("kind") == "detection" for e in eq["evidence"])

    # the canonical verdict store now answers /api/tiles/{id}/verdicts
    tile_id = eq["tile"]
    r = c.get(f"/api/tiles/{tile_id}/verdicts")
    assert r.status_code == 200
    vs = r.json()["verdicts"]
    assert any(v["pipeline"] == "earthquake_coseismic" and v["status"] == "SUPPORTED" for v in vs)
    assert all("tier" in v for v in vs)
    r = c.get("/api/tiles")
    assert any(t["tile"] == tile_id for t in r.json()["tiles"])


def test_unknown_bundle_404(client):
    c, _ = client
    r = c.post("/api/runs", json={"bundle_id": "does-not-exist", "pipelines": ["earthquake_coseismic"]})
    assert r.status_code == 404


def _run_to_ok(c, bundle_id, body):
    r = c.post("/api/runs", json={"bundle_id": bundle_id, **body})
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]
    state = {}
    for _ in range(200):
        state = c.get(f"/api/runs/{run_id}").json()
        if state["status"] in ("ok", "error"):
            break
        time.sleep(0.1)
    assert state["status"] == "ok", state.get("error")
    return run_id, state


def test_run_state_is_flat_not_nested(client):
    """UI contract pin (final-prompt defect 2): GET /api/runs/{id} returns the state FLAT
    (id/status/results at the top level). The run page polls exactly this shape — a nested
    `run` key here would silently leave the UI on 'Loading run…' forever."""
    c, _ = client
    bundles = c.get("/api/bundles").json()["bundles"]
    run_id, state = _run_to_ok(c, bundles[0]["bundle_id"],
                               {"pipelines": ["earthquake_coseismic"], "params": {}})
    payload = c.get(f"/api/runs/{run_id}").json()
    assert payload["ok"] is True
    assert "run" not in payload, "the state must stay flat — the UI reads payload.status directly"
    assert {"id", "status", "progress", "error", "results"} <= set(payload)


def test_run_params_are_applied_not_just_recorded(client):
    """Final-prompt defect 3: `coherence_threshold=0.99` used to be recorded while the
    pipeline still ran 0.5 ('a code comment admits it'). Params now ride the request
    triggers into EqParams.from_triggers, and detail.params proves what was applied."""
    c, _ = client
    bundles = c.get("/api/bundles").json()["bundles"]
    bundle_id = bundles[0]["bundle_id"]

    run_id, state = _run_to_ok(c, bundle_id,
                               {"pipelines": ["earthquake_coseismic"],
                                "params": {"coherence_threshold": 0.99}})
    eq = next(v for v in state["results"] if v["pipeline"] == "earthquake_coseismic")
    assert eq["detail"]["params"]["coherence_threshold"] == 0.99
    # nearly every pixel now fails the coherence gate -> honest data-gap verdict
    assert eq["status"] == "INSUFFICIENT_EVIDENCE"
    assert any("pixels pass the coherence threshold" in str(e.get("claim", ""))
               for e in eq["evidence"])

    run_id, state = _run_to_ok(c, bundle_id,
                               {"pipelines": ["earthquake_coseismic"],
                                "params": {"detection_floor_m": 0.2}})
    eq = next(v for v in state["results"] if v["pipeline"] == "earthquake_coseismic")
    assert eq["detail"]["params"]["detection_floor_m"] == 0.2
    # floor 200 mm > the 50 mm truth peak: nothing detectable, event present -> NOT_SUPPORTED
    assert eq["status"] == "NOT_SUPPORTED"
    assert eq["detail"]["decision"]["rule"] == "no_detection"


def test_run_params_out_of_range_and_unknown_are_400(client):
    c, _ = client
    bundles = c.get("/api/bundles").json()["bundles"]
    bundle_id = bundles[0]["bundle_id"]
    r = c.post("/api/runs", json={"bundle_id": bundle_id, "pipelines": ["earthquake_coseismic"],
                                  "params": {"coherence_threshold": 7.0}})
    assert r.status_code == 400 and "outside" in r.json()["detail"]["error"]
    r = c.post("/api/runs", json={"bundle_id": bundle_id, "pipelines": ["earthquake_coseismic"],
                                  "params": {"bogus_knob": 1.0}})
    assert r.status_code == 400 and "bogus_knob" in r.json()["detail"]["error"]


def test_run_conflict_409_while_a_run_is_active(client, monkeypatch):
    """Second POST /runs while one is active -> 409 (single-flight; confirmed by hand in
    the final prompt — now pinned)."""
    import threading

    c, _ = client
    bundles = c.get("/api/bundles").json()["bundles"]
    bundle_id = bundles[0]["bundle_id"]
    started = threading.Event()
    release = threading.Event()

    from engine.api import jobs as api_jobs
    real_execute = api_jobs.JobRunner._execute

    def slow_execute(self, *a, **k):
        started.set()
        release.wait(30)
        return real_execute(self, *a, **k)

    monkeypatch.setattr(api_jobs.JobRunner, "_execute", slow_execute)
    import json as _json
    r = c.post("/api/runs", json={"bundle_id": bundle_id,
                                  "pipelines": ["earthquake_coseismic"], "params": {}})
    assert r.status_code == 200
    first = r.json()["run_id"]
    assert started.wait(10), "first run never started"
    r2 = c.post("/api/runs", json={"bundle_id": bundle_id,
                                   "pipelines": ["earthquake_coseismic"], "params": {}})
    assert r2.status_code == 409, r2.text
    release.set()
    for _ in range(200):
        if c.get(f"/api/runs/{first}").json()["status"] in ("ok", "error"):
            break
        time.sleep(0.1)


def test_api_events_route_offline_and_upstream_error(client, monkeypatch):
    """GET /api/events is display-only ComCat context; upstream failures surface as 502."""
    c, _ = client
    from engine.retrieval.clients.adapters import r1_comcat

    monkeypatch.setattr(r1_comcat, "fetch_events",
                        lambda *a, **k: {"source": "fixture", "events": [EVENT],
                                         "degraded": False, "error": None})
    r = c.get("/api/events", params={"lon": -118.3, "lat": 34.05, "days": 30})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert any(e["id"] == "us6000-api" for e in r.json()["events"])

    monkeypatch.setattr(r1_comcat, "fetch_events",
                        lambda *a, **k: {"source": "usgs-comcat", "events": [],
                                         "degraded": True, "error": "ComCat unreachable: drill"})
    r = c.get("/api/events", params={"lon": -118.3, "lat": 34.05})
    assert r.status_code == 502
    assert "ComCat unreachable" in r.json()["detail"]["error"]


def test_run_layer_endpoint_serves_payload_with_bounds(client):
    """The layer route returns the b64 grid + its geographic frame (bounds_lonlat, plan
    6.4 — the map no longer has to fall back to the bundle bbox)."""
    c, _ = client
    bundles = c.get("/api/bundles").json()["bundles"]
    run_id, state = _run_to_ok(c, bundles[0]["bundle_id"],
                               {"pipelines": ["earthquake_coseismic"], "params": {}})
    r = c.get(f"/api/runs/{run_id}/layers/displacement")
    assert r.status_code == 200
    layer = r.json()["layer"]
    assert layer["name"] == "displacement" and layer["dtype"] == "<f4"
    assert layer["bounds_lonlat"] and len(layer["bounds_lonlat"]) == 4
    w, s, e, n = layer["bounds_lonlat"]
    assert -180 <= w < e <= 180 and -90 <= s < n <= 90

    r = c.get(f"/api/runs/{run_id}/layers/not-a-layer")
    assert r.status_code == 404
    assert "displacement" in r.json()["detail"]["available"]
    r = c.get("/api/runs/unknown-run/layers/displacement")
    assert r.status_code == 404


def test_replay_artifacts_written_for_every_run(client):
    """plan 13: data/runs/<run_id>/ carries params.json, result.json (grids as .npy
    pointers) and layers/*.npy — so any verdict can be replayed offline."""
    import base64 as _b64

    import numpy as _np

    c, data_dir = client
    bundles = c.get("/api/bundles").json()["bundles"]
    run_id, state = _run_to_ok(c, bundles[0]["bundle_id"],
                               {"pipelines": ["earthquake_coseismic"],
                                "params": {"significance_k": 3.0}})
    run_dir = Path(data_dir) / "runs" / run_id
    params = (run_dir / "params.json").read_text()
    assert bundles[0]["bundle_id"] in params and "significance_k" in params
    result = (run_dir / "result.json").read_text()
    assert "earthquake_coseismic" in result
    assert "<see layers/" in result                       # grids moved to .npy, payloads carry pointers
    npys = sorted((run_dir / "layers").glob("*.npy"))
    assert npys, "at least the displacement layer must be persisted"
    # one stored grid round-trips to the shape recorded in result.json
    rec = json.loads(result)
    payload = rec["results"][0]["detail"]["layers"]["displacement"]
    arr = _np.load(run_dir / payload["npy"])
    assert list(arr.shape) == payload["shape"] and arr.dtype.str == payload["dtype"]


def test_heavy_pipeline_runs_through_jobs_lane(client):
    c, data_dir = client
    # offline providers for the heavy pipeline, injected at the prep-module seam (worker threads
    # share the process, so the module-level patch reaches the job)
    from engine.pipelines.tsunami_ocean_evidence import prep as t17_prep, verdict as t17_verdict

    def fake_event(bundle, params, providers=None):
        return {"event": EVENT, "degraded": False, "error": None, "provenance": []}

    def fake_domain(event, params, providers=None):
        n = 31
        return {"bathy": {"grid": {"res_arcmin": 2, "lat0": EVENT["lat"] - 0.25,
                                   "lon0": EVENT["lon"] - 0.25, "nx": n, "ny": n,
                                   "bed": np.full((n, n), -3000.0).tolist()},
                          "degraded": False, "error": None, "provenance_records": []},
                "bbox": (EVENT["lon"] - 0.25, EVENT["lat"] - 0.25, EVENT["lon"] + 0.25, EVENT["lat"] + 0.25),
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

    monkey_targets = [(t17_prep, "pick_event", fake_event), (t17_prep, "build_domain", fake_domain),
                      (t17_prep, "pick_stations", fake_stations),
                      (t17_prep, "fetch_station_records", fake_records)]
    saved = [(m, n, getattr(m, n)) for m, n, _ in monkey_targets]
    for m, n, fn in monkey_targets:
        setattr(m, n, fn)
    try:
        bundles = c.get("/api/bundles").json()["bundles"]
        bundle_id = bundles[0]["bundle_id"]
        r = c.post("/api/runs", json={"bundle_id": bundle_id,
                                      "pipelines": ["tsunami_ocean_evidence"], "params": {}})
        assert r.status_code == 200, r.text
        run_id = r.json()["run_id"]
        state = {}
        for _ in range(300):
            state = c.get(f"/api/runs/{run_id}").json()
            if state["status"] in ("ok", "error"):
                break
            time.sleep(0.2)
        assert state["status"] == "ok", state.get("error")
        res = state["results"][0]
        assert res["pipeline"] == "tsunami_ocean_evidence"
        assert res["status"] in ("SUPPORTED", "POSSIBLE", "NOT_SUPPORTED", "INSUFFICIENT_EVIDENCE")
    finally:
        for m, n, fn in saved:
            setattr(m, n, fn)
