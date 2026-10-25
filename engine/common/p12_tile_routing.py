"""P12 - tile quality -> applicable-pipeline hint list (+ reasons).

Honours the architecture rule "not applicable != no event": every pipeline gets an
explicit decision with machine-readable reasons. The built-in requirement table is
a FALLBACK distilled from docs/ARCHITECTURE.md; the registry should pass the real
``applies/uses`` from each manifest.yaml via ``requirements=`` (this package does not
read pipeline folders). The registry still has the final say.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Tuple

import numpy as np

from .state import F_LOW_QUALITY, BundleBuilder

STAGE = "P12"

# needs: all required capabilities; needs_any: at least one; min_pairs/min_epochs: stack depth
DEFAULT_REQUIREMENTS: Dict[str, Dict[str, Any]] = {
    "earthquake_coseismic": {"needs": ["displacement", "geometry3d"]},
    "land_subsidence": {"needs": ["displacement", "stack"], "min_pairs": 10},
    "volcanic_deformation": {"needs": ["displacement", "geometry3d", "dem"]},
    "landslide": {"needs": ["displacement", "dem", "coherence"]},
    "glacier_ice_flow": {"needs_any": ["offsets", "backscatter"]},
    "permafrost_thaw": {"needs": ["displacement", "stack"], "min_epochs": 12},
    "flood_inundation": {"needs": ["backscatter", "dem"]},
    "deforestation": {"needs": ["backscatter", "stack"], "min_epochs": 4},
    "wildfire_burn_scar": {"needs": ["backscatter", "coherence"]},
    "river_erosion": {"needs": ["backscatter"]},
    "coastal_erosion": {"needs": ["backscatter"]},
    "urban_damage": {"needs": ["coherence", "stack"], "min_pairs": 3},
    "tropical_cyclone": {"sensors": ["Sentinel-1"]},
    "tsunami_inundation": {"needs": ["backscatter", "displacement"]},
    "oil_spill": {"sensors": ["Sentinel-1"]},
    "iceberg_calving_drift": {"needs_any": ["backscatter"]},
}


def capabilities(state: BundleBuilder) -> List[str]:
    L, caps = state.layers, {"coherence"}
    if np.isfinite(L["los_disp_m"]).any():
        caps.add("displacement")
    if {"los_e", "los_n", "los_u"} <= set(L):
        caps.add("geometry3d")
    if "dem_m" in L:
        caps.update(("dem", "terrain"))
    if state.meta.get("backscatter_available"):
        caps.add("backscatter")
    if "rg_offset" in state.work and "az_offset" in state.work:
        caps.add("offsets")
    if state.stack is not None and not state.stack.cold_start:
        caps.add("stack")
    return sorted(caps)


def tile_quality(state: BundleBuilder) -> Dict[str, Any]:
    cfg, L = state.cfg, state.layers
    valid = L["valid"]
    vf = float(valid.mean())
    mean_coh = float(np.nanmean(L["coherence"])) if np.isfinite(L["coherence"]).any() else 0.0
    score = vf * (0.5 + 0.5 * mean_coh)
    tier = ("GOOD" if score >= cfg.tier_good else "DEGRADED" if score >= cfg.tier_degraded
            else "POOR" if score >= cfg.tier_poor else "UNUSABLE")
    return {"valid_fraction": vf, "mean_coherence": mean_coh, "score": float(score), "tier": tier,
            "layover_shadow_fraction": float(L["layover_shadow"].mean()),
            "water_fraction": float(L["water"].mean()), "stable_fraction": float(L["stable"].mean()),
            "unwrapped_fraction": float((L["conncomp"] >= 1).mean())}


def route(caps: List[str], quality: Mapping[str, Any], stack, sensor: str,
          requirements: Mapping[str, Mapping[str, Any]]) -> Dict[str, Any]:
    decisions: Dict[str, Dict[str, Any]] = {}
    for name, req in requirements.items():
        reasons: List[str] = []
        if quality["tier"] == "UNUSABLE":
            reasons.append("TILE_UNUSABLE")
        if req.get("sensors") and sensor not in req["sensors"]:
            reasons.append(f"SENSOR_{sensor}_NOT_IN_{'/'.join(req['sensors'])}")
        reasons += [f"MISSING_{c.upper()}" for c in req.get("needs", []) if c not in caps]
        if req.get("needs_any") and not any(c in caps for c in req["needs_any"]):
            reasons.append("MISSING_ANY_OF_" + "_".join(c.upper() for c in req["needs_any"]))
        if stack is not None:
            if req.get("min_pairs") and stack.n_pairs < req["min_pairs"]:
                reasons.append(f"INSUFFICIENT_PAIRS_{stack.n_pairs}_OF_{req['min_pairs']}")
            if req.get("min_epochs") and stack.n_epochs < req["min_epochs"]:
                reasons.append(f"INSUFFICIENT_EPOCHS_{stack.n_epochs}_OF_{req['min_epochs']}")
        decisions[name] = {"applicable": not reasons, "reasons": reasons}
    return {"applicable": [n for n, d in decisions.items() if d["applicable"]],
            "decisions": decisions, "capabilities": caps}


def run(state: BundleBuilder, requirements: Optional[Mapping[str, Mapping[str, Any]]] = None) -> Dict:
    q = tile_quality(state)
    state.quality = q
    if q["tier"] in ("POOR", "UNUSABLE"):
        state.flag(F_LOW_QUALITY)
    caps = capabilities(state)
    sensor = state.resolution.current.sensor
    state.routing = route(caps, q, state.stack, sensor, requirements or DEFAULT_REQUIREMENTS)
    return {"tier": q["tier"], "score": q["score"], "capabilities": caps,
            "n_applicable": len(state.routing["applicable"])}
