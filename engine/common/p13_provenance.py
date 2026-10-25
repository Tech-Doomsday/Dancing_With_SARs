# P13 — provenance: input hashes, parameter hash, code version, deterministic bundle id, assumptions
"""Who and what a bundle was built from.

`bundle_id` = blake2b over (contract version, tile, request, input digests, ancillary digest, parameter hash, code
version). Identical inputs + parameters + code give the same id, so `bundle_io.save_bundle` is idempotent and a
pipeline result can name the exact bundle it was computed from. Not in the id: wall-clock time, step timings, git commit
(the source digest already covers the code) and flags (they are derived from the same inputs).
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import numpy as np

from .config import repo_root
from .context import BuildContext, iso
from .scene_bundle import CONTRACT_VERSION


def _h(n: int) -> "hashlib._Hash":
    return hashlib.blake2b(digest_size=n)


@lru_cache(maxsize=1)
def code_version(n: int = 16) -> str:
    """Digest of every engine/common source file (so a logic change changes the bundle id)."""
    h = _h(n)
    root = Path(__file__).resolve().parent
    for p in sorted(root.glob("*.py")):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()


@lru_cache(maxsize=1)
def git_commit() -> str | None:
    try:
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_root(), capture_output=True, text=True, timeout=5)
        return r.stdout.strip() or None if r.returncode == 0 else None
    except Exception:
        return None


def _ancillary_digest(ctx: BuildContext, n: int) -> str:
    h = _h(n)
    a = ctx.ancillary
    if a is None:
        return h.hexdigest()
    for name in ("elevation_m", "landcover", "water_occurrence_pct"):
        arr = getattr(a, name).g80
        if arr is not None:
            h.update(name.encode())
            h.update(np.ascontiguousarray(arr).tobytes())
    for k in sorted(a.context):
        h.update(k.encode())
        if a.context[k].g80 is not None:
            h.update(np.ascontiguousarray(a.context[k].g80).tobytes())
    h.update(json.dumps({k: v for k, v in sorted(a.facts.items())}, sort_keys=True, default=str).encode())
    for k in sorted(a.status):
        s = a.status[k]
        h.update(f"{k}|{s.available}|{s.source}|{s.version}".encode())
    return h.hexdigest()


def run(ctx: BuildContext) -> None:
    n = ctx.cfg.provenance.hash_bytes
    recs = tuple(sorted(ctx.input_records, key=lambda r: r.granule_id))
    ih = _h(n)
    for r in recs:
        ih.update(f"{r.granule_id}|{r.product}|{r.sha}".encode())
    inputs_hash = ih.hexdigest()
    params_hash = _h(n)
    params_hash.update(ctx.cfg.chain_params_json().encode())
    params_hash = params_hash.hexdigest()
    rq = ctx.request
    req = json.dumps({"tile": ctx.tile.id, "as_of": iso(rq.as_of), "t0": iso(rq.t0), "t1": iso(rq.t1),
                      "event_window": [iso(x) if x is not None else None for x in rq.event_window] if rq.event_window else None,
                      "triggers": rq.triggers, "codes": sorted(rq.active_codes) if rq.active_codes is not None else None},
                     sort_keys=True, default=str)
    cv = code_version(n)
    h = _h(n)
    for part in (CONTRACT_VERSION, req, inputs_hash, _ancillary_digest(ctx, n), params_hash, cv):
        h.update(part.encode())
        h.update(b"\x00")
    ctx.bundle_id = f"{ctx.tile.id}-{h.hexdigest()}"
    ctx.provenance_parts = {
        "inputs": recs, "inputs_hash": inputs_hash, "params_hash": params_hash, "code_version": cv,
        "git_commit": git_commit(), "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "assumptions": tuple(ctx.assumptions)}
