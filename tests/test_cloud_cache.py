"""Supabase verdict cache (engine write side). Offline: a local HTTP server plays PostgREST and
records exactly what the engine sends; the integration tests drive the REAL job runner."""
from __future__ import annotations

import json
import math
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
import pytest

from engine.verdict.cloud_cache import CUSTOM_PARAMS, NOT_CACHED, CloudCache, to_record

# the API suite's real-bundle fixtures (a persisted scene + injected event provider)
from tests.test_api import client, data_dir  # noqa: F401  (pytest fixtures)

KEY = "service-key-must-never-leak"


class FakePostgrest:
    """Records requests; answers 201 (or the configured failure)."""

    def __init__(self, status: int = 201, body: bytes = b""):
        self.requests: list[dict] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                n = int(self.headers.get("Content-Length", 0))
                outer.requests.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()},
                                       "body": json.loads(self.rfile.read(n) or b"null")})
                self.send_response(status)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_port}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


@pytest.fixture()
def pg():
    s = FakePostgrest()
    yield s
    s.close()


def row(pipeline="earthquake_coseismic", status="SUPPORTED", **kw):
    base = {"pipeline": pipeline, "tile": "11SLT66", "epoch": "2026-08-06", "bundle_id": "b1",
            "status": status, "tier": "supported", "raw_p": 0.7, "summary": "s",
            "evidence": [{"id": "e1", "kind": "detection", "claim": "c", "level": "decision"}],
            "layers": [], "provenance": [], "data_used": ["d"], "references_used": ["r"],
            "warnings": [], "runtime_s": 1.0, "min_epochs_met": None, "versions": {},
            "detail": {"layers": {"displacement": {"name": "displacement", "data_b64": "AAAA", "shape": [1, 1]}},
                       "n": 3}}
    base.update(kw)
    return base


# ------------------------------------------------------------------ configuration
def test_not_configured_is_disabled_with_a_reason_never_a_silent_success():
    rep = CloudCache(None, None).write_run([row()], run_id="r", bundle_id="b1", tile="t")
    assert rep["enabled"] is False and rep["written"] == 0 and "not configured" in rep["reason"]


def test_plain_http_to_a_remote_host_is_refused_the_service_key_is_not_sent_in_clear():
    c = CloudCache("http://example.com", KEY)
    assert not c.enabled and "https" in c.disabled_reason
    assert CloudCache("http://localhost:54321", KEY).enabled      # local dev is fine
    assert CloudCache("https://abc.supabase.co", KEY).enabled


def test_repr_never_contains_the_key():
    assert KEY not in repr(CloudCache("https://abc.supabase.co", KEY))


# ------------------------------------------------------------------ the record
def test_record_splits_layers_out_of_result_and_keeps_everything_else_verbatim():
    rec = to_record(row(), run_id="run-1", bundle_id="b1", tile="t", now="2026-10-28T00:00:00+00:00")
    assert rec["layers"] == {"displacement": {"name": "displacement", "data_b64": "AAAA", "shape": [1, 1]}}
    assert "layers" not in rec["result"]["detail"] and rec["result"]["detail"]["n"] == 3
    assert (rec["tile"], rec["pipeline"], rec["bundle_id"], rec["status"], rec["raw_p"]) == \
           ("11SLT66", "earthquake_coseismic", "b1", "SUPPORTED", 0.7)
    assert "first_computed_at" not in rec          # the DB default sets it once; a redo must not reset it


# ------------------------------------------------------------------ the write
def test_write_posts_one_upsert_with_the_right_url_headers_and_body(pg):
    cache = CloudCache(pg.url, KEY)
    rep = cache.write_run([row(), row("tsunami_ocean_evidence", "INSUFFICIENT_EVIDENCE", raw_p=None)],
                          run_id="run-1", bundle_id="b1", tile="11SLT66")
    assert rep == {"enabled": True, "written": 2, "skipped": [], "failed": []}
    assert len(pg.requests) == 1                    # one statement: all verdicts of a run land together
    r = pg.requests[0]
    assert r["path"] == "/rest/v1/verdicts?on_conflict=tile,pipeline,bundle_id"
    assert r["headers"]["prefer"] == "resolution=merge-duplicates,return=minimal"
    assert r["headers"]["apikey"] == KEY and r["headers"]["authorization"] == f"Bearer {KEY}"
    assert [x["pipeline"] for x in r["body"]] == ["earthquake_coseismic", "tsunami_ocean_evidence"]
    assert r["body"][1]["status"] == "INSUFFICIENT_EVIDENCE" and r["body"][1]["raw_p"] is None
    assert KEY not in json.dumps(rep)


def test_nan_inf_and_numpy_become_valid_json(pg):
    rw = row(detail={"layers": {}, "x": float("nan"), "y": float("inf"), "arr": np.arange(3), "f": np.float32(1.5),
                     "nested": [np.float64(2.0), float("-inf")]})
    rep = CloudCache(pg.url, KEY).write_run([rw], run_id="r", bundle_id="b1", tile="t")
    assert rep["written"] == 1, rep
    d = pg.requests[0]["body"][0]["result"]["detail"]
    assert d == {"x": None, "y": None, "arr": [0, 1, 2], "f": 1.5, "nested": [2.0, None]}


def test_error_and_pending_are_never_cached(pg):
    rep = CloudCache(pg.url, KEY).write_run(
        [row(), row("a", "ERROR"), row("b", "PENDING")], run_id="r", bundle_id="b1", tile="t")
    assert rep["written"] == 1
    assert {s["pipeline"]: s["reason"] for s in rep["skipped"]} == {"a": NOT_CACHED["ERROR"], "b": NOT_CACHED["PENDING"]}
    assert [x["pipeline"] for x in pg.requests[0]["body"]] == ["earthquake_coseismic"]


def test_only_errors_means_no_request_at_all(pg):
    rep = CloudCache(pg.url, KEY).write_run([row("a", "ERROR")], run_id="r", bundle_id="b1", tile="t")
    assert rep["written"] == 0 and pg.requests == []


def test_custom_parameter_runs_are_not_cached(pg):
    rep = CloudCache(pg.url, KEY).write_run([row()], run_id="r", bundle_id="b1", tile="t",
                                            params={"significance_k": 4.0})
    assert rep["written"] == 0 and pg.requests == [] and rep["skipped"][0]["reason"] == CUSTOM_PARAMS


def test_empty_params_dict_is_the_default_run_and_is_cached(pg):
    assert CloudCache(pg.url, KEY).write_run([row()], run_id="r", bundle_id="b1", tile="t", params={})["written"] == 1


def test_a_supabase_rejection_is_reported_not_raised_and_not_leaking_the_key():
    s = FakePostgrest(status=401, body=b'{"message":"Invalid API key"}')
    try:
        rep = CloudCache(s.url, KEY).write_run([row()], run_id="r", bundle_id="b1", tile="t")
    finally:
        s.close()
    assert rep["written"] == 0 and "HTTP 401" in rep["failed"][0] and "Invalid API key" in rep["failed"][0]
    assert KEY not in json.dumps(rep)


def test_unreachable_supabase_is_reported_not_raised():
    rep = CloudCache("http://127.0.0.1:9", KEY, timeout_s=2).write_run([row()], run_id="r", bundle_id="b1", tile="t")
    assert rep["written"] == 0 and rep["failed"] and "unreachable" in rep["failed"][0]


# ------------------------------------------------------------------ through the REAL job runner
def _run(c, bundle_id, params=None):
    r = c.post("/api/runs", json={"bundle_id": bundle_id, "pipelines": ["earthquake_coseismic"], "params": params or {}})
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]
    for _ in range(300):
        state = c.get(f"/api/runs/{run_id}").json()
        if state["status"] in ("ok", "error"):
            return state
        time.sleep(0.1)
    raise AssertionError("run did not finish")


def test_a_real_run_is_mirrored_to_the_cache_and_the_run_state_says_so(client, pg):  # noqa: F811
    from engine.api.state import get_runner
    c, _ = client
    get_runner().cache = CloudCache(pg.url, KEY)
    bundle_id = c.get("/api/bundles").json()["bundles"][0]["bundle_id"]

    state = _run(c, bundle_id)
    assert state["status"] == "ok", state.get("error")
    assert state["cache"] == {"enabled": True, "written": 1, "skipped": [], "failed": []}

    rec = pg.requests[0]["body"][0]
    eq = next(v for v in state["results"] if v["pipeline"] == "earthquake_coseismic")
    # what is cached is exactly what the engine answered — status and rank score verbatim
    assert (rec["status"], rec["raw_p"], rec["bundle_id"], rec["tile"]) == (eq["status"], eq["raw_p"], bundle_id, eq["tile"])
    assert rec["run_id"] == state["id"] and rec["tier"]
    assert set(rec["layers"]) >= {"displacement", "detected"} and rec["layers"]["displacement"]["data_b64"]
    assert "layers" not in rec["result"]["detail"] and rec["result"]["evidence"] == eq["evidence"]


def test_a_redo_is_a_second_upsert_on_the_same_key(client, pg):  # noqa: F811
    from engine.api.state import get_runner
    c, _ = client
    get_runner().cache = CloudCache(pg.url, KEY)
    bundle_id = c.get("/api/bundles").json()["bundles"][0]["bundle_id"]
    _run(c, bundle_id)
    _run(c, bundle_id)
    keys = [(r["body"][0]["tile"], r["body"][0]["pipeline"], r["body"][0]["bundle_id"]) for r in pg.requests]
    assert len(keys) == 2 and keys[0] == keys[1]          # same primary key -> the DB overwrites in place


def test_a_custom_parameter_run_reports_it_was_not_cached(client, pg):  # noqa: F811
    from engine.api.state import get_runner
    c, _ = client
    get_runner().cache = CloudCache(pg.url, KEY)
    bundle_id = c.get("/api/bundles").json()["bundles"][0]["bundle_id"]
    state = _run(c, bundle_id, {"significance_k": 4.0})
    assert state["status"] == "ok" and state["cache"]["written"] == 0
    assert state["cache"]["skipped"][0]["reason"] == CUSTOM_PARAMS and pg.requests == []


def test_a_failing_cache_never_fails_the_run(client):  # noqa: F811
    from engine.api.state import get_runner
    c, _ = client
    get_runner().cache = CloudCache("http://127.0.0.1:9", KEY, timeout_s=2)
    bundle_id = c.get("/api/bundles").json()["bundles"][0]["bundle_id"]
    state = _run(c, bundle_id)
    assert state["status"] == "ok" and state["results"] and state["cache"]["failed"]


def test_unconfigured_engine_runs_exactly_as_before(client):  # noqa: F811
    from engine.api.state import get_runner
    c, _ = client
    get_runner().cache = CloudCache(None, None)
    bundle_id = c.get("/api/bundles").json()["bundles"][0]["bundle_id"]
    state = _run(c, bundle_id)
    assert state["status"] == "ok" and state["cache"]["enabled"] is False and state["cache"]["written"] == 0
