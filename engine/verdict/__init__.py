"""Verdict stages 4.1–4.5: collect -> calibrate -> link_events -> select -> persist (verdicts.db).

One rule the whole stage chain keeps (Decision 3 of the port review): event-level conclusions are
built HERE, in link_events — never by one pipeline reading another pipeline's result.
"""
from .calibrate import TIER_LABEL, TIER_OF, calibrate, calibrate_all, tier_of
from .collect import collect, result_to_row
from .link_events import link
from .persist import VerdictStore
from .select import select

__all__ = ["collect", "result_to_row", "calibrate", "calibrate_all", "tier_of", "TIER_OF",
           "TIER_LABEL", "link", "select", "VerdictStore"]
