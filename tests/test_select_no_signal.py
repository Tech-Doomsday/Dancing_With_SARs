"""A5: a quiet tile reports NO_SIGNAL as its best verdict, not NOT_APPLICABLE / NOT_MODELLED, and is not 'abstained'."""
from engine.verdict.select import PRIORITY, select


def _row(pipeline, status, tier, raw_p=None, tile="T1"):
    return {"tile": tile, "pipeline": pipeline, "status": status, "tier": tier, "raw_p": raw_p}


QUIET_TILE = [
    _row("earthquake_coseismic", "NO_SIGNAL", "quiet"),
    _row("landslide", "NOT_APPLICABLE", "not_applicable"),
    _row("volcanic_deformation", "NOT_MODELLED", "not_modelled"),
    _row("tsunami_ocean_evidence", "NOT_APPLICABLE", "not_applicable"),
]


def test_no_signal_is_in_the_priority_table_between_not_supported_and_insufficient():
    assert PRIORITY["NOT_SUPPORTED"] < PRIORITY["NO_SIGNAL"] < PRIORITY["INSUFFICIENT_EVIDENCE"]
    assert PRIORITY["NO_SIGNAL"] < PRIORITY["NOT_APPLICABLE"] < PRIORITY["NOT_MODELLED"]


def test_quiet_tile_reports_no_signal_as_best_and_is_not_abstained():
    out = select(QUIET_TILE)
    assert out["best_by_tile"]["T1"]["status"] == "NO_SIGNAL"
    assert out["best_by_tile"]["T1"]["pipeline"] == "earthquake_coseismic"
    assert out["abstained"] == []
    assert [r["status"] for r in out["above_threshold"]] == ["NO_SIGNAL"]


def test_order_of_rows_does_not_matter():
    assert select(list(reversed(QUIET_TILE)))["best_by_tile"]["T1"]["status"] == "NO_SIGNAL"


def test_a_positive_or_rejected_verdict_still_beats_quiet():
    rows = QUIET_TILE + [_row("x", "NOT_SUPPORTED", "not_supported", 0.1)]
    assert select(rows)["best_by_tile"]["T1"]["status"] == "NOT_SUPPORTED"
    rows = QUIET_TILE + [_row("y", "SUPPORTED", "supported", 0.9)]
    assert select(rows)["best_by_tile"]["T1"]["status"] == "SUPPORTED"


def test_quiet_beats_insufficient_evidence_but_a_strict_threshold_abstains_on_it():
    rows = QUIET_TILE + [_row("z", "INSUFFICIENT_EVIDENCE", "insufficient")]
    assert select(rows)["best_by_tile"]["T1"]["status"] == "NO_SIGNAL"
    strict = select(QUIET_TILE, threshold_tier="supported")      # asked for a positive finding only
    assert strict["abstained"] and strict["abstained"][0]["tile"] == "T1"


def test_an_unknown_status_ranks_last_not_ahead_of_a_real_verdict():
    rows = [_row("a", "SOMETHING_NEW", "unknown"), _row("b", "NOT_MODELLED", "not_modelled")]
    assert select(rows)["best_by_tile"]["T1"]["status"] == "NOT_MODELLED"
