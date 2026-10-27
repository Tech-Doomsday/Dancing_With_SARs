"""debug_hub: it must observe the real chain without changing it, report failures verbatim, and refuse foreign callers."""
from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import numpy as np

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
    plain = build_scene_bundle(tile, cf.FakeRetriever(cf.selftest_scene(tile)), as_of="2026-09-20T00:00:00Z", ancillary=NullAncillary())
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


def test_independence_check_matches_the_repo_rule(tmp_path):
    """A pipeline's own fetch.py may use the retrieval layer; importing another pipeline or a chain step is a violation."""
    from debug_hub.health import independence
    a, b = tmp_path / "alpha", tmp_path / "beta"
    a.mkdir(); b.mkdir()
    (a / "fetch.py").write_text("from engine.retrieval.clients.nisar_client import NisarClient\nfrom engine.common.scene_bundle import SceneBundle\n"
                                "from engine.pipelines.alpha import model\n")
    (b / "model.py").write_text("from engine.pipelines.alpha import fetch\nfrom engine.common.p05_geometry import run\n"
                                "from engine.common import build_scene_bundle\n")
    out = independence(tmp_path)
    by = {}
    for v in out["violations"]:
        by.setdefault(v["file"].split("/")[0], []).append(v["problem"])
    assert "alpha" not in by                                  # retrieval + bundle types + own package: all allowed
    assert len(by["beta"]) == 3                               # other pipeline, chain step, chain entry point


def test_independence_resolves_relative_imports(tmp_path):
    """Regression: relative imports used to be skipped, so `from ..beta import model` was never flagged."""
    from debug_hub.health import independence
    a, b = tmp_path / "alpha", tmp_path / "beta"
    a.mkdir(); b.mkdir()
    (b / "model.py").write_text("x = 1\n")
    (a / "model.py").write_text("from ..beta import model\n")
    out = independence(tmp_path)
    assert [v["problem"] for v in out["violations"]] == ["imports another pipeline (beta)"]

    (a / "model.py").write_text("from .. import beta\n")
    out = independence(tmp_path)
    assert len(out["violations"]) == 1 and out["violations"][0]["file"].startswith("alpha/")

    (a / "model.py").write_text("from . import fetch\nfrom .prep import x\nfrom .. import alpha\n")
    assert independence(tmp_path)["violations"] == []         # own package, however it is spelled


def test_independence_real_repo_is_clean():
    from debug_hub.health import independence
    out = independence()
    assert out["violations"] == [] and out["scanned_files"] > 0


def test_make_source_rejects_unknown_kinds_without_touching_the_network():
    from debug_hub.runner import make_source
    import pytest
    with pytest.raises(ValueError, match="unknown source 'nonsense'"):
        make_source("nonsense", None, None, {})
    with pytest.raises(ValueError):
        make_source("", None, None, {})


def test_pipelines_tab_runs_featured_through_the_real_registry_path():
    """PIPELINES tab: earthquake runs the registry path on the job's own bundle; hurricane gets the
    registry's own honest answer. Layer payloads are decoded into the ArrayStore, never shipped as b64."""
    import json
    from debug_hub.util import plain
    j = _run()
    assert j.state == "done", j.error
    P = j.pipelines
    assert P and set(P["pipelines"]) == {"earthquake_coseismic", "tropical_cyclone"}
    eq = P["pipelines"]["earthquake_coseismic"]
    assert eq["launched"] is True
    assert eq["status"] == "SUPPORTED", (eq["status"], eq.get("summary"))   # the selftest scene IS the T-8 truth bowl
    assert eq["detail"]["decision"]["rule"] == "screen_pass"
    assert eq["raw_p"] is not None and 0.0 <= eq["raw_p"] <= 1.0
    assert eq["detail"]["events_considered"][0]["id"] == "hub-selftest-event"
    assert eq["layers_store"] and {l["name"] for l in eq["layers_store"]} >= {"displacement", "threshold", "detected"}
    for l in eq["layers_store"]:
        e = j.store.get(l["aid"])
        assert e is not None and e.data.size == int(np.prod(l["shape"]))
    # selftest stays hermetic: the event fetch used the fixture provider, no base64 leaves the server
    assert "hub selftest fixture" in P["providers_used"]["earthquake_coseismic"]
    assert "data_b64" not in json.dumps(plain(P))
    tc = P["pipelines"]["tropical_cyclone"]
    assert tc["launched"] is False and tc["status"] in ("NOT_APPLICABLE", "NOT_RUN", "INSUFFICIENT_EVIDENCE", "PENDING")
    assert tc["detail"]["routing"]["reasons"]


def test_featured_pipeline_failure_does_not_fail_the_job(monkeypatch):
    """A crashing featured pipeline is contained exactly like in the registry: the job still completes
    and the failure is visible verbatim on the PIPELINES tab."""
    import debug_hub.pipelines as pl
    def boom(*a, **k):
        raise RuntimeError("simulated featured-pipeline crash")
    monkeypatch.setattr(pl, "run_featured", boom)
    j = _run()
    assert j.state == "done"
    assert j.pipelines["error"] == "RuntimeError: simulated featured-pipeline crash"
    assert j.final and j.handoff                       # everything else is untouched


def test_server_serves_pipelines_part_ui_tab_and_pipeline_images():
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
        code, body = _req(port, f"/api/job?id={jid}&part=pipelines")
        assert code == 200
        P = json.loads(body)
        assert P["pipelines"]["earthquake_coseismic"]["status"] == "SUPPORTED"
        aid = P["pipelines"]["earthquake_coseismic"]["layers_store"][0]["aid"]
        code, png = _req(port, f"/api/img?id={jid}&aid={aid}")
        assert code == 200 and png[:8] == b"\x89PNG\r\n\x1a\n"
        code, html = _req(port, "/")
        assert code == 200 and b"PIPELINES" in html and b"viewPipelines" in html
    finally:
        httpd.shutdown()


def test_pipeline_entry_contract_is_read_from_the_registry_source():
    from debug_hub.health import pipeline_entry_contract
    c = pipeline_entry_contract()
    assert c["entry_module"] == "verdict" and c["entry_func"] == "run"
    assert c["registry_refers_to_fetch_module"] is False
    assert "fetch.py" in c["meaning"]
    assert c["registry_docstring_says_bundle_only"] is True


def test_pipeline_entry_contract_detects_a_fetch_reference(tmp_path):
    from debug_hub.health import pipeline_entry_contract
    f = tmp_path / "registry.py"
    f.write_text('"""A pipeline sees ONLY the bundle."""\nENTRY_MODULE = "verdict"\nENTRY_FUNC = "run"\nMODS = ("fetch", "verdict")\n')
    c = pipeline_entry_contract(f)
    assert c["registry_refers_to_fetch_module"] is True and c["entry_module"] == "verdict"
    f.write_text("x = 1\n")
    c = pipeline_entry_contract(f)
    assert c["entry_module"] is None and c["entry_func"] is None and c["registry_refers_to_fetch_module"] is False


def test_every_s_channel_is_named_in_ancillary_py():
    from debug_hub.health import S_CHANNEL, s_channel_check
    rows = s_channel_check()
    assert {r["code"] for r in rows} == set(S_CHANNEL)
    assert all(r["found_in_ancillary_py"] is True for r in rows), [r for r in rows if not r["found_in_ancillary_py"]]


def test_unknown_channel_is_reported_missing_in_the_needs_matrix(monkeypatch, tmp_path):
    from debug_hub import health
    monkeypatch.setitem(health.S_CHANNEL, "S3", "no_such_channel_xyz")
    rows = {r["code"]: r for r in health.s_channel_check()}
    assert rows["S3"]["found_in_ancillary_py"] is False
    n = health.needs_matrix(tmp_path)
    s3 = [c["S3"] for r in n["rows"] for c in [r["cells"]] if "S3" in c]
    assert s3 and all(c["state"] == "missing" and "'no_such_channel_xyz' is not named anywhere in engine/common/ancillary.py" in c["why"] for c in s3)
