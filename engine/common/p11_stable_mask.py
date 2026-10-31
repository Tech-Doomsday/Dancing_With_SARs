# P11 — stable-ground mask (coherent, gentle slope, not water, no change vs baseline) for referencing and detrending
"""A boolean "this ground did not move or change" layer per stack, on both grids, with its criteria spelled out.

Criteria (each a boolean layer in `StableMask.components`; the mask is their AND):
    coherence   mean coherence over the stack's pairs >= `stable.coherence_min`               (G80 -> G20 by index map)
    slope       DEM slope < `stable.slope_max_deg`                                            (needs a DEM)
    not_water   not WorldCover water and water occurrence < `stable.water_occurrence_max_pct` (needs a layer)
    no_change   every polarization's newest backscatter within `nochange_k` robust sigma of baseline median
                                                                                                (needs a warm baseline)
A criterion that cannot be evaluated is listed under `missing`; it is never assumed true. A pixel whose newest value is
missing fails `no_change` (not verifiable). When no criterion can be evaluated at all the stack gets no mask
(`StableMask.mask` is empty), so pipelines do not mistake "unknown" for "stable".
"""
from __future__ import annotations

import warnings

import numpy as np

from .ancillary import WC_WATER
from .context import BuildContext
from .frozen import Dual, FrozenDict
from .scene_bundle import StableMask


def _up(ctx: BuildContext, a80: np.ndarray) -> np.ndarray:
    return a80.ravel()[ctx.grids.index_map_20_to_80]


def run(ctx: BuildContext) -> None:
    cfg = ctx.cfg.stable
    anc = ctx.ancillary
    for key in ctx.stack_keys:
        comps: dict = {}
        missing: list = []

        # -- coherence ------------------------------------------------------------------------
        w = ctx.ifg.get(key)
        if w is not None and w.coherence is not None:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                m80 = np.nanmean(w.coherence, axis=0)
            c80 = np.isfinite(m80) & (m80 >= cfg.coherence_min)
            comps["coherence"] = Dual(c80, _up(ctx, c80))
        else:
            missing.append("coherence")

        # -- slope ----------------------------------------------------------------------------
        if anc is not None and anc.slope_deg.available:
            s = anc.slope_deg
            comps["slope"] = Dual(np.isfinite(s.g80) & (s.g80 < cfg.slope_max_deg),
                                  np.isfinite(s.g20) & (s.g20 < cfg.slope_max_deg))
        else:
            missing.append("slope")

        # -- water ----------------------------------------------------------------------------
        if anc is not None and (anc.landcover.available or anc.water_occurrence_pct.available):
            parts = {}
            for g in ("G80", "G20"):
                ok = np.ones(ctx.grids[g].shape, bool)
                if anc.landcover[g] is not None:
                    ok &= anc.landcover[g] != WC_WATER
                wo = anc.water_occurrence_pct[g]
                if wo is not None:
                    with np.errstate(invalid="ignore"):
                        ok &= ~(wo >= cfg.water_occurrence_max_pct)
                parts[g] = ok
            comps["not_water"] = Dual(parts["G80"], parts["G20"])
        else:
            missing.append("not_water")

        # -- no change vs baseline -------------------------------------------------------------
        c = ctx.cov.get(key)
        if c is not None and c.baseline is not None and not c.baseline.cold_start and c.gamma0_db:
            b = c.baseline
            ok = np.ones(ctx.grids.g20.shape, bool)
            used = 0
            for p, g in c.gamma0_db.items():
                if not (b.n[p] > 0).any():            # polarization never seen in the baseline: nothing to compare with
                    continue
                used += 1
                with np.errstate(invalid="ignore"):   # NaN (unverifiable) compares False -> not stable
                    ok &= np.abs(g[c.current_index] - b.median_db[p]) <= cfg.nochange_k * b.sigma_db[p]
            if used:
                comps["no_change"] = Dual(ctx.grids.aggregate_20_to_80(ok, "all"), ok)
            else:
                missing.append("no_change")
        else:
            missing.append("no_change")

        if not comps:
            ctx.flag("stable_mask_unavailable", "warn", "P11", f"{key}: no criterion could be evaluated; no stable mask",
                     stack=key, missing=missing)
            ctx.stable[key] = StableMask(Dual(), (), tuple(missing))
            continue
        m80 = np.logical_and.reduce([d.g80 for d in comps.values()])
        m20 = np.logical_and.reduce([d.g20 for d in comps.values()])
        ctx.stable[key] = StableMask(Dual(m80, m20), tuple(comps), tuple(missing), FrozenDict(comps))
        if missing:
            ctx.flag("stable_mask_partial", "info", "P11", f"{key}: stable mask built without {', '.join(missing)}",
                     stack=key, missing=missing)
        rs, cs = ctx.grids.g80.core_slices()
        if float(m80[rs, cs].mean()) == 0.0:
            ctx.flag("stable_mask_empty", "warn", "P11", f"{key}: no stable pixel inside the tile", stack=key)
