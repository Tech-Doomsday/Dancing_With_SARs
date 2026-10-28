#!/usr/bin/env python3
"""Push verdicts the engine ALREADY computed into the Supabase cache.

    python tools/backfill_supabase.py --dry-run      # list what would be pushed
    python tools/backfill_supabase.py                # push (needs SUPABASE_URL + SUPABASE_SERVICE_KEY)

Source of truth is the engine's own runs store (<data>/verdicts.db: `runs` + `run_verdicts`, which
keep each finished run's full result). Per (tile, pipeline, bundle) the NEWEST successful run wins,
and the original run time is kept as `computed_at`. Same rules as the live hook: runs with custom
parameters and ERROR/PENDING results are skipped. Nothing is recomputed or invented.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
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
        if json.loads(r["params"] or "{}"):
            continue                                    # custom parameters: never cached
        if r["bundle_id"] in chosen:
            continue                                    # a newer run of this bundle already won
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", type=Path, help="engine data dir (default: the engine's configured one)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    db_path = (a.data_dir or data_root()) / "verdicts.db"
    if not db_path.exists():
        print(f"no verdicts.db at {db_path} — nothing to push")
        return 1
    batches = newest_runs(db_path)
    cache = CloudCache.from_env()
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
        rep = cache.write_run(b["rows"], run_id=b["run_id"], bundle_id=b["bundle_id"], tile=b["tile"],
                              computed_at=b["created_at"])
        bad += bool(rep["failed"])
        print(f"  {'FAILED' if rep['failed'] else 'ok    '}  {b['tile'] or '?'}  {b['bundle_id']}  "
              f"written={rep['written']} skipped={len(rep['skipped'])} {'; '.join(rep['failed'])}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
