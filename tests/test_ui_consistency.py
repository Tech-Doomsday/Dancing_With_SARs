"""B6: one colour per direction. The Map's Data tab, the Background timeline, list, detail and summary all read
DIRECTION_COLORS from lib/status.ts; no second palette (the old purple) may creep back."""
from __future__ import annotations

from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "app-ui" / "src"


def test_no_second_descending_colour_in_the_ui():
    assert [str(f.relative_to(SRC)) for f in SRC.rglob("*.ts*") if "b39ce8" in f.read_text(encoding="utf-8").lower()] == []


def test_every_explorer_view_takes_direction_colours_from_the_shared_map():
    for name in ("TimelineCanvas", "ExplorerList", "ExplorerSummary", "ExplorerDetail"):
        text = (SRC / "components" / "globe" / "explorer" / f"{name}.tsx").read_text(encoding="utf-8")
        assert "DIRECTION_COLORS" in text, name
