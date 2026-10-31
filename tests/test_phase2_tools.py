"""Phase 2 tests: location columns in the cloud cache, backfill --fill-locations/--retier, and
the feature_area CLI's engine-resolved tile rules (offline: no Postgres, no network)."""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

from engine.verdict.cloud_cache import to_record
from tools.backfill_supabase import bundle_location, newest_runs, patch_rows
from tools.feature_area import resolve_tile


def _row(**kw):
    base = {"pipeline": "earthquake_coseismic", "tile": "11SLT66", "epoch": "2026-08-06",
            "bundle_id": "b1", "status": "SUPPORTED", "tier": "supported", "raw_p": 0.7,
            "summary": "s", "evidence": [], "warnings": [], "detail": {"layers": {}}}
    base.update(kw)
    return base


def test_to_record_carries_the_location_columns():
    loc = {"center_lon": -118.25, "center_lat": 34.05, "bbox_lonlat": [-118.3, 34.0, -118.2, 34.1]}
    rec = to_record(_row(), run_id="r1", bundle_id="b1", tile="11SLT66", now="2026-08-20T00:00:00Z",
                    location=loc)
    assert rec["center_lon"] == -118.25 and rec["center_lat"] == 34.05
    assert rec["bbox_lonlat"] == loc["bbox_lonlat"]
    assert "layers" not in rec["result"]["detail"]
    # records without location still cache (pre-0002 paths keep working)
    rec2 = to_record(_row(), run_id="r1", bundle_id="b1", tile="11SLT66", now="2026-08-20T00:00:00Z")
    assert "center_lon" not in rec2 and "center_lat" not in rec2


def test_newest_runs_ignores_the_scan_audit_key(tmp_path):
    """A scan's runs.params carries {"scan": {...}} for audit — that is NOT a parameter override
    and must not block the cache write path in the backfill."""
    db = tmp_path / "verdicts.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE runs(id TEXT PRIMARY KEY, created_at TEXT, bundle_id TEXT, tile TEXT,"
        " pipelines TEXT, params TEXT, status TEXT, error TEXT, duration_s REAL, kind TEXT DEFAULT 'bundle');"
        "CREATE TABLE run_verdicts(run_id TEXT, pipeline TEXT, tile TEXT, epoch TEXT, raw_p REAL,"
        " status TEXT, result TEXT, PRIMARY KEY(run_id, pipeline));")
    result = json.dumps({"pipeline": "earthquake_coseismic", "tile": "11SLT66", "status": "NO_SIGNAL"})
    conn.execute("INSERT INTO runs VALUES ('run-scan','2026-08-20T00:00:00Z','b-scan','11SLT66','[]',?, 'ok',NULL,1.0,'scan')",
                 (json.dumps({"scan": {"lon": -118.5, "lat": 34.0, "as_of": None, "lookback_days": None}}),))
    conn.execute("INSERT INTO run_verdicts VALUES ('run-scan','earthquake_coseismic','11SLT66',NULL,NULL,'NO_SIGNAL',?)",
                 (result,))
    conn.commit()
    conn.close()
    batches = newest_runs(db)
    assert len(batches) == 1 and batches[0]["bundle_id"] == "b-scan"
    assert batches[0]["rows"][0]["tier"] == "quiet"        # D15 tier flows into the backfill


def test_bundle_location_reads_bundle_json(tmp_path):
    from tests.common_fixtures import default_tile
    tile = default_tile()
    bdir = tmp_path / "bundles" / "b-x"
    bdir.mkdir(parents=True)
    (bdir / "bundle.json").write_text(json.dumps({
        "bundle_id": "b-x",
        "tile": {"id": tile.id, "epsg": int(tile.epsg),
                 "center_lonlat": [float(v) for v in tile.center],
                 "bbox_lonlat": [float(v) for v in tile.bbox(0.0)]}}))
    loc = bundle_location(tmp_path / "bundles", "b-x")
    assert loc and -180 <= loc["center_lon"] <= 180 and -90 <= loc["center_lat"] <= 90
    assert len(loc["bbox_lonlat"]) == 4
    assert bundle_location(tmp_path / "bundles", "missing") is None


def test_patch_rows_hits_the_right_primary_key(monkeypatch):
    """The PATCH targets (tile, pipeline, bundle_id) and sends ONLY the requested columns."""
    captured: dict = {}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b""

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["body"] = json.loads(req.data.decode())
        return FakeResp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    cache = type("C", (), {"url": "http://127.0.0.1:1", "_key": "k", "timeout_s": 5})()
    rec = {"tile": "11SLT66", "pipeline": "earthquake_coseismic", "bundle_id": "b1",
           "center_lon": 1.0, "center_lat": 2.0, "bbox_lonlat": [0, 0, 1, 1], "tier": "quiet"}
    written, errors = patch_rows(cache, [rec], ["center_lon", "center_lat", "bbox_lonlat"])
    assert written == 1 and not errors
    assert captured["method"] == "PATCH"
    assert "tile=eq.11SLT66" in captured["url"] and "bundle_id=eq.b1" in captured["url"]
    assert set(captured["body"]) == {"center_lon", "center_lat", "bbox_lonlat"}


# --------------------------------------------------------------------------- feature_area CLI
def test_feature_area_resolves_tiles_through_the_engine():
    slug, lon, lat = resolve_tile(type("A", (), {"tile": "11SLT66", "lon": None, "lat": None})())
    assert slug == "11SLT66" and -180 < lon < -118 and 33 < lat < 35
    # a point resolves to the containing tile: the engine's own answer for (-118.25, 34.05)
    # (verified in the S3 spike and by tests/test_mgrs.py's reference-library cross-check)
    slug2, lon2, _ = resolve_tile(type("A", (), {"tile": None, "lon": -118.25, "lat": 34.05})())
    assert slug2 == "11SLT86"
    # and the fixtures' own centre resolves back to the fixture tile
    from tests.common_fixtures import default_tile
    tile = default_tile()
    slug3, _, _ = resolve_tile(type("A", (), {"tile": None, "lon": float(tile.center[0]),
                                              "lat": float(tile.center[1])})())
    assert slug3 == tile.id


def test_feature_area_refuses_unknown_tiles_and_ranges():
    with pytest.raises(SystemExit):
        resolve_tile(type("A", (), {"tile": "99XYZ99", "lon": None, "lat": None})())
    with pytest.raises(SystemExit):
        resolve_tile(type("A", (), {"tile": None, "lon": 200.0, "lat": 0.0})())
    with pytest.raises(SystemExit, match="either --tile"):
        resolve_tile(type("A", (), {"tile": None, "lon": None, "lat": None})())
