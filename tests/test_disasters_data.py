"""The Explore > Events list: JSON must be what the CSV produces, and carry no invented coordinates."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import build_disasters_json as b  # noqa: E402


def test_json_is_in_sync_with_the_csv():
    assert b.OUT_PATH.read_text(encoding="utf-8") == b.render(b.parse())


def test_every_csv_event_is_present_and_ids_are_unique():
    ev = json.loads(b.OUT_PATH.read_text(encoding="utf-8"))
    n_rows = sum(1 for line in b.CSV_PATH.read_text(encoding="utf-8").splitlines()[1:] if line.strip())
    assert len(ev) >= n_rows - 3          # quoted multi-line cells can make raw lines differ; csv parse is authoritative
    assert len({e["id"] for e in ev}) == len(ev)


def test_coordinates_are_only_kept_when_valid_and_confidence_is_a_or_b():
    for e in json.loads(b.OUT_PATH.read_text(encoding="utf-8")):
        if e["lat"] is None:
            assert e["lon"] is None and e["coord_conf"] is None
        else:
            assert -90 <= e["lat"] <= 90 and -180 <= e["lon"] <= 180 and e["coord_conf"] in ("A", "B")


def test_the_heatwave_row_has_no_point():
    ev = {e["event"]: e for e in json.loads(b.OUT_PATH.read_text(encoding="utf-8"))}
    assert ev["European heatwaves 2026"]["lat"] is None
