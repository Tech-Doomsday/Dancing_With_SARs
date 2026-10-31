"""UI-build-2 — gated /api/background/* tests (§1 gate, §4.2 groups, §4.5 endpoints, §5/§6).
Offline: the source is injected through routes_background._SOURCE_FACTORY (the retriever seam),
so pytest never touches the network. Honesty rules are pinned: unknown stays raw/404, an all-NaN
clip is refused with the engine's own text, and no number is ever invented.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.common_fixtures import (  # noqa: E402
    FakeRetriever, GranuleSpec, default_tile, make_scene, ref_for, write_ancillary, build_clip)
from engine.common.chain import build_scene_bundle  # noqa: E402
from engine.common import DirectoryAncillary  # noqa: E402
from engine.common.bundle_io import save_bundle  # noqa: E402
from engine.retrieval.clients.types import FetchResult, GranuleRef  # noqa: E402
from engine.api.granules_bbox import search_bbox_sync  # noqa: E402

AS_OF = "2026-08-20T00:00:00Z"
TOKEN = "test-bg-token"

WEST, SOUTH, EAST, NORTH = -119.0, 33.5, -117.0, 35.0

TEST_VECTOR = ("NISAR_L2_PR_GUNW_031_034_A_019_033_4000_SH_20260920T130823_20260920T130857_"
               "20261014T130823_20261014T130857_P05023_N_F_J_001")
GUNW_FLIPPED = ("NISAR_L2_PR_GUNW_031_034_A_019_029_4000_SH_20261014T130823_20261014T130857_"
                "20260920T130823_20260920T130857_P05023_N_F_J_001")
GCOV_A = ("NISAR_L2_PR_GCOV_031_034_A_019_4000_SHNA_A_20260920T130900_20260920T130930_"
          "P05023_N_F_J_001")
GCOV_B = ("NISAR_L2_PR_GCOV_031_034_A_019_4005_SHNA_A_20260920T130900_20260920T130930_"
          "P05023_N_F_J_001")
GCOV_LATER = ("NISAR_L2_PR_GCOV_033_034_A_019_4000_SHNA_A_20261002T130900_20261002T130930_"
              "P05023_N_F_J_001")
UNKNOWN_ID = "totally-unknown-id"


def _ring(lon=-118.2, lat=34.0, half=0.4):
    return [[lon - half, lat - half], [lon + half, lat - half], [lon + half, lat + half],
            [lon - half, lat + half]]


def _ref(gid: str, product: str, start: str, *, footprint=None, extra=None) -> GranuleRef:
    d = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    ref = ref_for(gid, product, d, track=34, direction="ASCENDING", frame=19)
    if footprint is not None:
        object.__setattr__(ref, "footprint", footprint)
    if extra is not None:
        object.__setattr__(ref, "extra", extra)
    return ref


def default_specs() -> list[GranuleSpec]:
    return [
        GranuleSpec(ref=_ref(TEST_VECTOR, "GUNW", "2026-09-20T13:08:23", footprint=_ring()), kind="GUNW"),
        GranuleSpec(ref=_ref(GUNW_FLIPPED, "GUNW", "2026-10-14T13:08:23", footprint=_ring()), kind="GUNW"),
        GranuleSpec(ref=_ref(GCOV_A, "GCOV", "2026-09-20T13:09:00", footprint=_ring()), kind="GCOV"),
        GranuleSpec(ref=_ref(GCOV_B, "GCOV", "2026-09-20T13:09:00", footprint=_ring()), kind="GCOV"),
        GranuleSpec(ref=_ref(GCOV_LATER, "GCOV", "2026-10-02T13:09:00", footprint=_ring()), kind="GCOV"),
    ]


class FakeSource:
    """The source protocol (search/fetch/inventory/close) over the fixture retriever."""

    def __init__(self, specs: list[GranuleSpec]):
        self.retr = FakeRetriever(specs)

    def search(self, bbox, t0, t1, product, fresh=False, max_results=50):
        return self.retr.search(bbox or (-180.0, -90.0, 180.0, 90.0), t0, t1, product,
                                fresh=fresh, max_results=max_results)

    def fetch(self, bbox, t0, t1, product, layers, granules):
        res = FetchResult([], {}, {})
        tile = default_tile()
        for g in granules:
            spec = self.retr.specs.get(g.granule_id)
            if spec is None:
                res.errors[g.granule_id] = "not in the fixture set"
                continue
            clip = build_clip(tile, spec, layers)
            if clip.grids:
                res.clips.append(clip)
            else:
                res.skipped[g.granule_id] = "bbox does not overlap this granule's grids"
        return res

    def inventory(self, ref) -> dict:
        clip = build_clip(default_tile(), self.retr.specs[ref.granule_id], None)
        grids = []
        for gk, g in clip.grids.items():
            grids.append({
                "path": gk.replace("__", "/"), "key": gk, "epsg": int(g.epsg),
                "nx": int(len(g.x)), "ny": int(len(g.y)), "dx": None, "dy": None,
                "extras": [], 
                "layers": [{"path": lk.replace("__", "/"), "key": lk, "shape": list(a.shape),
                            "dtype": str(a.dtype), "chunks": None, "readable": True, "why_not": None}
                           for lk, a in g.layers.items()],
            })
        return {"grids": grids, "source": "fixture", "n_layers": sum(len(g["layers"]) for g in grids)}

    def close(self):
        pass


# --------------------------------------------------------------------------- fixtures
@pytest.fixture(autouse=True)
def _clean_rate_windows():
    from engine.api import routes_background
    routes_background._BG_HITS.clear()
    routes_background._PREVIEW_HITS.clear()
    yield
    routes_background._BG_HITS.clear()
    routes_background._PREVIEW_HITS.clear()


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("background_api")
    tile = default_tile()
    specs = make_scene(tile, n_epochs=4, pairs=((0, 1), (1, 2), (2, 3)), dlos=0.05)
    anc_root = write_ancillary(tmp / "ancillary", tile)
    bundle = build_scene_bundle(tile, FakeRetriever(specs), as_of=AS_OF,
                                ancillary=DirectoryAncillary(anc_root))
    save_bundle(bundle, tmp / "bundles")
    return tmp, bundle


@pytest.fixture()
def client(data_dir, monkeypatch):
    from fastapi.testclient import TestClient
    from engine.api import state as api_state
    from engine.api import routes_background
    from engine.api.main import create_app
    import engine.common.config as engine_config
    import engine.registry as engine_registry
    tmp, bundle = data_dir
    for mod in (engine_config, engine_registry, api_state):
        monkeypatch.setattr(mod, "data_root", lambda: Path(tmp))
    api_state.get_runner.cache_clear()
    monkeypatch.setattr(routes_background, "_SOURCE_FACTORY", lambda: FakeSource(default_specs()))
    monkeypatch.setattr("engine.api.jobs.earthdata_present", lambda: True)
    monkeypatch.setattr("engine.api.routes_background.earthdata_present", lambda: True)
    monkeypatch.setenv("NISAR_BACKGROUND_TOKEN", TOKEN)
    app = create_app()
    with TestClient(app) as c:
        yield c, tmp, bundle
    api_state.get_runner.cache_clear()


def _auth(token=TOKEN):
    return {"Authorization": f"Bearer {token}"} if token is not None else {}


# --------------------------------------------------------------------------- §1 gate
def test_gate_unset_token_is_503_naming_the_var(client, monkeypatch):
    c, _tmp, _b = client
    monkeypatch.delenv("NISAR_BACKGROUND_TOKEN")
    r = c.get(f"/api/background/granule?id={TEST_VECTOR}")
    assert r.status_code == 503
    body = r.json()["detail"]
    assert body["ok"] is False and "NISAR_BACKGROUND_TOKEN" in body["error"]


def test_gate_wrong_and_missing_token_are_401(client):
    c, _tmp, _b = client
    r = c.get(f"/api/background/granule?id={TEST_VECTOR}", headers=_auth("wrong"))
    assert r.status_code == 401 and "Bearer" in r.json()["detail"]["error"]
    r = c.get(f"/api/background/granule?id={TEST_VECTOR}")
    assert r.status_code == 401


def test_gate_needs_credentials(client, monkeypatch):
    c, _tmp, _b = client
    monkeypatch.setattr("engine.api.routes_background.earthdata_present", lambda: False)
    r = c.get(f"/api/background/granule?id={TEST_VECTOR}", headers=_auth())
    assert r.status_code == 503 and "Earthdata" in r.json()["detail"]["error"]


def test_background_rate_limit_429(client, monkeypatch):
    from engine.api import routes_background
    monkeypatch.setattr(routes_background, "_BG_RATE", {"max": 1, "window_s": 60.0})
    c, _tmp, _b = client
    assert c.get(f"/api/background/granule?id={TEST_VECTOR}", headers=_auth()).status_code == 200
    r = c.get(f"/api/background/granule?id={TEST_VECTOR}", headers=_auth())
    assert r.status_code == 429
    assert "Retry-After" in r.headers and int(r.headers["Retry-After"]) >= 1
    assert "rate limit" in r.json()["detail"]["error"]


# --------------------------------------------------------------------------- §4.5 granule
def test_granule_detail_decoded_fields_and_inventory(client):
    c, _tmp, _b = client
    r = c.get(f"/api/background/granule?id={TEST_VECTOR}", headers=_auth())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    g = body["granule"]
    assert g["product"] == "GUNW" and g["track"] == 34 and g["frame"] == 19
    assert g["ref_time"] == "2026-09-20T13:08:23Z" and g["sec_time"] == "2026-10-14T13:08:23Z"
    assert g["baseline_days"] == 24.0 and g["polarization"] == "HH"
    assert any("orbit token" in f for f in g["flags"])
    assert body["grouped"] is True and body["group"]["n_variants"] == 1
    inv = body["inventory"]
    assert inv["n_layers"] > 0
    flat = [l["key"] for grid in inv["grids"] for l in grid["layers"]]
    assert any("unwrappedPhase" in k for k in flat)


def test_granule_unknown_layout_is_404_with_decoder_text(client):
    c, _tmp, _b = client
    r = c.get(f"/api/background/granule?id={UNKNOWN_ID}", headers=_auth())
    assert r.status_code == 404
    assert "no verified filename layout" in r.json()["detail"]["error"]


# --------------------------------------------------------------------------- §4.2 groups
def test_groups_same_epoch_two_bandwidths_one_group_two_variants(client):
    c, _tmp, _b = client
    out = search_bbox_sync(bbox=(WEST, SOUTH, EAST, NORTH), levels=["GCOV"],
                           t0="2026-09-01T00:00:00Z", t1="2026-10-30T00:00:00Z",
                           product_names={"GCOV": "GCOV"}, max_results=250, fresh=False,
                           retriever=FakeRetriever(default_specs()))
    group = next(g for g in out["groups"] if GCOV_A in g["granule_ids"])
    assert GCOV_B in group["granule_ids"]                     # ONE group, both variants
    assert group["n_variants"] == 2
    assert group["variant_facts"] == ["range_bandwidth_mhz"]
    assert {v["id"] for v in group["variants"]} == {GCOV_A, GCOV_B}


def test_groups_two_dates_two_groups(client):
    from engine.api.granules_bbox import search_bbox_sync
    out = search_bbox_sync(bbox=(WEST, SOUTH, EAST, NORTH), levels=["GCOV"],
                           t0="2026-09-01T00:00:00Z", t1="2026-10-30T00:00:00Z",
                           product_names={"GCOV": "GCOV"}, max_results=250, fresh=False,
                           retriever=FakeRetriever(default_specs()))
    singles = [g for g in out["groups"] if g["kind"] == "single" and g["grouped"]]
    assert len(singles) == 2
    assert {g["start"] for g in singles} == {"2026-09-20T13:09:00Z", "2026-10-02T13:09:00Z"}


def test_groups_pair_group_key_and_dedup(client):
    """TEST_VECTOR and the flipped id describe the SAME true pair window (ref-first vs sec-first
    storage) -> ONE group with two variants, flagged flipped. Distinct pairs never merge."""
    from engine.api.granules_bbox import search_bbox_sync
    out = search_bbox_sync(bbox=(WEST, SOUTH, EAST, NORTH), levels=["GUNW"],
                           t0="2026-09-01T00:00:00Z", t1="2026-10-30T00:00:00Z",
                           product_names={"GUNW": "GUNW"}, max_results=250, fresh=False,
                           retriever=FakeRetriever(default_specs()))
    pairs = [g for g in out["groups"] if g["kind"] == "pair" and g["grouped"]]
    assert len(pairs) == 1
    assert pairs[0]["n_variants"] == 2
    assert {TEST_VECTOR, GUNW_FLIPPED} <= set(pairs[0]["granule_ids"])
    assert any("flipped" in f for f in pairs[0]["flags"])


def test_groups_flipped_pair_flagged_and_true_time_order(client):
    from engine.api.granules_bbox import search_bbox_sync
    out = search_bbox_sync(bbox=(WEST, SOUTH, EAST, NORTH), levels=["GUNW"],
                           t0="2026-09-01T00:00:00Z", t1="2026-10-30T00:00:00Z",
                           product_names={"GUNW": "GUNW"}, max_results=250, fresh=False,
                           retriever=FakeRetriever(default_specs()))
    flipped = next(g for g in out["groups"] if GUNW_FLIPPED in g["granule_ids"])
    assert flipped["ref_time"] == "2026-09-20T13:08:23Z"      # true time order, not storage order
    assert flipped["sec_time"] == "2026-10-14T13:08:23Z"
    assert any("flipped" in f for f in flipped["flags"])


def test_groups_unknown_id_ungrouped_and_raw(client):
    from engine.api.granules_bbox import search_bbox_sync
    specs = default_specs() + [GranuleSpec(ref=_ref(UNKNOWN_ID, "GCOV", "2026-09-21T13:09:00"), kind="GCOV")]
    out = search_bbox_sync(bbox=(WEST, SOUTH, EAST, NORTH), levels=["GCOV"],
                           t0="2026-09-01T00:00:00Z", t1="2026-10-30T00:00:00Z",
                           product_names={"GCOV": "GCOV"}, max_results=250, fresh=False,
                           retriever=FakeRetriever(specs))
    ung = next(g for g in out["groups"] if UNKNOWN_ID in g["granule_ids"])
    assert ung["grouped"] is False and ung["n_variants"] == 1
    assert any("not grouped" in f for f in ung["flags"])
    assert ung["ref_time"] is None and ung["start"] is None    # nothing invented


def test_groups_truncated_flagged_may_be_incomplete(client):
    from engine.api.granules_bbox import search_bbox_sync
    out = search_bbox_sync(bbox=(WEST, SOUTH, EAST, NORTH), levels=["GCOV"],
                           t0="2026-09-01T00:00:00Z", t1="2026-10-30T00:00:00Z",
                           product_names={"GCOV": "GCOV"}, max_results=1, fresh=False,
                           retriever=FakeRetriever(default_specs()))
    assert out["truncated"] is True
    assert all(any("may be incomplete" in f for f in g["flags"])
               for g in out["groups"] if g["grouped"])


# --------------------------------------------------------------------------- §4.5 preview / probe
def test_preview_happy_path_png_and_meta(client):
    c, _tmp, _b = client
    layer = "science__LSAR__GCOV__grids__frequencyA__HHHH"
    url = (f"/api/background/preview?id={GCOV_A}&layer={layer}"
           f"&west={WEST}&south={SOUTH}&east={EAST}&north={NORTH}&max_px=64")
    r = c.get(url, headers=_auth())
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"
    import json
    meta = json.loads(r.headers["X-Img-Meta"])
    assert meta["id"] == GCOV_A and meta["layer"] == layer
    assert meta["stats"]["shape"] == meta["stats"]["shape"]
    assert meta["stats"]["min"] is not None and meta["stats"]["max"] is not None
    assert meta["cache_hit"] is False
    # cache hit on the second identical request
    r2 = c.get(url, headers=_auth())
    meta2 = json.loads(r2.headers["X-Img-Meta"])
    assert meta2["cache_hit"] is True


def test_preview_json_sibling(client):
    c, _tmp, _b = client
    layer = "science__LSAR__GCOV__grids__frequencyA__HHHH"
    r = c.get(f"/api/background/preview?id={GCOV_A}&layer={layer}&west={WEST}&south={SOUTH}"
              f"&east={EAST}&north={NORTH}&max_px=32&format=json", headers=_auth())
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and "png" not in body
    assert body["meta"]["stats"]["shape"]


class NanSource(FakeSource):
    """Serves HHHH as all-NaN (the GCOV fixture builder has no fills hook for grids)."""

    def fetch(self, bbox, t0, t1, product, layers, granules):
        res = super().fetch(bbox, t0, t1, product, layers, granules)
        for clip in res.clips:
            for g in clip.grids.values():
                for lk in list(g.layers):
                    if lk.endswith("__HHHH"):
                        g.layers[lk] = np.full(np.asarray(g.layers[lk]).shape, np.nan)
        return res


def test_preview_all_nan_is_a_clear_answer_not_a_blank_image(client):
    from engine.api import background as bg
    with pytest.raises(bg.BackgroundError) as ei:
        bg.preview(NanSource(default_specs()), gid=GCOV_A,
                   layer="science__LSAR__GCOV__grids__frequencyA__HHHH",
                   bbox=(WEST, SOUTH, EAST, NORTH), max_px=64, cmap="viridis", use_cache=False)
    assert ei.value.status == 422 and "no valid pixels" in str(ei.value)


def test_preview_max_px_capped(client):
    from engine.api import background as bg
    out = bg.preview(FakeSource(default_specs()), gid=GCOV_A,
                     layer="science__LSAR__GCOV__grids__frequencyA__HHHH",
                     bbox=(WEST, SOUTH, EAST, NORTH), max_px=100000, cmap="viridis",
                     use_cache=False)
    assert out["meta"]["shown_shape"] and out["meta"]["max_px_cap"] == 512
    assert max(out["meta"]["shown_shape"]) <= 512


def test_preview_antimeridian_box_is_400(client):
    c, _tmp, _b = client
    r = c.get(f"/api/background/preview?id={GCOV_A}&layer=x&west=179.9&south=-1&east=-179.9&north=1",
              headers=_auth())
    assert r.status_code == 400 and "antimeridian" in r.json()["detail"]["error"]


def test_preview_single_concurrent_409(client):
    from engine.api import background as bg
    import threading
    acquired = bg._PREVIEW_LOCK.acquire(blocking=False)
    assert acquired
    try:
        with pytest.raises(bg.BackgroundError) as ei:
            bg.preview(FakeSource(default_specs()), gid=GCOV_A,
                       layer="science__LSAR__GCOV__grids__frequencyA__HHHH",
                       bbox=(WEST, SOUTH, EAST, NORTH), max_px=32, cmap="viridis", use_cache=False)
        assert ei.value.status == 409 and "single concurrent preview" in str(ei.value)
    finally:
        bg._PREVIEW_LOCK.release()


def test_probe_value_at_a_point(client):
    c, _tmp, _b = client
    layer = "science__LSAR__GCOV__grids__frequencyA__HHHH"
    r = c.get(f"/api/background/probe?id={GCOV_A}&layer={layer}&lon=-118.5&lat=34.0",
              headers=_auth())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["finite"] is True and body["value"] is not None
    assert -119 <= body["lon"] <= -117 and 33.5 <= body["lat"] <= 35


# --------------------------------------------------------------------------- §4.5 stack-pair
def test_stack_pair_finds_gcov_at_both_ends(client):
    c, _tmp, _b = client
    r = c.get(f"/api/background/stack-pair?id={TEST_VECTOR}", headers=_auth())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["ref"]["epoch"] == "2026-09-20T13:08:23Z"
    assert body["sec"]["epoch"] == "2026-10-14T13:08:23Z"
    ref_ids = [g["id"] for g in body["ref"]["granules"]]
    assert GCOV_A in ref_ids and GCOV_B in ref_ids       # both variants, none hidden
    assert body["sec"]["granules"] == []                 # honest: nothing at that date in the fixture


def test_stack_pair_non_pair_product_is_422(client):
    c, _tmp, _b = client
    r = c.get(f"/api/background/stack-pair?id={GCOV_A}", headers=_auth())
    assert r.status_code == 422 and "pair" in r.json()["detail"]["error"]


# --------------------------------------------------------------------------- §5 preprocessing
def test_preprocessing_bundle_records(client):
    c, tmp, bundle = client
    r = c.get(f"/api/background/runs/x/preprocessing?bundle_id={bundle.bundle_id}", headers=_auth())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["trace_arrays"] is False
    steps = body["trace"]["steps"]
    assert [s["code"] for s in steps][:3] == ["P01", "P02", "P03"]
    assert body["trace"]["handoff"]["rows"]
    assert body["trace"]["bundle_summary"]["bundle_id"] == bundle.bundle_id
    assert "without a live trace" in body["trace"]["source"]


def test_preprocessing_unknown_run_is_404(client):
    c, _tmp, _b = client
    r = c.get("/api/background/runs/run-nope/preprocessing", headers=_auth())
    assert r.status_code == 404 and "unknown run" in r.json()["detail"]["error"]


def test_preprocessing_run_without_trace_honest_empty(client):
    c, _tmp, _b = client
    from engine.api.state import get_runner
    runner = get_runner()
    runner._states["run-no-trace"] = {"status": "ok", "progress": {}, "error": None,
                                      "results": [], "kind": "scan"}
    r = c.get("/api/background/runs/run-no-trace/preprocessing", headers=_auth())
    assert r.status_code == 200
    body = r.json()
    assert body["trace"] is None and "no recorded preprocessing trace" in body["empty_reason"]


def test_trace_attach_and_array_routes(client):
    c, _tmp, bundle = client
    from engine.api.state import get_runner
    from engine.api import background as bg
    from engine.common.debug.trace import Tracer
    from tests.common_fixtures import FakeRetriever as FR, make_scene as ms
    tile = default_tile()
    tracer = Tracer()
    b2 = build_scene_bundle(tile, FR(ms(tile, n_epochs=3, pairs=((0, 1),))), as_of=AS_OF,
                            observer=tracer)
    runner = get_runner()
    runner._states["run-traced"] = {"status": "ok", "progress": {}, "error": None,
                                    "results": [], "kind": "scan"}
    payload = bg.build_trace_payload(tracer, b2)
    runner.attach_trace("run-traced", payload)
    trace = runner.get_trace("run-traced")
    assert trace is not None and len(trace["steps"]) >= 10
    assert trace["n_arrays"] > 0
    aid = sorted(trace["arrays"])[0]
    r = c.get(f"/api/background/runs/run-traced/arrays/{aid}", headers=_auth())
    assert r.status_code == 200 and r.content[:8] == b"\x89PNG\r\n\x1a\n"
    r = c.get(f"/api/background/runs/run-traced/arrays/{aid}/probe?fx=0.5&fy=0.5", headers=_auth())
    assert r.status_code == 200
    assert "value" in r.json() and "row" in r.json()


# --------------------------------------------------------------------------- §6 pipelines
def _seed_run(client, run_id="run-pipe"):
    from engine.api.state import get_runner
    runner = get_runner()
    runner._states[run_id] = {
        "status": "ok", "progress": {"stage": "done"}, "error": None, "kind": "scan",
        "tile": "11SLT66", "bundle_id": None,
        "results": [{
            "pipeline": "earthquake_coseismic", "tile": "11SLT66", "epoch": AS_OF,
            "status": "NOT_SUPPORTED", "summary": "screen correlation below threshold",
            "raw_p": 0.4, "confidence": None, "runtime_s": 1.2, "warnings": [],
            "evidence": [], "layers": [], "data_used": [], "references_used": [],
            "diagnostics": {"screen": {"correlation": -0.36}}, "versions": {},
            "min_epochs_met": True, "bundle_id": None,
            "detail": {"layers": {}},
        }],
    }
    return runner


def test_pipeline_detail_shape(client):
    c, _tmp, _b = client
    _seed_run(client)
    r = c.get("/api/background/runs/run-pipe/pipelines/earthquake_coseismic", headers=_auth())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["manifest"]["name"] == "earthquake_coseismic"
    assert body["manifest"]["uses_codes"]
    assert body["result"]["status"] == "NOT_SUPPORTED"
    assert body["process"]["runtime_s"] == 1.2
    assert body["result_missing"] is None


def test_pipeline_detail_404s_carry_engine_text(client):
    c, _tmp, _b = client
    _seed_run(client)
    r = c.get("/api/background/runs/run-nope/pipelines/earthquake_coseismic", headers=_auth())
    assert r.status_code == 404 and "unknown run" in r.json()["detail"]["error"]
    r = c.get("/api/background/runs/run-pipe/pipelines/not_a_pipeline", headers=_auth())
    assert r.status_code == 404 and "unknown pipeline" in r.json()["detail"]["error"]
    r = c.get("/api/background/runs/run-pipe/pipelines/tsunami_inundation", headers=_auth())
    assert r.status_code == 404 and "no result in run" in r.json()["detail"]["error"]


# =========================================================================== post-audit fixes
# Problem 1 — local-data routes: no Earthdata gate, not on the scan pool
# --------------------------------------------------------------------------------------------
def _no_earthdata(monkeypatch):
    monkeypatch.setattr("engine.api.jobs.earthdata_present", lambda: False)
    monkeypatch.setattr("engine.api.routes_background.earthdata_present", lambda: False)


def _attach_trace(run_id: str):
    """A real chain trace bound to a run (same recipe as test_trace_attach_and_array_routes)."""
    from engine.api.state import get_runner
    from engine.api import background as bg
    from engine.common.debug.trace import Tracer
    from tests.common_fixtures import FakeRetriever as FR, make_scene as ms
    tile = default_tile()
    tracer = Tracer()
    b2 = build_scene_bundle(tile, FR(ms(tile, n_epochs=3, pairs=((0, 1),))), as_of=AS_OF, observer=tracer)
    runner = get_runner()
    runner._states[run_id] = {"status": "ok", "progress": {}, "error": None, "results": [],
                              "kind": "scan"}
    runner.attach_trace(run_id, bg.build_trace_payload(tracer, b2))
    return sorted(runner.get_trace(run_id)["arrays"])[0]


def _local_urls(bundle_id: str, aid: str = "t1"):
    return [
        f"/api/background/runs/run-pipe/preprocessing",
        f"/api/background/runs/x/preprocessing?bundle_id={bundle_id}",
        f"/api/background/runs/run-traced/arrays/{aid}",
        f"/api/background/runs/run-traced/arrays/{aid}/probe?fx=0.5&fy=0.5",
        f"/api/background/runs/run-pipe/pipelines/earthquake_coseismic",
    ]


def test_local_routes_work_without_earthdata_credentials(client, monkeypatch):
    c, _tmp, bundle = client
    _seed_run(client)
    aid = _attach_trace("run-traced")
    _no_earthdata(monkeypatch)
    for url in _local_urls(bundle.bundle_id, aid):
        r = c.get(url, headers=_auth())
        assert r.status_code == 200, (url, r.status_code, r.text[:200])
    # unknown runs answer their normal 404, never the credentials 503
    for url in ("/api/background/runs/run-nope/preprocessing",
                "/api/background/runs/run-nope/arrays/t1",
                "/api/background/runs/run-nope/arrays/t1/probe?fx=0.5&fy=0.5",
                "/api/background/runs/run-nope/pipelines/earthquake_coseismic"):
        r = c.get(url, headers=_auth())
        assert r.status_code == 404, (url, r.status_code, r.text[:200])


def test_provider_routes_still_503_without_earthdata(client, monkeypatch):
    from engine.api.jobs import NO_EARTHDATA_MESSAGE
    c, _tmp, _b = client
    _no_earthdata(monkeypatch)
    box = f"west={WEST}&south={SOUTH}&east={EAST}&north={NORTH}"
    for url in (f"/api/background/granule?id={TEST_VECTOR}",
                f"/api/background/preview?id={GCOV_A}&layer=x&{box}",
                f"/api/background/probe?id={GCOV_A}&layer=x&lon=-118.2&lat=34.0",
                f"/api/background/stack-pair?id={TEST_VECTOR}"):
        r = c.get(url, headers=_auth())
        assert r.status_code == 503, (url, r.status_code)
        assert r.json()["detail"] == {"ok": False, "error": NO_EARTHDATA_MESSAGE}


def test_local_route_not_queued_behind_a_busy_scan_pool(client):
    import threading
    import time
    from engine.api.state import get_runner
    c, _tmp, _b = client
    runner = get_runner()
    _seed_run(client)
    release = threading.Event()
    started = threading.Event()

    def _block():
        started.set()
        release.wait(30)

    runner._pool.submit(_block)               # the single worker is now busy, like a running scan
    try:
        assert started.wait(5)
        t0 = time.monotonic()
        r = c.get("/api/background/runs/run-pipe/preprocessing", headers=_auth())
        assert r.status_code == 200 and r.json()["ok"] is True
        r2 = c.get("/api/background/runs/run-pipe/pipelines/earthquake_coseismic", headers=_auth())
        assert r2.status_code == 200
        assert time.monotonic() - t0 < 2.0, "local routes must not wait for the shared pool"
    finally:
        release.set()


def test_local_routes_keep_the_gate(client, monkeypatch):
    c, _tmp, bundle = client
    _seed_run(client)
    urls = _local_urls(bundle.bundle_id)
    for url in urls:
        assert c.get(url, headers=_auth("wrong")).status_code == 401, url
        assert c.get(url).status_code == 401, url
    monkeypatch.delenv("NISAR_BACKGROUND_TOKEN")
    for url in urls:
        r = c.get(url, headers=_auth())
        assert r.status_code == 503, url
        assert "NISAR_BACKGROUND_TOKEN" in r.json()["detail"]["error"], url


def test_local_routes_share_the_background_rate_limit(client, monkeypatch):
    from engine.api import routes_background
    monkeypatch.setattr(routes_background, "_BG_RATE", {"max": 1, "window_s": 60.0})
    c, _tmp, _b = client
    assert c.get("/api/background/runs/run-nope/preprocessing", headers=_auth()).status_code == 404
    r = c.get("/api/background/runs/run-nope/preprocessing", headers=_auth())
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1


# Problem 4 — bundle_id is validated before it becomes a path
# --------------------------------------------------------------------------------------------
@pytest.mark.parametrize("raw_query_value", [
    "..%2Fx",            # ../x (URL-encoded slash)
    "%2E%2E%2Fx",        # fully encoded ../x
    "../x",
    "%2Fetc",            # absolute path /etc
    "a%2Fb",             # a/b
    "a%5Cb",             # a\b
    "a" * 129,           # one over the 128-char limit
    "..",
    "a..b",              # ".." anywhere is refused
    ".hidden",           # must start with a letter or digit
])
def test_bundle_id_unsafe_values_are_400(client, raw_query_value):
    c, tmp, _b = client
    r = c.get(f"/api/background/runs/x/preprocessing?bundle_id={raw_query_value}", headers=_auth())
    assert r.status_code == 400, (raw_query_value, r.status_code, r.text[:200])
    detail = r.json()["detail"]
    assert detail["ok"] is False and "bundle_id" in detail["error"]
    assert str(tmp) not in detail["error"]


def test_bundle_id_symlink_escaping_the_store_is_400(client, tmp_path):
    import os
    import shutil
    c, tmp, bundle = client
    bundles = Path(tmp) / "bundles"
    outside = tmp_path / "outside-bundle"
    shutil.copytree(bundles / bundle.bundle_id, outside)          # a perfectly loadable bundle...
    link = bundles / "escape-link"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available on this platform")
    try:
        r = c.get("/api/background/runs/x/preprocessing?bundle_id=escape-link", headers=_auth())
        assert r.status_code == 400, r.text[:200]                  # ...must still be refused
        assert str(tmp_path) not in r.json()["detail"]["error"]
    finally:
        link.unlink()


def test_bundle_id_valid_cached_bundle_still_loads(client):
    c, _tmp, bundle = client
    r = c.get(f"/api/background/runs/x/preprocessing?bundle_id={bundle.bundle_id}", headers=_auth())
    assert r.status_code == 200 and r.json()["run"]["bundle_id"] == bundle.bundle_id


def test_bundle_id_missing_bundle_404_names_id_and_class_but_no_paths(client):
    c, tmp, _b = client
    r = c.get("/api/background/runs/x/preprocessing?bundle_id=no-such-bundle", headers=_auth())
    assert r.status_code == 404
    err = r.json()["detail"]["error"]
    assert "no-such-bundle" in err and "Error" in err           # id + exception class
    assert str(tmp) not in err and str(Path(tmp).resolve()) not in err


def test_pipeline_bundle_error_scrubs_the_bundles_prefix(client):
    from engine.api.state import get_runner
    c, tmp, _b = client
    runner = _seed_run(client, run_id="run-missing-bundle")
    runner._states["run-missing-bundle"]["bundle_id"] = "gone-bundle"
    r = c.get("/api/background/runs/run-missing-bundle/pipelines/earthquake_coseismic", headers=_auth())
    assert r.status_code == 200
    err = r.json()["bundle_error"]
    assert err and str(tmp) not in err and str(Path(tmp).resolve()) not in err


# Problem 5 — constant-time token parse + thread-safe rate limiter
# --------------------------------------------------------------------------------------------
@pytest.mark.parametrize("header", [
    "Bearer",                     # no token
    "Bearer ",                    # empty token
    "bearer " + TOKEN,            # scheme is case-sensitive
    "BEARER " + TOKEN,
    "Bearer  " + TOKEN,           # two spaces
    "Bearer " + TOKEN + " ",      # trailing space is part of the presented token
    " Bearer " + TOKEN,
    "Token " + TOKEN,
    "",
])
def test_gate_malformed_authorization_headers_are_401(client, header):
    c, _tmp, _b = client
    r = c.get("/api/background/runs/run-nope/preprocessing", headers={"Authorization": header})
    assert r.status_code == 401, (header, r.status_code)
    assert r.json()["detail"]["ok"] is False


def test_gate_correct_header_passes_and_uses_compare_digest(client, monkeypatch):
    import hmac as _hmac
    from engine.api import routes_background
    seen = []
    real = _hmac.compare_digest

    def spy(a, b):
        seen.append((type(a), type(b)))
        return real(a, b)

    monkeypatch.setattr(routes_background.hmac, "compare_digest", spy)
    c, _tmp, _b = client
    r = c.get("/api/background/runs/run-nope/preprocessing", headers=_auth())
    assert r.status_code == 404                       # past the gate
    assert seen == [(bytes, bytes)]


def test_rate_limiter_is_atomic_under_concurrency():
    import threading
    from engine.api.routes_background import _rate_limited
    hits: list[float] = []
    rate = {"max": 12, "window_s": 60.0}
    n = 40
    barrier = threading.Barrier(n)
    results: list = []

    def worker():
        barrier.wait()
        results.append(_rate_limited(hits, rate))

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert len(results) == n
    assert sum(r is None for r in results) == 12
    assert len(hits) == 12
    assert all(isinstance(r, int) and r >= 1 for r in results if r is not None)
