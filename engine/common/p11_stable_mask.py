"""P11 - stable mask: high coherence, low slope, non-water, usable geometry.

Stable pixels are the reference population for pipelines (far-field referencing,
climatology, noise estimation). Uses the temporal median coherence once the stack
is deep enough, else the current epoch's coherence.
"""
from __future__ import annotations

from typing import Dict

import numpy as np

from .state import BundleBuilder

STAGE = "P11"


def run(state: BundleBuilder) -> Dict:
    cfg, L = state.cfg, state.layers
    coh = L["coh_med"] if (not state.stack.cold_start and np.isfinite(L["coh_med"]).any()) else L["coherence"]
    with np.errstate(invalid="ignore"):
        stable = np.isfinite(coh) & (coh >= cfg.stable_min_coherence) & L["valid"]
    stable &= ~L["water"]
    stable &= ~L["layover_shadow"]
    if "slope_deg" in L:
        with np.errstate(invalid="ignore"):
            stable &= np.isfinite(L["slope_deg"]) & (L["slope_deg"] <= cfg.stable_max_slope_deg)
        state.meta["stable_uses_slope"] = True
    else:
        state.meta["stable_uses_slope"] = False
    state.meta["stable_coherence_source"] = "temporal_median" if coh is L["coh_med"] else "current_epoch"
    state.put("stable", stable)
    return {"stable_fraction": float(stable.mean()), "coherence_source": state.meta["stable_coherence_source"]}
