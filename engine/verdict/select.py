"""4.4 select — best verdict per tile + everything above threshold; honest abstention.

Rules (from the engine architecture):
  * one BEST verdict per tile (the status priority below; ties broken by raw_p then pipeline name);
  * all verdicts above the given tier threshold are returned alongside;
  * the null pipeline is ALWAYS scored — a NOT_SUPPORTED from a real pipeline is a verdict, not a
    gap, and "no detection" never reads as "nothing ran";
  * NO_SIGNAL (tier "quiet") is a decision, ranked right after NOT_SUPPORTED, so a quiet tile reports it
    instead of a NOT_APPLICABLE / NOT_MODELLED pipeline;
  * the selector ABSTAINS (returns no best) when the best verdict is below the threshold or in a
    non-decision tier (pending / insufficient / error) — an honest "we cannot say" instead of a
    confident wrong answer.
"""
from __future__ import annotations

PRIORITY = {
    "SUPPORTED": 0,
    "UNCATALOGUED_CANDIDATE": 1,
    "POSSIBLE": 2,
    "NOT_SUPPORTED": 3,
    "NO_SIGNAL": 4,           # a designed outcome (a pipeline looked and found nothing above its floor):
                              # more informative than "could not tell" and than "does not apply here"
    "INSUFFICIENT_EVIDENCE": 5,
    "PENDING": 6,
    "NOT_RUN": 7,
    "NOT_APPLICABLE": 8,
    "NOT_MODELLED": 9,
    "ERROR": 10,
}
_UNKNOWN_RANK = max(PRIORITY.values()) + 1   # a status nobody listed ranks LAST, never ahead of a real verdict

DECISION_TIERS = {"supported", "candidate", "not_supported", "quiet"}


def select(rows: list, threshold_tier: str = "not_supported") -> dict:
    """-> {"best_by_tile": {tile: row}, "above_threshold": [rows], "abstained": [tiles]}.

    threshold_tier: the WEAKEST tier still reportable as a tile's answer
    (supported > candidate > not_supported).
    """
    order = ["supported", "candidate", "not_supported"]
    if threshold_tier not in order:
        raise ValueError(f"threshold_tier must be one of {order}, got {threshold_tier!r}")
    allowed = set(order[: order.index(threshold_tier) + 1])
    if threshold_tier == "not_supported":
        allowed.add("quiet")       # "quiet" is an answer (nothing above the floor), reportable at the weakest level;
                                   # a stricter threshold (supported / candidate) asks for a positive finding only

    by_tile: dict = {}
    for row in rows:
        tile = row.get("tile")
        if not tile:
            continue
        by_tile.setdefault(tile, []).append(row)

    best_by_tile: dict = {}
    above: list = []
    abstained: list = []
    for tile, candidates in sorted(by_tile.items()):
        ranked = sorted(candidates, key=lambda r: (PRIORITY.get(r.get("status", "ERROR"), _UNKNOWN_RANK),
                                                   -(r.get("raw_p") or 0.0),
                                                   str(r.get("pipeline"))))
        best = ranked[0]
        best_by_tile[tile] = best
        for r in candidates:
            if (r.get("tier") or "unknown") in allowed:
                above.append(r)
        if (best.get("tier") or "unknown") not in allowed:
            abstained.append({"tile": tile, "reason": f"best verdict tier is {best.get('tier')!r} "
                                                      f"({best.get('status')}) — below the reporting threshold"})
    return {"best_by_tile": best_by_tile, "above_threshold": above, "abstained": abstained}


__all__ = ["select", "PRIORITY", "DECISION_TIERS"]
