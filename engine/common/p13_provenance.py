"""P13 - provenance: checksums + param hash -> deterministic bundle id -> SceneBundle.

bundle_id depends only on (tile, schema, params, granule ids/versions, raw input
checksums), so it is stable across machines and numpy versions. Output-layer
checksums are recorded separately for integrity checks, not folded into the id.
"""
from __future__ import annotations

import hashlib
import json
import platform
from datetime import datetime, timezone
from typing import Any, Dict

import numpy as np

from .scene_bundle import SCHEMA_VERSION, CORE_LAYERS, SceneBundle
from .state import BundleBuilder

STAGE = "P13"
__version__ = "1.0.0"


def checksum(a: np.ndarray) -> str:
    a = np.ascontiguousarray(a)
    h = hashlib.sha256()
    h.update(str(a.dtype).encode())
    h.update(str(a.shape).encode())
    h.update(a.tobytes())
    return h.hexdigest()[:16]


def _input_checksums(state: BundleBuilder) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for tag, clip in (("gunw", state.clip), ("gcov", state.bs_clip)):
        if clip is None:
            continue
        for name in sorted(clip.layers):
            out[f"{tag}:{clip.granule_id}:{name}"] = checksum(clip.layers[name].data)
    for name in sorted(state.inp.ancillary):
        out[f"ancillary:{name}"] = checksum(state.inp.ancillary[name].data)
    return out


def run(state: BundleBuilder) -> Dict:
    res, cfg = state.resolution, state.cfg
    missing = [n for n in CORE_LAYERS if n not in state.layers]
    if missing:
        raise RuntimeError(f"P13: core layers missing at freeze time: {missing}")
    inputs = _input_checksums(state)
    granules = {"current": res.current.granule_id,
                "history": [g.granule_id for g in res.history],
                "gcov_current": res.gcov_current.granule_id if res.gcov_current else None,
                "gcov_history": [g.granule_id for g in res.gcov_history],
                "versions": {g.granule_id: g.processing_version
                             for g in res.all_pairs + ((res.gcov_current,) if res.gcov_current else ()) + res.gcov_history}}
    id_blob = json.dumps({"tile": state.inp.tile.tile_id, "bbox": state.inp.tile.bbox, "schema": SCHEMA_VERSION,
                          "params": cfg.param_hash(), "granules": granules, "inputs": inputs,
                          "grids": {k: v.to_dict() for k, v in state.grids.items()}}, sort_keys=True, default=str)
    bundle_id = "sb_" + hashlib.sha256(id_blob.encode()).hexdigest()[:20]
    prov: Dict[str, Any] = {
        "engine_common_version": __version__, "schema_version": SCHEMA_VERSION, "param_hash": cfg.param_hash(),
        "config": cfg.to_dict(), "granules": granules, "input_checksums": inputs,
        "layer_checksums": {k: checksum(v) for k, v in sorted(state.layers.items())},
        "graph": res.graph, "dropped_granules": [list(x) for x in res.dropped],
        "stage_log": [{"stage": s.stage, "status": s.status, "seconds": round(s.seconds, 4),
                       "notes": s.notes, "metrics": s.metrics} for s in state.stage_log],
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "python": platform.python_version(), "numpy": np.__version__,
    }
    cur = res.current
    state.bundle = SceneBundle(
        bundle_id=bundle_id, tile=state.inp.tile, sensor=cur.sensor, product=cur.product,
        group={"sensor": cur.sensor, "track": cur.track, "frame": cur.frame, "direction": cur.direction},
        epoch={"granule_id": cur.granule_id, "ref_time": cur.ref_time.isoformat(),
               "sec_time": cur.sec_time.isoformat() if cur.sec_time else None,
               "gcov_granule_id": res.gcov_current.granule_id if res.gcov_current else None,
               "processing_version": cur.processing_version},
        grids=dict(state.grids), layers=state.layers, layer_grid=state.layer_grid,
        meta=dict(state.meta), stack=state.stack,
        corrections=state.audit, quality=state.quality, routing=state.routing,
        flags=tuple(sorted(state.flags)), cold_start=bool(state.stack.cold_start),
        degraded_mode=state.degraded_mode, provenance=prov)
    return {"bundle_id": bundle_id, "n_layers": len(state.layers), "flags": sorted(state.flags)}
