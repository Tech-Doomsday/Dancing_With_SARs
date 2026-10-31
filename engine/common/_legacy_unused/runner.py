"""Orchestration: run P01..P13 in order and return a SceneBundle.

Two entry points:
  * ``build_scene_bundle`` - raises ``PreprocError`` on failure (scripts, tests).
  * ``run_preprocessing``  - returns a ``PreprocResult`` with status, failing
    stage and full stage log (scheduler / registry; nothing fails silently).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, List, Mapping, Optional

from ._types import PreprocError
from .config import PreprocConfig
from .scene_bundle import SceneBundle
from .state import BundleBuilder, PreprocInput, StageRecord
from . import (p01_granule_resolution, p02_clip, p03_decode_gate, p04_grid_harmonization, p05_geometry,
               p06_phase_to_los, p07_correction_audit, p08_backscatter_prep, p09_ancillary_align,
               p10_temporal_stack, p11_stable_mask, p12_tile_routing, p13_provenance)

STAGES = (p01_granule_resolution, p02_clip, p03_decode_gate, p04_grid_harmonization, p05_geometry,
          p06_phase_to_los, p07_correction_audit, p08_backscatter_prep, p09_ancillary_align,
          p10_temporal_stack, p11_stable_mask, p12_tile_routing, p13_provenance)


@dataclass
class PreprocResult:
    status: str                       # "ok" | "failed"
    bundle: Optional[SceneBundle] = None
    failed_stage: Optional[str] = None
    error: Optional[str] = None
    stage_log: List[StageRecord] = field(default_factory=list)


def run_preprocessing(inp: PreprocInput, cfg: Optional[PreprocConfig] = None,
                      requirements: Optional[Mapping[str, Mapping[str, Any]]] = None) -> PreprocResult:
    cfg = cfg or PreprocConfig()
    state = BundleBuilder(inp=inp, cfg=cfg)
    for mod in STAGES:
        t0 = time.perf_counter()
        rec = StageRecord(stage=mod.STAGE, status="ok")
        state.stage_log.append(rec)
        try:
            out = mod.run(state, requirements) if mod is p12_tile_routing else mod.run(state)
        except PreprocError as exc:
            rec.status, rec.seconds = "failed", time.perf_counter() - t0
            rec.notes.append(f"{type(exc).__name__}: {exc}")
            return PreprocResult("failed", None, mod.STAGE, f"{type(exc).__name__}: {exc}", state.stage_log)
        rec.seconds = time.perf_counter() - t0
        if isinstance(out, dict):
            if out.get("skipped"):
                rec.status = "skipped"
            rec.metrics = {k: v for k, v in out.items() if k != "notes"}
            rec.notes.extend(out.get("notes", []))
    return PreprocResult("ok", state.bundle, None, None, state.stage_log)


def build_scene_bundle(inp: PreprocInput, cfg: Optional[PreprocConfig] = None,
                       requirements: Optional[Mapping[str, Mapping[str, Any]]] = None) -> SceneBundle:
    res = run_preprocessing(inp, cfg, requirements)
    if res.status != "ok":
        raise PreprocError(f"preprocessing failed at {res.failed_stage}: {res.error}")
    return res.bundle  # type: ignore[return-value]
