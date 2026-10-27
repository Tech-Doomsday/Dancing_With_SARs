"""Verdict stage chain tests: collect -> calibrate -> link_events -> select -> persist (verdicts.db)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from engine.common.result import EvidenceItem, PipelineResult, Source
from engine.verdict import VerdictStore, calibrate, calibrate_all, collect, link, result_to_row, select


def _row(pipeline: str, tile: str, status: str, raw_p=None, best_event=None, event_id=None):
    detail = {}
    if best_event:
        detail["decision"] = {"best_event_id": best_event}
    if event_id:
        detail["event"] = {"id": event_id}
    return {
        "pipeline": pipeline, "tile": tile, "epoch": "2026-10-05", "bundle_id": f"b-{tile}-{pipeline}",
        "status": status, "summary": f"{pipeline} on {tile}", "raw_p": raw_p, "confidence": None,
        "evidence": [EvidenceItem(id="e1", kind="detection", claim="synthetic claim").to_dict()],
        "layers": [], "provenance": [Source(adapter="r1_comcat", url_or_id="x",
                                           retrieved_at="2026-10-27T00:00:00+00:00").to_dict()],
        "data_used": [], "references_used": [], "warnings": [], "runtime_s": 0.1,
        "min_epochs_met": True, "detail": detail, "versions": {},
    }


def test_calibrate_never_invents_a_confidence():
    row = calibrate(_row("earthquake_coseismic", "11SLT66", "SUPPORTED", raw_p=0.71))
    assert row["tier"] == "supported"
    assert row["tier_label"] == "supported by evidence"
    assert row["confidence"] is None                      # no calibration exists yet
    assert "uncalibrated" in row["calibration"]
    assert row["raw_p"] == 0.71                           # passes through untouched


def test_link_groups_by_cited_event_and_flags_cross_pipeline():
    rows = calibrate_all([
        _row("earthquake_coseismic", "11SLT66", "SUPPORTED", 0.8, best_event="us6000abc"),
        _row("earthquake_coseismic", "11SLT67", "NOT_SUPPORTED", 0.1, best_event="us6000abc"),
        _row("tsunami_ocean_evidence", "11SLT66", "POSSIBLE", 0.4, event_id="us6000abc"),
        _row("landslide", "11SLT66", "NOT_APPLICABLE"),
    ])
    out = link(rows)
    ids = [e["event_id"] for e in out["events"]]
    assert ids == ["us6000abc"]
    ev = out["events"][0]
    assert set(ev["pipelines"]) == {"earthquake_coseismic", "tsunami_ocean_evidence"}
    assert ev["supporting"] is True
    assert ev["tiles"] == ["11SLT66", "11SLT67"]
    assert ev["cross_pipeline"] and "corroboration" in ev["cross_pipeline"][0]["note"]
    assert any(u["pipeline"] == "landslide" for u in out["unlinked"])


def test_select_best_per_tile_with_honest_abstention():
    rows = calibrate_all([
        _row("earthquake_coseismic", "T1", "SUPPORTED", 0.9),
        _row("tsunami_ocean_evidence", "T1", "POSSIBLE", 0.4),
        _row("earthquake_coseismic", "T2", "INSUFFICIENT_EVIDENCE", None),
    ])
    out = select(rows, threshold_tier="not_supported")
    assert out["best_by_tile"]["T1"]["pipeline"] == "earthquake_coseismic"
    assert out["best_by_tile"]["T2"]["status"] == "INSUFFICIENT_EVIDENCE"
    assert any(a["tile"] == "T2" for a in out["abstained"])       # honest abstention
    assert len(out["above_threshold"]) == 2                       # SUPPORTED + POSSIBLE for T1

    with pytest.raises(ValueError):
        select(rows, threshold_tier="supported-ish")


def test_select_treats_null_result_as_a_verdict():
    rows = calibrate_all([_row("earthquake_coseismic", "T3", "NOT_SUPPORTED", 0.2)])
    out = select(rows, threshold_tier="not_supported")
    assert out["best_by_tile"]["T3"]["status"] == "NOT_SUPPORTED"
    assert out["abstained"] == []                                 # a null result IS an answer


def test_persist_round_trip(tmp_path):
    store = VerdictStore(tmp_path / "verdicts.db")
    rows = calibrate_all([
        _row("earthquake_coseismic", "11SLT66", "SUPPORTED", 0.8, best_event="us6000abc"),
        _row("tsunami_ocean_evidence", "11SLT66", "POSSIBLE", 0.4, event_id="us6000abc"),
    ])
    links = link(rows)["events"]
    res = store.write_run(rows, run_id="run-1", links=links)
    assert res["written"] == 2 and not res["failed"]

    got = store.verdicts_for_tile("11SLT66")
    assert len(got) == 2
    by_pipe = {g["pipeline"]: g for g in got}
    assert by_pipe["earthquake_coseismic"]["status"] == "SUPPORTED"
    assert by_pipe["earthquake_coseismic"]["evidence"][0]["claim"] == "synthetic claim"
    evs = store.events()
    assert evs and evs[0]["event_id"] == "us6000abc"

    # idempotent re-write (same bundle) updates rather than duplicates
    store.write_run(rows, run_id="run-2", links=links)
    assert len(store.verdicts_for_tile("11SLT66")) == 2
    store.close()


def test_persist_failure_is_reported_not_raised(tmp_path):
    store = VerdictStore(tmp_path / "v.db")
    bad = _row("p", "t", "SUPPORTED")
    bad["evidence"] = object()                    # not JSON-able -> write must fail cleanly
    res = store.write_run([bad])
    assert res["written"] == 0 and res["failed"]
    store.close()


def test_collect_from_pipeline_result():
    pr = PipelineResult(pipeline="earthquake_coseismic", tile="T", epoch=None, status="SUPPORTED",
                        raw_p=0.5, evidence=[EvidenceItem(id="a", kind="detection", claim="c")],
                        provenance=[Source(adapter="r1_comcat", url_or_id="u", retrieved_at="now")])
    row = result_to_row(pr)
    assert row["status"] == "SUPPORTED" and row["evidence"][0]["id"] == "a"
    assert row["provenance"][0]["adapter"] == "r1_comcat"
