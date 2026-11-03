#!/usr/bin/env python
"""disasters_after_nisar_launch.csv -> app-ui/src/lib/disasters.json  (the Map's Explore > Events list).

The CSV stays the source of truth: edit it, run `python tools/build_disasters_json.py`, commit both.
This script only parses and normalises; nothing is invented (empty cells stay null) and ranking is done in the UI
(app-ui/src/lib/events.ts) because part of it depends on today's date.

    python tools/build_disasters_json.py            # write the JSON
    python tools/build_disasters_json.py --check    # exit 1 if the JSON is not what the CSV produces
"""
from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "disasters_after_nisar_launch.csv"
OUT_PATH = ROOT / "app-ui" / "src" / "lib" / "disasters.json"


def _num(v: str):
    v = (v or "").strip()
    try:
        return float(v)
    except ValueError:
        return None


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60]


def parse(csv_path: Path = CSV_PATH) -> list[dict]:
    out: list[dict] = []
    seen: dict[str, int] = {}
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if not (row.get("Event") or "").strip():
                continue
            lat, lon = _num(row["Lat"]), _num(row["Lon"])
            conf = (row.get("Coord conf.") or "").strip().upper()
            located = lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180
            deaths = _num(row["Reported deaths"])
            base = _slug(row["Event"])
            seen[base] = seen.get(base, 0) + 1
            out.append({
                "id": base if seen[base] == 1 else f"{base}-{seen[base]}",
                "category": row["Category"].strip(),
                "event": row["Event"].strip(),
                "date_text": row["Date (UTC unless noted)"].strip(),
                "region": row["Country / region"].strip(),
                "lat": lat if located else None,
                "lon": lon if located else None,
                "coord_conf": conf if located and conf in ("A", "B") else None,
                "size": row["Size / intensity"].strip(),
                "deaths": int(deaths) if deaths is not None else None,
                "facts": row["Key facts"].strip(),
                "sar_angle": row["Suggested NISAR angle"].strip(),
                "sources": row["Source(s)"].strip(),
            })
    return out


def render(events: list[dict]) -> str:
    return json.dumps(events, ensure_ascii=False, indent=1) + "\n"


def main(argv: list[str]) -> int:
    text = render(parse())
    if "--check" in argv:
        ok = OUT_PATH.exists() and OUT_PATH.read_text(encoding="utf-8") == text
        print("disasters.json is in sync with the CSV" if ok else "disasters.json is OUT OF SYNC: run tools/build_disasters_json.py")
        return 0 if ok else 1
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(text, encoding="utf-8")
    print(f"wrote {OUT_PATH.relative_to(ROOT)}: {len(json.loads(text))} events")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
