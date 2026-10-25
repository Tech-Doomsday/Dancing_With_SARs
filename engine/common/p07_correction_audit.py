"""P07 - correction audit: RECORD corrections, never apply them silently.

For every correction layer the product ships (ionosphere, troposphere, solid-earth
tide) we convert it to LOS metres as a diagnostic layer, report its magnitude and
whether the *product* already applied it. The engine itself applies nothing:
``engine_applied`` is always False. A residual planar ramp and (once the DEM is
aligned, in P09) the phase/elevation correlation are recorded the same way.
"""
from __future__ import annotations

from typing import Dict, Optional

import numpy as np

from .p06_phase_to_los import phase_to_los_m
from .state import BundleBuilder

STAGE = "P07"

SCREENS = {
    "iono_phase": ("iono_los_m", "ionosphere"),
    "wet_tropo_phase": ("wet_tropo_los_m", "wet_troposphere"),
    "hydro_tropo_phase": ("hydro_tropo_los_m", "hydrostatic_troposphere"),
    "solid_earth_tide_phase": ("tide_los_m", "solid_earth_tide"),
}


def planar_ramp_mm(disp: np.ndarray, dx: float, dy: float, max_px: int = 50000) -> Optional[Dict[str, float]]:
    ok = np.isfinite(disp)
    if ok.sum() < 100:
        return None
    rr, cc = np.nonzero(ok)
    if rr.size > max_px:
        sel = np.random.default_rng(0).choice(rr.size, max_px, replace=False)
        rr, cc = rr[sel], cc[sel]
    z = disp[rr, cc].astype(np.float64)
    X = np.column_stack([np.ones(rr.size), cc * dx / 1000.0, rr * dy / 1000.0])  # km
    coef, *_ = np.linalg.lstsq(X, z, rcond=None)
    ny, nx = disp.shape
    corners = np.array([[0, 0], [nx * dx / 1000.0, 0], [0, ny * dy / 1000.0], [nx * dx / 1000.0, ny * dy / 1000.0]])
    plane = coef[1] * corners[:, 0] + coef[2] * corners[:, 1]
    return {"gradient_x_mm_per_km": float(coef[1] * 1e3), "gradient_y_mm_per_km": float(coef[2] * 1e3),
            "amplitude_mm": float((plane.max() - plane.min()) * 1e3)}


def elevation_audit(state: BundleBuilder) -> None:
    """Called by P09 once a DEM is on G80: Pearson r between LOS displacement and elevation."""
    dem, disp = state.layers.get("dem_m"), state.layers.get("los_disp_m")
    if dem is None or disp is None:
        return
    ok = np.isfinite(dem) & np.isfinite(disp)
    if ok.sum() < 100 or np.std(dem[ok]) == 0 or np.std(disp[ok]) == 0:
        state.audit["elevation_correlation"] = {"r": None, "n": int(ok.sum())}
        return
    r = float(np.corrcoef(dem[ok].astype(np.float64), disp[ok].astype(np.float64))[0, 1])
    state.audit["elevation_correlation"] = {"r": r, "n": int(ok.sum()), "engine_applied": False}


def run(state: BundleBuilder) -> Dict:
    cfg, w = state.cfg, state.work
    wl = state.meta["wavelength_m"]
    applied_in_product = (state.clip.attrs.get("corrections_applied") or {}) if state.clip else {}
    entries: Dict[str, Dict] = {}
    for src, (layer_name, label) in SCREENS.items():
        if src not in w:
            entries[label] = {"available": False, "applied_in_product": applied_in_product.get(label),
                              "engine_applied": False}
            continue
        los = phase_to_los_m(w[src], wl, cfg)
        state.put(layer_name, los)
        ok = np.isfinite(los)
        rms = float(np.sqrt(np.mean(los[ok].astype(np.float64) ** 2)) * 1e3) if ok.any() else None
        p95 = float(np.percentile(np.abs(los[ok]), 95) * 1e3) if ok.any() else None
        entries[label] = {"available": True, "layer": layer_name, "rms_mm": rms, "p95_abs_mm": p95,
                          "valid_fraction": float(ok.mean()),
                          "applied_in_product": applied_in_product.get(label), "engine_applied": False,
                          "significant": bool(rms is not None and rms > cfg.audit_flag_mm)}
    state.audit["corrections"] = entries
    g = state.grids["G80"]
    ramp = planar_ramp_mm(state.layers["los_disp_m"], g.dx, g.dy, cfg.audit_max_fit_pixels)
    if ramp is not None:
        ramp["significant"] = bool(ramp["amplitude_mm"] > cfg.ramp_flag_mm)
        ramp["engine_applied"] = False
    state.audit["residual_ramp"] = ramp
    return {"corrections_available": [k for k, v in entries.items() if v["available"]],
            "ramp_mm": ramp["amplitude_mm"] if ramp else None}
