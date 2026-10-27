"""Replay a stored run offline — plan §13 (data/runs/<run_id>/ artifacts).

    python tools/replay_run.py <run_id> [--data-dir PATH]

Reads `data/runs/<run_id>/params.json`, loads the bundle fresh from the bundle
store, re-runs the recorded pipelines through the REAL registry path and prints
each verdict next to what the run originally reported. No API involved.

This is the audit path: what the run did must be reproducible from what it wrote.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    ap = argparse.ArgumentParser(description="Replay a stored engine run offline (plan 13).")
    ap.add_argument("run_id")
    ap.add_argument("--data-dir", default=None, help="engine data root (default: engine.common.config.data_root)")
    ap.add_argument("--json", action="store_true", help="print machine-readable JSON")
    args = ap.parse_args()

    if args.data_dir:
        data_dir = Path(args.data_dir)
    else:
        from engine.common.config import data_root
        data_dir = Path(data_root())

    run_dir = data_dir / "runs" / args.run_id
    params_path = run_dir / "params.json"
    if not params_path.exists():
        print(f"no replay artifacts at {run_dir} — run the API once with this run_id, "
              f"or check the data dir (--data-dir)", file=sys.stderr)
        return 2
    recorded = json.loads(params_path.read_text())

    from engine.common.bundle_io import load_bundle
    from engine.registry import Registry

    bundle_id = recorded["bundle_id"]
    bundle_path = data_dir / "bundles" / bundle_id
    if not (bundle_path / "bundle.json").exists():
        print(f"bundle {bundle_id} not found under {data_dir / 'bundles'}", file=sys.stderr)
        return 2

    bundle = load_bundle(bundle_path, mmap=True)
    if recorded.get("params"):
        from engine.api.jobs import _apply_params
        bundle = _apply_params(bundle, recorded["params"])

    registry = Registry(bundles_dir=data_dir / "bundles")
    tile_run = registry.run_tile(bundle.tile, retriever=None, bundle=bundle,
                                 names=recorded.get("pipelines") or None, workers=0)

    rows = []
    for name, res in tile_run.results.items():
        rows.append({"pipeline": name, "status_now": res.status,
                     "status_recorded": _recorded_status(args.run_id, name, run_dir),
                     "summary": res.summary, "runtime_s": round(res.runtime_s, 2)})
    if args.json:
        print(json.dumps({"run_id": args.run_id, "bundle_id": bundle_id, "replay": rows}, indent=2))
    else:
        print(f"replay of {args.run_id} (bundle {bundle_id})")
        for r in rows:
            marker = "OK" if r["status_now"] == r["status_recorded"] else "DIFF"
            print(f"  [{marker}] {r['pipeline']}: now={r['status_now']} "
                  f"recorded={r['status_recorded'] or '?'}  {r['summary']}")
        print("statuses must match the recorded run — the engine is deterministic by bundle "
              "(seeds derive from bundle_id); a DIFF means the pipeline changed since the run.")
    return 0


def _recorded_status(run_id: str, pipeline: str, run_dir: Path) -> str | None:
    result_path = run_dir / "result.json"
    if not result_path.exists():
        return None
    try:
        recorded = json.loads(result_path.read_text())
    except Exception:
        return None
    for res in recorded.get("results", []):
        if res.get("pipeline") == pipeline:
            return res.get("status")
    return None


if __name__ == "__main__":
    raise SystemExit(main())
