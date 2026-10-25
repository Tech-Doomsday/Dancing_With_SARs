"""P04 - grid harmonization onto canonical grids G80 (GUNW) and G20 (GCOV)."""
from __future__ import annotations

from typing import Dict, Mapping

import numpy as np

from ._raster import CATEGORICAL, resample
from ._types import Grid, InsufficientInputError, Raster
from .state import BundleBuilder

STAGE = "P04"


def harmonize(decoded: Mapping[str, Raster], target: Grid) -> Dict[str, np.ndarray]:
    out: Dict[str, np.ndarray] = {}
    for name, r in decoded.items():
        if name in CATEGORICAL:
            arr = resample(r.data, r.grid, target, "nearest", 0)
        else:
            arr = resample(r.data, r.grid, target, "mean")
        out[name] = arr
    return out


def run(state: BundleBuilder) -> Dict:
    g80, g20 = state.grids["G80"], state.grids["G20"]
    state.work = harmonize(state.decoded, g80)
    metrics: Dict = {"layers_g80": sorted(state.work)}
    for req in ("unw_phase", "coherence"):
        if req in state.work and not np.isfinite(state.work[req]).any():
            raise InsufficientInputError(
                f"layer '{req}' has no valid pixels on {g80.name}: the clip does not overlap tile "
                f"{state.inp.tile.tile_id} or is entirely masked")
    if state.decoded_bs:
        state.work20 = harmonize(state.decoded_bs, g20)
        metrics["layers_g20"] = sorted(state.work20)
    state.put("coherence", state.work["coherence"], "G80")
    state.put("conncomp", state.work["conncomp"].astype(np.int32), "G80")
    return metrics
