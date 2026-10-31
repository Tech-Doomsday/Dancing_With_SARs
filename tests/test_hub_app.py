"""colab/hub.py end to end, offline: the real key gate + the real hub routes + the real BatchRunner and
RetrievalManager over the fixture scene (a fake retriever that writes real zarr clips). Only the network is faked.

plan -> retrieve -> "already stored" in the next cover -> the stored layers come back as PNGs through the token-gated
routes: exactly the path the page walks."""
from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

import pytest

from tests.test_retrieval_stages import _factory                                   # noqa: F401  (fake scene + store)

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("nisar_hub_app_under_test", ROOT / "colab" / "hub.py")
hub = importlib.util.module_from_spec(_spec)
sys.modules["nisar_hub_app_under_test"] = hub
_spec.loader.exec_module(hub)

KEY = "hub-test-key-0123456789abcdef"
LON, LAT = -118.5, 34.0                                                            # tests.common_fixtures.default_tile()


@pytest.fixture()
def hubc(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    import engine.api.jobs as api_jobs
    import engine.api.state as api_state
    import engine.common.config as engine_config
    import engine.registry as engine_registry

    monkeypatch.setenv("NISAR_ENABLE_SCANS", "1")
    monkeypatch.setenv("EARTHDATA_TOKEN", "test-token-present")
    monkeypatch.setenv("NISAR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("NISAR_BACKGROUND_TOKEN", KEY)                              # what hub.main() does
    for mod in (engine_config, engine_registry, api_state):
        monkeypatch.setattr(mod, "data_root", lambda: Path(tmp_path))
    api_state.get_runner.cache_clear()
    held: list = []
    monkeypatch.setattr(api_jobs, "_default_retriever_factory", lambda: _factory(tmp_path, held))
    monkeypatch.setattr(hub, "PAGE", ROOT / "colab" / "hub_page.html")
    app = hub.KeyGate(hub.build_app(KEY), KEY)
    with TestClient(app) as c:
        yield c
    api_state.get_runner.cache_clear()


def _auth():
    return {"Authorization": f"Bearer {KEY}"}


def _run(c, mode, **body):
    r = c.post("/hub/api/batch", json={"lon": LON, "lat": LAT, "snap": True, "mode": mode, "products": ["GCOV"],
                                       "lookback_days": 120, **body}, headers=_auth())
    assert r.status_code == 202, r.text
    end = time.time() + 30
    while time.time() < end:
        s = c.get("/hub/api/batch", headers=_auth()).json()
        if s["status"] != "running":
            return s
        time.sleep(0.05)
    raise AssertionError("batch did not finish")


def test_the_gate_guards_everything_and_the_link_opens_the_page(hubc):
    c = hubc
    for path in ("/", "/hub/api/disk", "/api/health", "/api/retrievals"):
        assert c.get(path).status_code == 401, path
    r = c.get(f"/?k={KEY}")                                                         # follows the 303, keeps the cookie
    assert r.status_code == 200 and "NISAR hub" in r.text
    assert f'const TOKEN = "{KEY}"' in r.text and "__HUB_TOKEN__" not in r.text     # the page can call the gated routes
    assert c.get("/api/health").status_code == 200                                  # the cookie now opens the rest
    assert c.get("/hub/api/disk").json()["ok"] is True


def test_cover_is_one_tile_when_snapped_and_capped_when_too_big(hubc):
    c = hubc
    c.get(f"/?k={KEY}")
    one = c.get("/hub/api/cover", params={"lon": LON, "lat": LAT, "snap": True}).json()
    assert [t["id"] for t in one["tiles"]] == ["11SLT66"] and one["cell_m"] == 10_000 and one["have"] == {}
    box = c.get("/hub/api/cover", params={"lon": LON, "lat": LAT, "size_km": 30}).json()
    assert 9 <= len(box["tiles"]) <= 16 and box["tiles"][0]["id"] == "11SLT66"      # nearest first
    big = c.get("/hub/api/cover", params={"lon": LON, "lat": LAT, "size_km": 200})
    assert big.status_code == 400 and "tiles" in big.json()["detail"]["error"]
    assert c.get("/hub/api/cover", params={"lon": 500, "lat": 0}).status_code == 422


def test_plan_then_retrieve_then_browse_the_stored_layers(hubc):
    c = hubc
    c.get(f"/?k={KEY}")

    plan = _run(c, "plan")
    assert plan["status"] == "finished" and [t["status"] for t in plan["tiles"]] == ["planned"]
    assert plan["tiles"][0]["totals"]["bytes_planned"] > 0 and plan["tiles"][0]["totals"]["stored"] == 0
    assert c.get("/hub/api/cover", params={"lon": LON, "lat": LAT, "snap": True}).json()["have"] == {}   # a plan stores nothing

    got = _run(c, "retrieve")
    t = got["tiles"][0]
    assert got["status"] == "finished" and t["status"] == "done" and t["totals"]["stored"] > 0
    have = c.get("/hub/api/cover", params={"lon": LON, "lat": LAT, "snap": True}).json()["have"]
    assert have["11SLT66"] == {"stored": t["totals"]["stored"], "retrieval": t["rid"]}

    rows = c.get("/api/retrievals", params={"limit": 10}).json()["retrievals"]
    assert [r["id"] for r in rows][0] == t["rid"] and rows[0]["created_at"] >= rows[1]["created_at"]    # newest first
    stored = rows[0]
    assert stored["tile"] == "11SLT66" and stored["bbox"] and stored["window"]["lookback_days"] == 120
    assert stored["request"]["products"] == ["GCOV"]

    d = c.get("/hub/api/disk").json()                                              # the disk panel sees clips + retrievals
    assert {"clips", "retrievals"} <= {p["name"] for p in d["parts"]} and d["used_bytes"] > 0

    rid = t["rid"]
    assert c.get(f"/api/retrievals/{rid}/clips").status_code == 401                # the cookie alone is not the layer token
    clips = c.get(f"/api/retrievals/{rid}/clips", headers=_auth()).json()["clips"]
    assert clips and {"granule_id", "grid", "layer", "shape", "dtype", "epsg", "start"} <= set(clips[0])
    one = next(x for x in clips if len(x["shape"]) >= 2)
    q = {"granule": one["granule_id"], "grid": one["grid"], "layer": one["layer"]}
    img = c.get(f"/api/retrievals/{rid}/clips/image", params={**q, "max_px": 64}, headers=_auth())
    assert img.status_code == 200 and img.content[:8] == b"\x89PNG\r\n\x1a\n"
    import json
    meta = json.loads(img.headers["x-img-meta"])
    assert {"lo", "hi", "full_shape", "shown_shape", "decimation_stride", "label"} <= set(meta)
    st = c.get(f"/api/retrievals/{rid}/clips/stats", params=q, headers=_auth()).json()
    assert st["ok"] and st["valid"] > 0 and len(st["hist"]["counts"]) == 32
    pr = c.get(f"/api/retrievals/{rid}/clips/probe", params={**q, "row": 0, "col": 0}, headers=_auth()).json()
    assert pr["ok"] and pr["epsg"] > 0
    z = c.get(f"/api/retrievals/{rid}/download.zip", headers=_auth())
    assert z.status_code == 200 and z.content[:2] == b"PK"


def test_a_bad_request_is_said_plainly(hubc):
    c = hubc
    c.get(f"/?k={KEY}")
    for body, status, word in (({"mode": "delete"}, 400, "mode"), ({"products": ["XYZ"]}, 400, "products")):
        r = c.post("/hub/api/batch", json={"lon": LON, "lat": LAT, "snap": True, **body})
        assert r.status_code == status and word in r.json()["detail"]["error"], r.text


def test_no_credentials_is_a_503_with_the_engines_own_words(hubc, monkeypatch):
    c = hubc
    c.get(f"/?k={KEY}")
    for k in ("EARTHDATA_TOKEN", "EARTHDATA_USERNAME", "EARTHDATA_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    r = c.post("/hub/api/batch", json={"lon": LON, "lat": LAT, "snap": True, "mode": "plan"})
    assert r.status_code == 503 and "Earthdata" in r.json()["detail"]["error"]
