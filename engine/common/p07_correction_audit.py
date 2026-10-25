# P07 — record applied tropo/iono/tide correction flags, never apply silently
"""What could be removed from the LOS signal, recorded - never applied.

For every pair this step converts each correction layer the product provides (ionosphere phase screen,
hydrostatic and wet troposphere, solid-earth tide) into the same units and sign as `los_m`, i.e. "how much
apparent displacement does this effect contribute", and stores it as a diagnostic layer. The audit also states
whether the product generator already applied it (an assumption from config: the clip carries no processing
metadata) and always `applied_by_engine=False`.

`residual_los_m` = `los_m` minus every available diagnostic the product did not apply. It is offered as a layer
for pipelines that want it (land_subsidence uses it); `los_m` itself is never altered, so no pipeline can receive
a silently corrected field.
"""
from __future__ import annotations

import math

import numpy as np

from .context import BuildContext
from .p05_geometry import collapse_height
from .scene_bundle import CorrectionAudit, CorrectionItem
from .terrain_set import get_terrain

# correction name -> (layer role, is it a height cube?)
SOURCES = {
    "iono": ("iono_phase", False),
    "tropo_hydrostatic": ("tropo_hydro_phase", True),
    "tropo_wet": ("tropo_wet_phase", True),
    "solid_earth_tide": ("tide_phase", True),
}


def run(ctx: BuildContext) -> None:
    r = ctx.cfg.radar
    k = r.phase_sign * r.wavelength_m / (4.0 * math.pi)
    flags = r.corrections_applied_by_product
    ctx.assume("correction screens (iono / tropo / tide) are interferometric phase in radians and are NOT already applied "
               "to unwrappedPhase (config radar.corrections_applied_by_product) - unverified on real NISAR data")
    terrain = get_terrain(ctx) if any(w.units for w in ctx.ifg.values()) else None
    for key, w in ctx.ifg.items():
        if w.los_m is None:
            continue
        P = w.los_m.shape[0]
        shape = w.los_m.shape[1:]
        items = {}
        residual = w.los_m.copy()
        used = []
        for name, (role, cube) in SOURCES.items():
            layer = np.full((P,) + shape, np.nan, np.float32)
            have = False
            for i, u in enumerate(w.units):
                arr = next((a for (rr, _p), a in u.layers.items() if rr == role), None)
                if arr is None:
                    continue
                if cube and arr.ndim == 3:
                    at = terrain.elevation["G80"] if terrain is not None and terrain.available else r_fallback(ctx)
                    arr = collapse_height(arr, u.heights, at)
                layer[i] = (w.sign[i] * k) * arr
                have = True
            applied = bool(flags.get(name, False))
            if have:
                rms = tuple(float(np.sqrt(np.nanmean(layer[i] ** 2))) if np.isfinite(layer[i]).any() else float("nan")
                            for i in range(P))
                items[name] = CorrectionItem(name, True, applied, False, layer, rms,
                                             "diagnostic only: not removed from los_m")
                if not applied:
                    used.append(name)
                    residual = residual - np.nan_to_num(layer, nan=0.0)
            else:
                items[name] = CorrectionItem(name, False, None, False, None, (),
                                             "layer absent from the retrieved clips")
        missing = [n for n, it in items.items() if not it.available]
        if missing:
            ctx.flag("corrections_missing", "info", "P07", f"{key}: no diagnostic layer for {', '.join(missing)}", stack=key,
                     missing=missing)
        w.corrections = CorrectionAudit(items, residual.astype(np.float32) if used else None, tuple(used),
                                        "residual = los_m - sum(diagnostics not applied by the product); los_m is untouched")


def r_fallback(ctx: BuildContext) -> float:
    return ctx.cfg.geometry.fallback_height_m
