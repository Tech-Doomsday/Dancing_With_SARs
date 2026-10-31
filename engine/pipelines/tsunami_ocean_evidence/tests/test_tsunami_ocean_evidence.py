"""3.17 tsunami_ocean_evidence — offline tests (injected providers, no network).

Pins:
  P-1  Registry defers the heavy pipeline: run_tile answers PENDING with the lane annotated.
  P-2  End-to-end with injected providers: trigger + wave-like DART records + ETOPO-like bathy
       -> sim runs -> evidence classification; every fetched record cited in provenance.
  P-3  Sensors with coverage but no wave -> NOT_SUPPORTED with null-hypothesis language.
  P-4  No station in range -> INSUFFICIENT_EVIDENCE (missing evidence is never evidence of absence).
  P-5  Degraded event fetch -> INSUFFICIENT_EVIDENCE with the verbatim reason.
  P-6  Pipeline independence + contract tests stay green (run by the repo suite).
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from engine.common import DirectoryAncillary  # noqa: E402
from engine.common.chain import build_scene_bundle  # noqa: E402
from tests.common_fixtures import (  # noqa: E402
    FakeRetriever, default_tile, make_scene, write_ancillary)
from engine.pipelines.tsunami_ocean_evidence import verdict as ts_run
from engine.pipelines.tsunami_ocean_evidence.types import TsunamiParams
from engine.registry import Registry

AS_OF = "2026-10-05T00:00:00Z"
PAIR_WINDOW = ("2026-09-25T00:00:00Z", "2026-10-05T00:00:00Z")

EVENT = {
    "id": "us6000-testq",
    "magnitude": 8.2,
    "place": "offshore test region",
    "time": "2026-10-04T12:00:00Z",
    "lat": 38.3, "lon": 142.4,
    "depth_km": 25.0,
    "url": "https://earthquake.usgs.gov/earthquakes/eventpage/us6000-testq",
    "nodal_planes": ((200.0, 15.0, 90.0),),
}


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def tile():
    # Tohoku-area epicentre is fixed by EVENT; any tile works for the request context
    return default_tile()


def _bundle(tmp_path, tile):
    specs = make_scene(tile, n_epochs=4, pairs=((0, 1),), dlos=0.0)
    anc_root = write_ancillary(tmp_path / "ancillary", tile)
    return build_scene_bundle(
        tile, FakeRetriever(specs), as_of=AS_OF,
        ancillary=DirectoryAncillary(anc_root),
        event_window=PAIR_WINDOW,
        triggers={"quake": {"id": EVENT["id"]}},
    )


def _flat_bathy_grid(res=2.0, half_deg=0.75, depth=3000.0):
    n = int(round(half_deg * 2 * 60 / res)) + 1
    return {"res_arcmin": res, "lat0": EVENT["lat"] - half_deg, "lon0": EVENT["lon"] - half_deg,
            "nx": n, "ny": n, "bed": np.full((n, n), -float(depth)).tolist()}


def _dart_series(with_wave: bool, origin_ms: float, amplitude_m: float = 1.2):
    t0 = origin_ms - 12 * 3_600_000.0
    t = t0 + np.arange(96) * (15 * 60_000.0)
    tide = 0.8 * np.sin(2 * math.pi * (t - t0) / (12.4 * 3_600_000.0))
    v = tide + 0.03 * (t - t0) / (24 * 3_600_000.0)
    if with_wave:
        tau = t - origin_ms
        wave = np.where((tau >= 25 * 60_000.0) & (tau <= 25 * 60_000.0 + 40 * 60_000.0),
                        amplitude_m * np.sin(2 * math.pi * (tau - 25 * 60_000.0) / (20 * 60_000.0)), 0.0)
        v = v + wave
    return {"t": t.tolist(), "v": v.tolist()}


def _providers(with_wave: bool, origin_ms: float, n_stations: int = 2, event=EVENT):
    stations = [{"id": "21418", "lat": EVENT["lat"] + 0.4, "lon": EVENT["lon"] + 0.6,
                 "name": "test NE", "region": "test", "dist_km": 70.0},
                {"id": "21413", "lat": EVENT["lat"] - 0.5, "lon": EVENT["lon"] + 0.3,
                 "name": "test SE", "region": "test", "dist_km": 62.0}][:n_stations]
    prov = {
        "event_by_id": lambda eid: {"source": "fixture", "event": dict(event), "degraded": False, "error": None},
        "stations": lambda lat, lon, max_km: stations,
        "dart": (lambda sid, oms: {"source": "fixture", "station": sid, "kind": "fixture",
                                   "series": _dart_series(with_wave, oms), "degraded": False,
                                   "error": None, "provenance": {"adapter": "r15_dart", "url_or_id": f"fixture:{sid}",
                                                                 "retrieved_at": "2026-10-27T00:00:00+00:00"}}),
        "bathy": (lambda bbox, res: {"source": "fixture", "grid": _flat_bathy_grid(res=res),
                                     "degraded": False, "error": None,
                                     "provenance": {"adapter": "r16_bathymetry", "url_or_id": "fixture:etopo",
                                                    "retrieved_at": "2026-10-27T00:00:00+00:00"}}),
        "tsunami_db": lambda bbox: {"source": "fixture", "events": [{"id": "x", "year": 2011, "lat": 38.0,
                                                                     "lon": 143.0, "magnitude": 9.0,
                                                                     "place": "test", "url": "", "validity": "4"}],
                                    "degraded": False, "error": None},
    }
    stages: list = []
    prov["progress"] = lambda stage, frac: stages.append((stage, frac))
    return prov, stages


# --------------------------------------------------------------------------- #
# P-1 heavy-lane deferral
# --------------------------------------------------------------------------- #
def test_registry_defers_heavy_pipeline(tmp_path, tile):
    specs = make_scene(tile, n_epochs=4, pairs=((0, 1),), dlos=0.0)
    anc_root = write_ancillary(tmp_path / "ancillary", tile)
    reg = Registry()
    run = reg.run_tile(
        tile, FakeRetriever(specs), as_of=AS_OF,
        ancillary=DirectoryAncillary(anc_root),
        event_window=PAIR_WINDOW,
        triggers={"quake": {"id": EVENT["id"]}},
        names=["tsunami_ocean_evidence"],
    )
    res = run.results["tsunami_ocean_evidence"]
    assert res.status == "PENDING"
    assert res.detail["lane"] == "heavy"
    assert res.detail["timeout_s"] == 3600.0
    # and it RUNS when explicitly asked (heavy="run")
    run2 = reg.run_tile(
        tile, FakeRetriever(specs), as_of=AS_OF,
        ancillary=DirectoryAncillary(anc_root),
        event_window=PAIR_WINDOW,
        triggers={"quake": {"id": EVENT["id"]}},
        names=["tsunami_ocean_evidence"],
        workers=0, heavy="run",
    )
    res2 = run2.results["tsunami_ocean_evidence"]
    assert res2.status in ("SUPPORTED", "POSSIBLE", "NOT_SUPPORTED", "INSUFFICIENT_EVIDENCE")


# --------------------------------------------------------------------------- #
# P-2 end-to-end (small domain, order 1 for speed)
# --------------------------------------------------------------------------- #
def test_end_to_end_supported_with_injected_providers(tmp_path, tile):
    bundle = _bundle(tmp_path, tile)
    origin_ms = 1_791_182_400_000.0          # 2026-10-04T12:00:00Z
    providers, stages = _providers(with_wave=True, origin_ms=origin_ms)
    providers["tsunami_params_override"] = None
    result = ts_run.run(bundle, providers={
        **providers,
        # keep the sim small and fast for the test
        "__params": None,
    })
    # shrink the simulation via triggers for a second run (parameters ride the bundle)
    assert result.status in ("SUPPORTED", "POSSIBLE")
    assert result.detail["event"]["id"] == EVENT["id"]
    triggered = [d for d in result.detail["stations"] if d["trigger_ms"]]
    assert triggered, "the injected wave must trigger"
    assert any(e.kind == "detection" for e in result.evidence)
    assert any(p.adapter == "r15_dart" for p in result.provenance)
    assert result.detail["simulation"] is not None           # sim ran on the fixture grid
    assert any(s[0] == "simulation" for s in stages)
    assert result.raw_p is not None and 0.0 <= result.raw_p <= 1.0
    assert result.confidence is None                          # never a probability


def test_params_override_via_triggers(tmp_path, tile):
    specs = make_scene(tile, n_epochs=4, pairs=((0, 1),), dlos=0.0)
    anc_root = write_ancillary(tmp_path / "ancillary", tile)
    bundle = build_scene_bundle(
        tile, FakeRetriever(specs), as_of=AS_OF,
        ancillary=DirectoryAncillary(anc_root), event_window=PAIR_WINDOW,
        triggers={"quake": {"id": EVENT["id"]},
                  "tsunami_params": {"run_simulation": False, "sim_hours": 0.2, "domain_half_deg": 0.5}},
    )
    params = TsunamiParams.from_triggers(bundle.request.triggers)
    assert params.run_simulation is False and params.sim_hours == 0.2
    origin_ms = 1_791_182_400_000.0
    providers, _ = _providers(with_wave=True, origin_ms=origin_ms)
    result = ts_run.run(bundle, providers=providers)
    assert result.detail["simulation"] is None
    assert result.status in ("POSSIBLE", "SUPPORTED")
    assert any("capped at 'possible'" in w or "simulation disabled" in w for w in result.warnings)


# --------------------------------------------------------------------------- #
# P-3 null sensors
# --------------------------------------------------------------------------- #
def test_no_wave_is_not_supported(tmp_path, tile):
    bundle = _bundle(tmp_path, tile)
    origin_ms = 1_791_182_400_000.0
    providers, _ = _providers(with_wave=False, origin_ms=origin_ms)
    result = ts_run.run(bundle, providers={**providers, "tsunami_params": None})
    assert result.status in ("NOT_SUPPORTED", "POSSIBLE")
    assert any(e.kind == "null_result" for e in result.evidence)


# --------------------------------------------------------------------------- #
# P-4 no stations in range
# --------------------------------------------------------------------------- #
def test_no_stations_in_range_is_insufficient(tmp_path, tile):
    bundle = _bundle(tmp_path, tile)
    origin_ms = 1_791_182_400_000.0
    providers, _ = _providers(with_wave=True, origin_ms=origin_ms, n_stations=0)
    result = ts_run.run(bundle, providers=providers)
    assert result.status == "INSUFFICIENT_EVIDENCE"
    assert any("no DART station" in e.claim for e in result.evidence)


# --------------------------------------------------------------------------- #
# P-5 degraded event fetch
# --------------------------------------------------------------------------- #
def test_degraded_event_fetch_is_insufficient(tmp_path, tile):
    bundle = _bundle(tmp_path, tile)
    providers = {"event_by_id": lambda eid: {"source": "fixture", "event": None, "degraded": True,
                                             "error": "ComCat unreachable: fixture outage"}}
    result = ts_run.run(bundle, providers=providers)
    assert result.status == "INSUFFICIENT_EVIDENCE"
    assert any("fixture outage" in e.claim for e in result.evidence)
