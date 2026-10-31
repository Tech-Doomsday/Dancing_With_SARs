"""Heavy-job lane tests: content-addressed cache, progress file, cooperative timeout, WAL store.

A tiny synthetic heavy pipeline (in a tmp pipelines dir) exercises the runner contract without
touching the network; the REAL heavy pipeline's science is covered by its own test package.
"""
from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from engine.common import DirectoryAncillary  # noqa: E402
from engine.common.chain import build_scene_bundle  # noqa: E402
from engine.common.bundle_io import save_bundle  # noqa: E402
from tests.common_fixtures import (  # noqa: E402
    FakeRetriever, default_tile, make_scene, write_ancillary)

AS_OF = "2026-10-05T00:00:00Z"
WINDOW = ("2026-09-25T00:00:00Z", "2026-10-05T00:00:00Z")

SLOW_MANIFEST = """code: "9.99"
name: "slow_heavy"
sensor: "fixture"
applies: "always"
uses_codes: []
fetch_codes: []
execution: heavy
timeout_s: 30
needs_sar: false
min_epochs: null
status_values:
  - "SUPPORTED"
"""

SLOW_VERDICT = '''
"""Synthetic heavy pipeline: deterministic work + progress reports (runner contract fixture)."""
import time

from engine.common.result import EvidenceItem, PipelineResult


def run(bundle, providers=None):
    progress = (providers or {}).get("progress")
    steps = 5
    for i in range(steps):
        time.sleep(0.15)
        if progress is not None:
            progress("working", (i + 1) / steps)
    return PipelineResult(
        pipeline="slow_heavy", tile=bundle.tile.id, epoch=bundle.epoch, status="SUPPORTED",
        summary="synthetic heavy job finished", raw_p=0.5, confidence=None,
        evidence=[EvidenceItem(id="done", kind="info", claim="five steps done")],
        data_used=["fixture"], versions={"pipeline": "1.0"}, bundle_id=bundle.bundle_id)
'''


@pytest.fixture(scope="module")
def heavy_repo(tmp_path_factory):
    """A repo-isolated pipelines dir with the synthetic heavy pipeline + a persisted bundle."""
    tmp = tmp_path_factory.mktemp("heavy")
    pipelines = tmp / "pipelines"
    (pipelines / "slow_heavy").mkdir(parents=True)
    (pipelines / "slow_heavy" / "manifest.yaml").write_text(SLOW_MANIFEST)
    (pipelines / "slow_heavy" / "verdict.py").write_text(SLOW_VERDICT)
    (pipelines / "slow_heavy" / "__init__.py").write_text("")

    tile = default_tile()
    specs = make_scene(tile, n_epochs=4, pairs=((0, 1),), dlos=0.0)
    anc_root = write_ancillary(tmp / "ancillary", tile)
    bundle = build_scene_bundle(tile, FakeRetriever(specs), as_of=AS_OF,
                                ancillary=DirectoryAncillary(anc_root), event_window=WINDOW)
    bpath = save_bundle(bundle, tmp / "bundles")
    return tmp, pipelines, bpath


@pytest.fixture()
def runner(heavy_repo, tmp_path):
    from engine.common.config import CommonConfig
    from engine.jobs import HeavyJobRunner
    from engine.registry import Registry
    tmp, pipelines, bpath = heavy_repo
    reg = Registry(pipelines_dir=pipelines, cfg=CommonConfig(), bundles_dir=tmp / "bundles",
                   package="pipelines", sys_path=[str(tmp)])
    # a FRESH jobs dir per test: the cache is keyed by bundle+code, so a module-scoped bundle
    # would otherwise serve the first test's cached verdict to the later ones
    r = HeavyJobRunner(data_dir=tmp_path / "jobs", registry=reg, bundles_dir=tmp / "bundles")
    yield r
    r.close()


def _await_state(runner, job_id, timeout_s=30.0):
    deadline = time.monotonic() + timeout_s
    state = {}
    while time.monotonic() < deadline:
        state = runner.get_state(job_id)
        if state["status"] in ("ok", "error"):
            return state
        time.sleep(0.05)
    return state


def test_submit_runs_writes_progress_and_caches(runner, heavy_repo):
    _, pipelines, bpath = heavy_repo
    out = runner.submit("slow_heavy", bpath)
    assert out["status"] == "queued"
    state = _await_state(runner, out["job_id"])
    assert state["status"] == "ok", state
    assert state["result"]["status"] == "SUPPORTED"
    assert state["result"]["summary"] == "synthetic heavy job finished"

    pf = runner.data_dir / f"{out['job_id']}.json"
    assert pf.exists()
    assert json.loads(pf.read_text())["status"] == "ok"

    out2 = runner.submit("slow_heavy", bpath)
    assert out2["status"] == "ok" and out2.get("cached") is True
    assert out2["result"]["raw_p"] == 0.5


def test_cooperative_timeout(runner, heavy_repo, monkeypatch):
    from engine.registry import Manifest
    monkeypatch.setattr(Manifest, "deadline_s", property(lambda self: 0.3))
    _, pipelines, bpath = heavy_repo
    out = runner.submit("slow_heavy", bpath)
    state = _await_state(runner, out["job_id"])
    assert state["status"] == "error", state
    assert "timeout_s" in (state.get("error") or "")


def test_cooperative_cancel(runner, heavy_repo, monkeypatch):
    _, pipelines, bpath = heavy_repo
    out = runner.submit("slow_heavy", bpath)
    time.sleep(0.2)                      # while step 1-2 are running
    assert runner.cancel(out["job_id"]) is True
    state = _await_state(runner, out["job_id"])
    assert state["status"] == "error"
    assert "cancelled" in (state.get("error") or "")
