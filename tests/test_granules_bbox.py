"""E8 — bbox /api/granules tests (the Granule Explorer's engine contract). Offline: the
retriever is injected (FakeRetriever through the JobRunner's factory seam), so pytest never
touches the network. The TASK's granule test vector is reproduced field by field; the one
token that could not be verified (the orbit letter) is asserted to stay RAW, never guessed.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.common_fixtures import (  # noqa: E402
    FakeRetriever, GranuleSpec, default_tile, make_scene, ref_for, write_ancillary)
from engine.common.chain import build_scene_bundle  # noqa: E402
from engine.common import DirectoryAncillary  # noqa: E402
from engine.common.bundle_io import save_bundle  # noqa: E402

AS_OF = "2026-08-20T00:00:00Z"

# the TASK's test vector (a real granule from the owner's screenshot)
TEST_VECTOR = ("NISAR_L2_PR_GUNW_031_034_A_019_033_4000_SH_20260920T130823_20260920T130857_"
               "20261014T130823_20261014T130857_P05023_N_F_J_001")

WEST, SOUTH, EAST, NORTH = -119.0, 33.5, -117.0, 35.0


@pytest.fixture(autouse=True)
def _clean_bbox_rate_window():
    """The bbox rate limiter is module-level; every test starts with an empty window."""
    from engine.api import routes_scanner
    routes_scanner._BBOX_HITS.clear()
    yield
    routes_scanner._BBOX_HITS.clear()


# the API client fixtures (same shape as tests/test_api.py — the engine data dir + TestClient)
@pytest.fixture(scope="module")
def data_dir(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("granules_bbox")
    tile = default_tile()
    specs = make_scene(tile, n_epochs=4, pairs=((0, 1), (1, 2), (2, 3)), dlos=0.05)
    anc_root = write_ancillary(tmp / "ancillary", tile)
    bundle = build_scene_bundle(tile, FakeRetriever(specs), as_of=AS_OF,
                                ancillary=DirectoryAncillary(anc_root))
    save_bundle(bundle, tmp / "bundles")
    return tmp


@pytest.fixture()
def client(data_dir, monkeypatch):
    from fastapi.testclient import TestClient
    from engine.api import state as api_state
    from engine.api.main import create_app
    import engine.common.config as engine_config
    import engine.registry as engine_registry
    import engine.api.state as _state
    for mod in (engine_config, engine_registry, _state):
        monkeypatch.setattr(mod, "data_root", lambda: Path(data_dir))
    api_state.get_runner.cache_clear()
    app = create_app()
    with TestClient(app) as c:
        yield c, data_dir
    api_state.get_runner.cache_clear()


def _spec(gid: str, product: str, start: str, track=5, direction="ASCENDING", frame=219,
          extra: dict | None = None) -> GranuleSpec:
    from datetime import datetime, timezone
    d = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    ref = ref_for(gid, product, d, track=track, direction=direction, frame=frame)
    if extra:
        object.__setattr__(ref, "extra", extra)
    return GranuleSpec(ref=ref, kind=product)


@pytest.fixture()
def bbox_client(client, monkeypatch):
    """The API client with a bbox-capable FakeRetriever injected as the retriever factory."""
    c, data_dir = client
    retriever = FakeRetriever([
        # the TASK vector, exactly
        _spec(TEST_VECTOR, "GUNW", "2026-09-20T13:08:23", track=34, direction="ASCENDING", frame=19,
              extra={"collectionName": "NISAR_L2_GUNW_PROVISIONAL_V1", "orbitType": "MOE"}),
        # a second GUNW pair 12 days later on the same track -> a 12 d gap between pairs
        _spec("NISAR_L2_PR_GUNW_033_034_A_019_035_4000_SH_20261002T130823_20261002T130857_"
              "20261026T130823_20261026T130857_P05023_N_F_J_001",
              "GUNW", "2026-10-02T13:08:23", track=34, direction="ASCENDING", frame=19),
        # a GCOV epoch on the same track (bandwidth 4005 stays RAW — multi-frequency)
        _spec("NISAR_L2_PR_GCOV_031_034_A_019_4005_SHNA_A_20260920T130900_20260920T130930_"
              "P05023_N_F_J_001", "GCOV", "2026-09-20T13:09:00", track=34, direction="ASCENDING", frame=19),
        # a descending stack
        _spec("NISAR_L2_PR_GCOV_031_071_D_045_7700_SH_A_20260921T011000_20260921T011030_"
              "P05023_N_F_J_001", "GCOV", "2026-09-21T01:10:00", track=71, direction="DESCENDING", frame=45),
    ])
    from engine.api import state as api_state
    monkeypatch.setattr(
        type(api_state.get_runner()), "granule_search_bbox",
        lambda self, **kw: _search_with(retriever, **kw), raising=True)
    yield c, retriever


def _search_with(retriever, *, bbox, levels, t0, t1, product_names, max_results, fresh, **_):
    from engine.api.granules_bbox import search_bbox_sync
    return search_bbox_sync(bbox=bbox, levels=levels, t0=t0, t1=t1,
                            product_names=product_names, max_results=max_results,
                            fresh=fresh, retriever=retriever)


def test_bbox_granules_shape_and_counts(bbox_client):
    c, _ = bbox_client
    r = c.get(f"/api/granules?west={WEST}&south={SOUTH}&east={EAST}&north={NORTH}&level=GUNW,GCOV")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    for key in ("bbox", "window", "n_total", "truncated", "degraded", "search_budget",
                "generated_at", "granules", "stacks"):
        assert key in body, key
    assert body["bbox"] == [WEST, SOUTH, EAST, NORTH]
    assert body["n_total"] == 4
    assert body["truncated"] is False and body["degraded"] is False
    assert body["search_budget"] == 250
    # stacks: A034 first (ascending), then D071 — the P01 stack-key convention
    assert [s["key"] for s in body["stacks"]] == ["A034", "D071"]
    a034 = body["stacks"][0]
    assert a034["n_granules"] == 3
    assert a034["direction"] == "A" and a034["track"] == 34
    # epochs: 09-20 (GUNW ref + GCOV merge into ONE epoch), 10-02 (ref2), 10-14 (sec1), 10-26 (sec2)
    assert a034["n_epochs"] == 4
    assert a034["gaps_days"] == [12.0, 12.0, 12.0]
    assert a034["min_gap_days"] == 12.0 and a034["median_gap_days"] == 12.0 and a034["max_gap_days"] == 12.0
    assert a034["first"] == "2026-09-20T13:08:23Z" and a034["last"] == "2026-10-26T13:08:23Z"
    # the engine supplies NO nominal repeat (no verified source) — null, never invented
    assert a034["nominal_repeat_days"] is None
    d071 = body["stacks"][1]
    assert d071["n_granules"] == 1 and d071["gaps_days"] == []


def test_bbox_granules_the_task_test_vector(bbox_client):
    """The owner's screenshot granule must reproduce field by field (verified tokens only)."""
    c, _ = bbox_client
    r = c.get(f"/api/granules?west={WEST}&south={SOUTH}&east={EAST}&north={NORTH}&level=GUNW")
    g = next(x for x in r.json()["granules"] if x["id"] == TEST_VECTOR)
    assert g["product"] == "GUNW"
    assert g["direction"] == "A" and g["track"] == 34 and g["frame"] == 19
    assert g["ref_time"] == "2026-09-20T13:08:23Z"
    assert g["sec_time"] == "2026-10-14T13:08:23Z"
    assert g["baseline_days"] == 24.0
    assert g["cycle_ref"] == 31 and g["cycle_sec"] == 33
    assert g["polarization"] == "HH"
    assert g["range_bandwidth_mhz"] == 40.0
    assert g["crid"] == "P05023"
    assert g["collection"] == "NISAR_L2_GUNW_PROVISIONAL_V1"     # provider-side, verbatim
    assert g["orbit_accuracy"] == {"asf": "MOE", "filename": "N"}
    # the orbit letter has NO verified public mapping -> RAW + flagged, never "near-real-time"
    assert any("orbit token" in f for f in g["flags"])
    assert g["version"] == "001"
    assert g["stack"] == "A034"


def test_bbox_granules_multifrequency_bandwidth_stays_raw(bbox_client):
    c, _ = bbox_client
    r = c.get(f"/api/granules?west={WEST}&south={SOUTH}&east={EAST}&north={NORTH}&level=GCOV")
    g = next(x for x in r.json()["granules"] if x["id"].startswith("NISAR_L2_PR_GCOV_031"))
    assert g["range_bandwidth_mhz"] is None                       # 4005 = "40+5": kept raw
    assert any("4005" in f for f in g["flags"])
    assert g["polarization"] == "HH"                              # SHNA -> HH (CMR-verified)


def test_bbox_antimeridian_box_is_400(bbox_client):
    c, _ = bbox_client
    r = c.get("/api/granules?west=179.9&south=-1&east=-179.9&north=1")
    assert r.status_code == 400
    assert "antimeridian" in r.json()["detail"]["error"]


def test_bbox_bad_south_north_is_400(bbox_client):
    c, _ = bbox_client
    r = c.get("/api/granules?west=0&south=10&east=1&north=10")
    assert r.status_code == 400
    assert "south" in r.json()["detail"]["error"]
    r = c.get("/api/granules?west=0&south=-95&east=1&north=10")
    assert r.status_code == 400 and "south" in r.json()["detail"]["error"]


def test_bbox_unknown_level_is_400(bbox_client):
    c, _ = bbox_client
    r = c.get(f"/api/granules?west={WEST}&south={SOUTH}&east={EAST}&north={NORTH}&level=SLC")
    assert r.status_code == 400 and "level" in r.json()["detail"]["error"]


def test_bbox_rate_limit_429_with_retry_after(bbox_client, monkeypatch):
    from engine.api import routes_scanner
    monkeypatch.setattr(routes_scanner, "_BBOX_RATE", {"max": 2, "window_s": 60.0})
    monkeypatch.setattr(routes_scanner, "_BBOX_HITS", [])   # a clean window for this test
    c, _ = bbox_client
    url = f"/api/granules?west={WEST}&south={SOUTH}&east={EAST}&north={NORTH}&level=GCOV"
    assert c.get(url).status_code == 200
    assert c.get(url).status_code == 200
    r = c.get(url)
    assert r.status_code == 429
    assert "Retry-After" in r.headers and int(r.headers["Retry-After"]) >= 1
    assert r.json()["detail"]["ok"] is False and "rate limit" in r.json()["detail"]["error"]


def test_bbox_degraded_search_is_surfaced(bbox_client, monkeypatch):
    c, retriever = bbox_client
    retriever.degraded = True
    r = c.get(f"/api/granules?west={WEST}&south={SOUTH}&east={EAST}&north={NORTH}&level=GCOV")
    assert r.status_code == 200
    assert r.json()["degraded"] is True


def test_bbox_truncation_is_flagged_not_silent(bbox_client):
    c, _ = bbox_client
    r = c.get(f"/api/granules?west={WEST}&south={SOUTH}&east={EAST}&north={NORTH}&level=GCOV&max=1")
    assert r.status_code == 200
    body = r.json()
    assert body["search_budget"] == 1
    assert body["truncated"] is True
    assert body["n_total"] >= 1


def test_bbox_needs_credentials(client, monkeypatch):
    from engine.api import jobs as api_jobs
    c, _ = client
    monkeypatch.setattr(api_jobs, "earthdata_present", lambda: False)
    r = c.get(f"/api/granules?west={WEST}&south={SOUTH}&east={EAST}&north={NORTH}")
    assert r.status_code == 503
    assert "Earthdata" in r.json()["detail"]["error"]


def test_legacy_tile_mode_unchanged(client, monkeypatch):
    """The old per-tile Data tab contract keeps working alongside the bbox mode."""
    c, _ = client
    # no credentials -> the same 503 the legacy tests pin
    from engine.api import jobs as api_jobs
    monkeypatch.setattr(api_jobs, "earthdata_present", lambda: False)
    r = c.get("/api/granules?lon=-118.25&lat=34.05")
    assert r.status_code == 503
    # neither-mode is a 400 with the engine's text
    r = c.get("/api/granules")
    assert r.status_code == 400
    assert "lon+lat" in r.json()["detail"]["error"]


# --------------------------------------------------------------------------- #
# post-audit regressions (decoder polarization, per-level truncation)
# --------------------------------------------------------------------------- #
def _pair_id(pol: str) -> str:
    return TEST_VECTOR.replace("_SH_", f"_{pol}_")


@pytest.mark.parametrize("token,expected", [("SH", "HH"), ("SHNA", "HH"), ("DHDH", "HH"),
                                             ("HH", "HH"), ("HV", None), ("SV", None), ("QP", None)])
def test_decoder_polarization_only_decodes_the_verified_h_case(token, expected):
    """Only a 2nd character of H is verified (main band HH). HV/SV/... stay raw + flagged —
    a guessed 'VV' (the old behaviour) is unverified and wrong for a cross-pol main band."""
    from engine.common.granule_id import decode_granule_id
    d = decode_granule_id(_pair_id(token))
    assert d["polarization"] == expected
    assert (f"{token} (polarization)" in d["undecoded"]) is (expected is None)


class _CountingRetriever:
    """search() returns n refs per level (the budget is applied PER search, like the real client)."""
    def __init__(self, n_by_product):
        self.n_by_product = n_by_product

    def search(self, bbox, t0, t1, product, fresh=False, max_results=None):
        from engine.retrieval.clients.types import GranuleRef
        n = self.n_by_product[product]
        off = 0 if product == "GUNW" else 100          # distinct ids per level, so de-duplication can't hide the union
        refs = [GranuleRef(f"NISAR_L2_PR_GCOV_{i + off:03d}_004_A_018_4000_SH_A_20260101T000000_"
                           f"20260101T000030_P05023_N_P_J_001", "http://x", product, "2026-01-01T00:00:00Z",
                           "2026-01-01T00:00:30Z", 4, 18, "ASCENDING") for i in range(1, n + 1)]
        return refs[:max_results], False

    def close(self):
        pass


def test_truncated_is_per_search_not_the_union_of_levels():
    from engine.api.granules_bbox import search_bbox_sync
    names = {"GUNW": "GUNW", "GCOV": "GCOV"}
    kw = dict(bbox=(WEST, SOUTH, EAST, NORTH), t0="2026-01-01T00:00:00Z", t1="2026-02-01T00:00:00Z",
              product_names=names, fresh=False)
    # two levels, 6 + 7 distinct granules, budget 10: each search is under budget -> NOT truncated,
    # although the union (13) exceeds it (the old union-based rule reported True here)
    out = search_bbox_sync(levels=["GUNW", "GCOV"], max_results=10,
                           retriever=_CountingRetriever({"GUNW": 6, "GCOV": 7}), **kw)
    assert out["truncated"] is False and out["n_total"] == 13
    # one level that exactly hits the budget -> truncated
    out = search_bbox_sync(levels=["GUNW", "GCOV"], max_results=7,
                           retriever=_CountingRetriever({"GUNW": 3, "GCOV": 9}), **kw)
    assert out["truncated"] is True
