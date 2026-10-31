"""Mutable build state threaded through P01..P13, plus the public input type.

Stages never talk to each other except through ``BundleBuilder``; P13 freezes it
into a ``SceneBundle``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set

import numpy as np

from ._types import BBox, Clip, ClipSource, GranuleRecord, Grid, Raster, TileSpec
from .config import PreprocConfig

# ---- flag vocabulary (strings, stable, documented in README) -----------------
F_SINGLE_PAIR = "SINGLE_PAIR_ONLY"
F_DISCONNECTED = "PAIR_NETWORK_DISCONNECTED"
F_BETA = "BETA_DATA_IN_STACK"
F_DEGRADED = "DEGRADED_INPUT"
F_NO_GEOMETRY = "GEOMETRY_3D_MISSING"
F_INC_MISMATCH = "INCIDENCE_INCONSISTENT"
F_NO_LS = "LAYOVER_SHADOW_NOT_AVAILABLE"
F_NO_DEM = "DEM_MISSING"
F_NO_BACKSCATTER = "BACKSCATTER_UNAVAILABLE"
F_HAND_PROXY = "HAND_IS_PROXY"
F_NO_WATER = "WATER_MASK_MISSING"
F_COLD = "COLD_START"
F_LOW_QUALITY = "TILE_QUALITY_LOW"
F_HISTORY_SKIPPED = "HISTORY_PAIRS_SKIPPED"


@dataclass
class PreprocInput:
    """Everything the chain needs, supplied by the caller (registry / scheduler).

    ``ancillary`` holds already-fetched rasters keyed "dem" (metres, required for
    terrain) and optionally "water" (1 = water, or 0..1 fraction).
    """

    tile: TileSpec
    candidates: Sequence[GranuleRecord]
    clip_source: ClipSource
    target: Optional[GranuleRecord] = None
    ancillary: Mapping[str, Raster] = field(default_factory=dict)
    degraded_mode: bool = False


@dataclass
class StageRecord:
    stage: str
    status: str                      # ok | skipped | failed
    seconds: float = 0.0
    notes: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BundleBuilder:
    inp: PreprocInput
    cfg: PreprocConfig
    # P01
    resolution: Any = None
    # P02
    grids: Dict[str, Grid] = field(default_factory=dict)
    clip_bbox: Optional[BBox] = None
    clip: Optional[Clip] = None
    bs_clip: Optional[Clip] = None
    # P03 / P04 working sets
    decoded: Dict[str, Raster] = field(default_factory=dict)
    decoded_bs: Dict[str, Raster] = field(default_factory=dict)
    work: Dict[str, np.ndarray] = field(default_factory=dict)      # G80 intermediates
    work20: Dict[str, np.ndarray] = field(default_factory=dict)    # G20 intermediates
    # accumulated outputs
    layers: Dict[str, np.ndarray] = field(default_factory=dict)
    layer_grid: Dict[str, str] = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict)
    audit: Dict[str, Any] = field(default_factory=dict)
    stack: Any = None
    quality: Dict[str, Any] = field(default_factory=dict)
    routing: Dict[str, Any] = field(default_factory=dict)
    flags: Set[str] = field(default_factory=set)
    stage_log: List[StageRecord] = field(default_factory=list)
    degraded_mode: bool = False
    bundle: Any = None

    def put(self, name: str, arr: np.ndarray, grid_name: str = "G80") -> None:
        g = self.grids[grid_name]
        if tuple(arr.shape) != g.shape:
            raise ValueError(f"layer {name}: shape {arr.shape} != {grid_name} {g.shape}")
        self.layers[name] = arr
        self.layer_grid[name] = grid_name

    def flag(self, code: str) -> None:
        self.flags.add(code)

    @property
    def needs_g20(self) -> bool:
        return bool(self.cfg.always_build_g20 or self.bs_clip is not None)
