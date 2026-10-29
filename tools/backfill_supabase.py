#!/usr/bin/env python3
"""Push verdicts the engine ALREADY computed into the Supabase cache.

    python tools/backfill_supabase.py --dry-run      # list what would be pushed
    python tools/backfill_supabase.py                # push (needs SUPABASE_URL + SUPABASE_SERVICE_KEY)
    python tools/backfill_supabase.py --fill-locations   # PATCH only the location columns (Phase 2)
    python tools/backfill_supabase.py --retier           # PATCH only the tier of already-cached rows (D15)

Source of truth is the engine's own runs store (<data>/verdicts.db: `runs` + `run_verdicts`, which
keep each finished run's full result). Per (tile, pipeline, bundle) the NEWEST successful run wins,
and the original run time is kept as `computed_at`. Same rules as the live hook: runs with custom
parameters and ERROR/PENDING results are skipped. Nothing is recomputed or invented.

--fill-locations: reads each cached bundle's bundle.json (the same source routes_bundles uses) for
tile.center_lonlat / tile.bbox_lonlat and PATCHes ONLY the location columns of existing rows by
primary key. It never re-writes a verdict. Requires the 0002 migration (center_lon/center_lat/
bbox_lonlat columns) to be applied first.

--retier: recalibrates the tier of already-cached rows from their stored status (adds the D15
"quiet" tier for NO_SIGNAL) and PATCHes ONLY tier. The verdict body is never touched.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.common.config import data_root  # noqa: E402
from engine.verdict import calibrate_all  # noqa: E402
from engine.verdict.cloud_cache import CloudCache  # noqa: E402


def newest_runs(db_path: Path) -> list[dict]:
    """[{run_id, bundle_id, tile, created_at, rows[]}] — the newest default-param run per bundle."""
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    runs = db.execute(
        "SELECT id, bundle_id, params, created_at FROM runs WHERE status='ok' ORDER BY created_at DESC").fetchall()
    chosen: dict[str, dict] = {}
    for r in runs:
        params = json.loads(r["params"] or "{}")
        if params.get("scan"):
            params = {}                     # a scan's audit key is not a parameter override
        if params:
            continue                        # custom parameters: never cached
        if r["bundle_id"] in chosen:
            continue                        # a newer run of this bundle already won
        results = [json.loads(v["result"]) for v in db.execute(
            "SELECT result FROM run_verdicts WHERE run_id=? ORDER BY pipeline", (r["id"],))]
        if not results:
            continue
        chosen[r["bundle_id"]] = {
            "run_id": r["id"], "bundle_id": r["bundle_id"], "created_at": r["created_at"],
            "tile": next((x.get("tile") for x in results if x.get("tile")), ""),
            "rows": calibrate_all([{**x, "detail": x.get("detail") or {}} for x in results]),
        }
    db.close()
    return list(chosen.values())


def bundle_location(bundles_dir: Path, bundle_id: str) -> dict | None:
    """tile.center_lonlat / bbox_lonlat from <bundles_dir>/<bundle_id>/bundle.json (the same
    source routes_bundles._meta reads). None when the bundle is not on disk."""
    meta_path = bundles_dir / bundle_id / "bundle.json"
    if not meta_path.exists():
        return None
    try:
        meta = json.loads(meta_path.read_text())
        tile = meta.get("tile") or {}
        center = tile.get("center_lonlat")
        bbox = tile.get("bbox_lonlat")
        if not center or not bbox:
            return None
        return {"center_lon": float(center[0]), "center_lat": float(center[1]),
                "bbox_lonlat": [float(v) for v in bbox]}
    except (OSError, json.JSONDecodeError, (TypeError, ValueError)):
        return None


def patch_rows(cache: CloudCache, records: list[dict], columns: list[str]) -> tuple[int, list[str]]:
    """PATCH only `columns` of existing rows, matched on the primary key. Returns (count, errors)."""
    url = f"{cache.url}/rest/v1/verdicts"
    key = cache._key
    written, errors = 0, []
    for rec in records:
        ident = urllib.parse.quote(
            f"tile=eq.{rec['tile']}&pipeline=eq.{rec['pipeline']}&bundle_id=eq.{rec['bundle_id']}",
            safe="=&.q_-")
        body = json.dumps({c: rec[c] for c in columns}, allow_nan=False, default=str).encode("utf-8")
        req = urllib.request.Request(f"{url}?{ident}", data=body, method="PATCH", headers={
            "apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json",
            "Prefer": "return=minimal"})
        try:
            with urllib.request.urlopen(req, timeout=cache.timeout_s) as resp:
                resp.read()
            written += 1
        except urllib.error.HTTPError as ex:
            errors.append(f"HTTP {ex.code} for {rec['tile']}/{rec['pipeline']}: "
                          f"{ex.read().decode('utf-8', 'replace')[:200]}")
        except urllib.error.URLError as ex:
            errors.append(f"unreachable for {rec['tile']}/{rec['pipeline']}: {ex.reason}")
    return written, errors


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", type=Path, help="engine data dir (default: the engine's configured one)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--fill-locations", action="store_true",
                    help="PATCH only center_lon/center_lat/bbox_lonlat of existing cached rows (0002)")
    ap.add_argument("--retier", action="store_true",
                    help="PATCH only the tier of existing cached rows from their stored status (D15)")
    a = ap.parse_args()
    db_path = (a.data_dir or data_root()) / "verdicts.db"
    if not db_path.exists():
        print(f"no verdicts.db at {db_path} — nothing to push")
        return 1
    cache = CloudCache.from_env()

    if a.fill_locations or a.retier:
        if not a.dry_run and not cache.enabled:
            print(f"cache disabled: {cache.disabled_reason}")
            return 1
        bundles_dir = (a.data_dir or data_root()) / "bundles"
        return _fill_columns(a, cache, db_path, bundles_dir)

    batches = newest_runs(db_path)
    print(f"{len(batches)} bundle(s) with a cacheable run in {db_path}")
    if not a.dry_run and not cache.enabled:
        print(f"cache disabled: {cache.disabled_reason}")
        return 1
    bad = 0
    for b in batches:
        names = ", ".join(f"{r['pipeline']}={r['status']}" for r in b["rows"])
        if a.dry_run:
            print(f"  would push  {b['tile'] or '?'}  {b['bundle_id']}  [{names}]")
            continue
        loc = bundle_location(bundles_dir_from(a), b["bundle_id"])
        rep = cache.write_run(b["rows"], run_id=b["run_id"], bundle_id=b["bundle_id"], tile=b["tile"],
                              computed_at=b["created_at"], location=loc)
        bad += bool(rep["failed"])
        print(f"  {'FAILED' if rep['failed'] else 'ok    '}  {b['tile'] or '?'}  {b['bundle_id']}  "
              f"written={rep['written']} skipped={len(rep['skipped'])} {'; '.join(rep['failed'])}")
    return 1 if bad else 0


def bundles_dir_from(a) -> Path:
    return (a.data_dir or data_root()) / "bundles"


def _fill_columns(a, cache: CloudCache, db_path: Path, bundles_dir: Path) -> int:
    """--fill-locations / --retier: PATCH-only maintenance of EXISTING rows (never re-writes)."""
    if a.fill_locations and a.retier:
        print("pick one: --fill-locations or --retier (they PATCH different columns)")
        return 1
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    if not _table_exists(db_path, "verdicts"):
        db.close()
        print("no canonical verdicts table in verdicts.db yet — nothing to patch")
        return 0
    cached = db.execute("SELECT tile, pipeline, bundle_id, status FROM verdicts").fetchall()
    db.close()
    if not cached:
        print("no cached rows found (is the canonical verdict table empty?)")
        return 0
    records: list[dict] = []
    missing = 0
    for row in cached:
        rec = {"tile": row["tile"], "pipeline": row["pipeline"], "bundle_id": row["bundle_id"]}
        if a.fill_locations:
            loc = bundle_location(bundles_dir, row["bundle_id"])
            if loc is None:
                missing += 1
                if not a.dry_run:
                    continue
                print(f"  no bundle.json for {row['bundle_id']} — cannot fill locations for "
                      f"{row['tile']}/{row['pipeline']}")
                continue
            rec.update(loc)
        if a.retier:
            from engine.verdict.calibrate import tier_of
            rec["tier"] = tier_of(row["status"])
        records.append(rec)
    if a.dry_run:
        for rec in records:
            cols = [c for c in ("center_lon", "center_lat", "bbox_lonlat", "tier") if c in rec]
            print(f"  would patch  {rec['tile']}/{rec['pipeline']}  ({', '.join(cols)})")
        print(f"{len(records)} row(s) patchable; {missing} row(s) without a local bundle.json")
        return 0
    columns = (["center_lon", "center_lat", "bbox_lonlat"] if a.fill_locations else ["tier"])
    written, errors = patch_rows(cache, records, columns)
    for e in errors:
        print(f"  FAILED  {e}")
    print(f"patched {written} of {len(records)} row(s) ({', '.join(columns)}); "
          f"{missing} row(s) skipped without a local bundle.json")
    return 1 if errors else 0


def _table_exists(db_path: Path, name: str) -> bool:
    db = sqlite3.connect(db_path)
    try:
        row = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
        return row is not None
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
