"""The UI's tier tables (app-ui/src/lib/status.ts) must cover every tier the engine can emit.

A hand-copied chip table once covered 5 of 9 tiers, so a finding with tier `error` rendered in the
generic fallback instead of the "run failed" look. The tables are now single, and this pins them to the
engine's own TIER_OF / TIER_LABEL so an incomplete one cannot come back silently."""
from __future__ import annotations

import re
from pathlib import Path

from engine.verdict.calibrate import TIER_LABEL, TIER_OF

STATUS_TS = Path(__file__).resolve().parents[1] / "app-ui" / "src" / "lib" / "status.ts"


def _keys(table: str) -> set[str]:
    src = STATUS_TS.read_text(encoding="utf-8")
    m = re.search(rf"export const {table}\b[^=]*=\s*\{{(.*?)\n\}};", src, re.S)
    assert m, f"{table} not found in status.ts"
    return set(re.findall(r"^\s{2}(\w+):", m.group(1), re.M))


def test_every_engine_tier_has_a_frontend_chip_and_pin_entry():
    tiers = set(TIER_LABEL) | set(TIER_OF.values())
    assert tiers == set(TIER_LABEL), "TIER_OF emits a tier TIER_LABEL does not name"
    for table in ("TIER_CHIP_CLASSES", "TIER_PIN_COLORS"):
        missing = tiers - _keys(table)
        assert not missing, f"{table} has no entry for engine tier(s) {sorted(missing)}"


def test_every_engine_status_has_a_status_only_badge():
    missing = set(TIER_OF) - _keys("STATUS_CHIP")
    assert not missing, f"STATUS_CHIP has no entry for engine status(es) {sorted(missing)}"
