"""4.2 calibrate — status tiers today; isotonic/Platt only once labelled data exists.

Engine principle 4 and Decision 4 of the port review: there is NO calibrated probability yet.
`raw_p` stays an uncalibrated within-tier rank score; `confidence` stays None unless a pipeline
could justify a number itself. This stage maps statuses onto the four UI tiers, records WHICH
calibration would apply when labels arrive, and never invents a number.
"""
from __future__ import annotations

TIER_OF = {
    "SUPPORTED": "supported",
    "UNCATALOGUED_CANDIDATE": "candidate",
    "POSSIBLE": "candidate",
    "NOT_SUPPORTED": "not_supported",
    "INSUFFICIENT_EVIDENCE": "insufficient",
    "NO_SIGNAL": "quiet",          # D15: the designed outcome for a tile where nothing happened —
                                   # never an error (it used to fall through to tier="error")
    "PENDING": "pending",
    "NOT_RUN": "pending",
    "NOT_APPLICABLE": "not_applicable",
    "NOT_MODELLED": "not_modelled",
    "ERROR": "error",
}

TIER_LABEL = {
    "supported": "supported by evidence",
    "candidate": "candidate / possible",
    "not_supported": "not supported by evidence",
    "insufficient": "not enough evidence",
    "quiet": "no signal — quiet tile",
    "pending": "not run (yet)",
    "not_applicable": "did not apply here",
    "not_modelled": "not modelled yet",
    "error": "run failed",
}


def tier_of(status: str) -> str:
    return TIER_OF.get(str(status), "error")


def calibrate(row: dict) -> dict:
    """Add the tier (and its label) to a verdict row; raw_p passes through untouched.

    When verdict calibration lands (labelled histories from the operator), this is the single
    place that fits isotonic/Platt and starts filling `confidence`. Until then it is a no-op on
    the numbers — by design, and pinned by a test."""
    out = dict(row)
    out["tier"] = tier_of(row.get("status", ""))
    out["tier_label"] = TIER_LABEL[out["tier"]]
    out["calibration"] = "none — raw_p is an uncalibrated within-tier rank score (no labelled data yet)"
    return out


def calibrate_all(rows: list) -> list:
    return [calibrate(r) for r in rows]


__all__ = ["tier_of", "calibrate", "calibrate_all", "TIER_OF", "TIER_LABEL"]
