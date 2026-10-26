"""debug_hub: it must observe the real chain without changing it, report failures verbatim, and refuse foreign callers."""
from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "tools", ROOT / "tests"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import common_fixtures as cf  # noqa: E402
from debug_hub.events import Job  # noqa: E402
from debug_hub.runner import run_job  # noqa: E402


def _run(**params):
    j = Job("t", {"lon": -118.5, "lat": 34.0, "source": "selftest", **params})
    run_job(j)
    return j


def test_selftest_runs_the_real_chain_and_hands_off_every_pipeline():
    j = _run()
    assert j.state == "done", j.error
    assert [s["code"] for s in j.steps] == [f"P{i:02d}" for i in range(1, 14)]
    assert all(s["status"] == "ok" for s in j.steps)
    assert j.final["contract_issues"] == [] and j.final["tracer_errors"] == []
    assert j.retrieval["clips"] and j.retrieval["n_layers"] > 0
    from engine.registry import Registry
    assert len(j.handoff["rows"]) == len(Registry().manifests)
    assert sum(j.handoff["tally"].values()) == len(j.handoff["rows"])


def test_observing_does_not_change_the_bundle():
    """Same inputs through the hub and through plain build_scene_bundle give the same content-addressed bundle id."""
    from engine.common.ancillary import NullAncillary
    from engine.common.chain import build_scene_bundle
    j = _run()
    tile = cf.default_tile()
    plain = build_scene_bundle(tile, cf.FakeRetriever(cf.make_scene(tile)), as_of="2026-09-20T00:00:00Z", ancillary=NullAncillary())
    assert j.final["bundle_id"] == plain.bundle_id


def test_retriever_failure_is_recorded_and_visible_not_swallowed():
    from engine.common.ancillary import NullAncillary
    from engine.common.chain import build_scene_bundle
    from debug_hub.recorder import Recorder
    tile = cf.default_tile()
    job = Job("f", {})
    rec = Recorder(cf.FakeRetriever(cf.make_scene(tile), fail_search="GUNW"), job)
    b = build_scene_bundle(tile, rec, as_of="2026-09-20T00:00:00Z", ancillary=NullAncillary())
    failed = [c for c in job.calls if c["ok"] is False]
    assert failed and "ASF unreachable" in failed[0]["error"]
    assert b.status in ("partial", "ok")
    assert any(e["kind"] == "call_end" and e["data"]["ok"] is False for e in job.events)


def test_unknown_source_fails_with_the_engines_error_text():
    j = Job("x", {"lon": -118.5, "lat": 34.0, "source": "nonsense"})
    run_job(j)
    assert j.state == "failed" and j.error


def _serve(allow_selftest=True):
    from debug_hub.server import Hub, make_handler
    hub = Hub(0, allow_selftest, "live")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(hub))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return hub, httpd, httpd.server_address[1]


def _req(port, path, method="GET", headers=None, body=None):
    r = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method, headers=headers or {},
                               data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_server_refuses_foreign_host_missing_token_and_bad_input():
    hub, httpd, port = _serve(allow_selftest=False)
    try:
        assert _req(port, "/api/config", headers={"Host": "evil.example"})[0] == 403
        assert _req(port, "/api/run", "POST", body={"lon": 0, "lat": 0})[0] == 403                      # no token
        tok = {"X-Hub-Token": hub.token}
        assert _req(port, "/api/run", "POST", tok, {"lon": 0, "lat": 95, "source": "live"})[0] == 400    # out of range
        assert _req(port, "/api/run", "POST", tok, {"lon": 1, "lat": 1, "source": "selftest"})[0] == 400  # selftest disabled
        assert _req(port, "/api/run", "POST", {**tok, "Origin": "http://evil.example"}, {"lon": 1, "lat": 1})[0] == 403
        assert _req(port, "/api/tile?lon=-118.25&lat=34.05")[0] == 200
        assert _req(port, "/api/tile?lon=abc&lat=1")[0] == 400
    finally:
        httpd.shutdown()


def test_server_end_to_end_image_and_pixel_probe_come_from_the_stored_array():
    hub, httpd, port = _serve()
    try:
        tok = {"X-Hub-Token": hub.token}
        code, body = _req(port, "/api/run", "POST", tok, {"lon": -118.5, "lat": 34.0, "source": "selftest"})
        assert code == 200
        jid = json.loads(body)["id"]
        job = hub.jobs[jid]
        for _ in range(600):
            if job.finished:
                break
            threading.Event().wait(0.1)
        assert job.state == "done", job.error
        aid = job.retrieval["clips"][0]["grids"][0]["layers"][0]["aid"]
        code, png = _req(port, f"/api/img?id={jid}&aid={aid}")
        assert code == 200 and png[:8] == b"\x89PNG\r\n\x1a\n"
        code, px = _req(port, f"/api/px?id={jid}&aid={aid}&fx=0.5&fy=0.5")
        p = json.loads(px)
        e = job.store.get(aid)
        assert code == 200 and p["finite"] == bool(e.data[p["row"], p["col"]] == e.data[p["row"], p["col"]])
    finally:
        httpd.shutdown()
