"""P09 - ancillary alignment: DEM -> slope/aspect/HAND (+ water) onto the grids.

Terrain is computed on a 1-px padded grid and cropped, so tile edges are not
biased. HAND here is a local-minimum PROXY (flagged HAND_IS_PROXY); inject a real
routing-based HAND later via ``hand_provider`` without touching anything else.
"""
from __future__ import annotations

from typing import Callable, Dict, Optional

import numpy as np

from . import _terrain
from ._raster import crop_to, resample
from ._types import Grid
from .p07_correction_audit import elevation_audit
from .state import F_HAND_PROXY, F_NO_DEM, F_NO_LS, F_NO_WATER, BundleBuilder

STAGE = "P09"

HandProvider = Callable[[np.ndarray, Grid], np.ndarray]
hand_provider: Optional[HandProvider] = None   # module-level hook; None -> proxy


def _terrain_on(state: BundleBuilder, dem_raster, grid: Grid) -> Dict[str, np.ndarray]:
    pad = grid.padded(1)
    dem_p = resample(dem_raster.data, dem_raster.grid, pad, "mean")
    slope, aspect = _terrain.slope_aspect(dem_p, grid.dx, grid.dy)
    return {"dem": crop_to(dem_p, pad, grid), "slope": crop_to(slope, pad, grid),
            "aspect": crop_to(aspect, pad, grid), "dem_padded": dem_p}


def run(state: BundleBuilder) -> Dict:
    anc = state.inp.ancillary
    cfg = state.cfg
    g80, g20 = state.grids["G80"], state.grids["G20"]
    metrics: Dict = {}
    dem = anc.get("dem")
    if dem is None:
        state.flag(F_NO_DEM)
        metrics["dem"] = "missing"
    else:
        t80 = _terrain_on(state, dem, g80)
        state.put("dem_m", t80["dem"])
        state.put("slope_deg", t80["slope"])
        state.put("aspect_deg", t80["aspect"])
        if hand_provider is not None:
            hand = hand_provider(t80["dem"], g80).astype(np.float32)
            state.meta["hand_method"] = "external_provider"
        else:
            pad = g80.padded(1)
            hand = crop_to(_terrain.hand_proxy(t80["dem_padded"], max(1, int(round(cfg.hand_radius_m / g80.dx)))), pad, g80)
            state.meta["hand_method"] = "local_min_proxy"
            state.meta["hand_radius_m"] = cfg.hand_radius_m
            state.flag(F_HAND_PROXY)
        state.put("hand_m", hand)
        if state.needs_g20:
            t20 = _terrain_on(state, dem, g20)
            state.put("dem_g20_m", t20["dem"], "G20")
            state.put("slope_g20_deg", t20["slope"], "G20")
            state.put("aspect_g20_deg", t20["aspect"], "G20")
            state.put("hand_g20_m", resample(hand, g80, g20, "bilinear"), "G20")
        # refine layover/shadow from the DEM only when the product did not provide it
        if state.meta.get("layover_shadow_source") == "none" and {"los_e", "los_n", "los_u"} <= set(state.layers):
            ls = _terrain.layover_shadow_from_dem(t80["dem"], g80.dx, g80.dy, state.layers["los_e"],
                                                  state.layers["los_n"], state.layers["los_u"])
            state.layers["layover_shadow"] = ls
            state.meta["layover_shadow_source"] = "dem_derived"
            state.flags.discard(F_NO_LS)
        elevation_audit(state)
        metrics["dem"] = "ok"
        metrics["hand_method"] = state.meta["hand_method"]

    water = anc.get("water")
    if water is None:
        state.flag(F_NO_WATER)
        state.put("water", np.zeros(g80.shape, dtype=bool))
        state.meta["water_source"] = "none"
    else:
        frac = resample(water.data.astype(np.float32), water.grid, g80, "mean")
        state.put("water", np.nan_to_num(frac, nan=0.0) >= cfg.water_threshold)
        state.meta["water_source"] = "ancillary"
        if state.needs_g20:
            f20 = resample(water.data.astype(np.float32), water.grid, g20, "mean")
            state.put("water_g20", np.nan_to_num(f20, nan=0.0) >= cfg.water_threshold, "G20")
    return metrics
