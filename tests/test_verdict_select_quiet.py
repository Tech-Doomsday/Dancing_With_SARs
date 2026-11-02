"""A5: a quiet tile must report NO_SIGNAL, not 'landslide: NOT_APPLICABLE' and not 'abstained'."""
from __future__ import annotations

from engine.verdict.calibrate import calibrate
from engine.verdict.select import PRIORITY, select


def _row(pipeline, status, tile="T1", raw_p=None):
    return calibrate({"tile": tile, "pipeline": pipeline, "status": status, "raw_p": raw_p})


def _quiet_tile():
    return [_row("earthquake_coseismic", "NO_SIGNAL"), _row("landslide", "NOT_APPLICABLE"),
            _row("volcanic_deformation", "NOT_MODELLED"), _row("flood_inundation", "NOT_RUN"),
            _row("tsunami_ocean_evidence", "INSUFFICIENT_EVIDENCE"), _row("urban_damage", "PENDING")]


def test_no_signal_is_in_the_priority_table_and_beats_everything_that_says_nothing():
    assert "NO_SIGNAL" in PRIORITY
    for weaker in ("INSUFFICIENT_EVIDENCE", "PENDING", "NOT_RUN", "NOT_APPLICABLE", "NOT_MODELLED", "ERROR"):
        assert PRIORITY["NO_SIGNAL"] < PRIORITY[weaker], weaker
    for stronger in ("SUPPORTED", "UNCATALOGUED_CANDIDATE", "POSSIBLE"):
        assert PRIORITY[stronger] < PRIORITY["NO_SIGNAL"], stronger


def test_a_quiet_tile_reports_no_signal_and_is_not_abstained():
    out = select(_quiet_tile())
    best = out["best_by_tile"]["T1"]
    assert best["status"] == "NO_SIGNAL" and best["pipeline"] == "earthquake_coseismic"   # was: landslide NOT_APPLICABLE
    assert out["abstained"] == []                                                          # was: abstained
    assert out["quiet"] == ["T1"]
    assert out["above_threshold"] == []                                                    # quiet is an answer, not a hazard


def test_a_real_verdict_still_outranks_quiet_and_unknown_statuses_still_rank_last():
    rows = _quiet_tile() + [_row("tsunami_ocean_evidence", "SUPPORTED", raw_p=0.6)]
    assert select(rows)["best_by_tile"]["T1"]["status"] == "SUPPORTED"
    rows = [_row("a", "WHATEVER"), _row("b", "NOT_MODELLED")]
    assert select(rows)["best_by_tile"]["T1"]["status"] == "NOT_MODELLED"


def test_a_tile_with_nothing_but_gaps_is_still_abstained():
    out = select([_row("a", "NOT_MODELLED"), _row("b", "INSUFFICIENT_EVIDENCE")])
    assert [a["tile"] for a in out["abstained"]] == ["T1"] and out["quiet"] == []
