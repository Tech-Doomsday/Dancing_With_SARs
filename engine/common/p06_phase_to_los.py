"""P06 - unwrapped phase -> LOS displacement with per-pixel sigma.

Output sign: ``los_disp_m`` is positive TOWARD the sensor (range decrease),
regardless of the product's phase sign (see PreprocConfig.phase_means_range_increase).
Not referenced to any point: referencing is pipeline-specific (e.g. far-field).
"""
from __future__ import annotations

from typing import Dict

import numpy as np

from .config import PreprocConfig
from .state import BundleBuilder

STAGE = "P06"


def phase_factor(wavelength_m: float, cfg: PreprocConfig) -> float:
    """metres of toward-sensor LOS displacement per radian of interferometric phase."""
    f = wavelength_m / (4.0 * np.pi)
    return -f if cfg.phase_means_range_increase else f


def phase_to_los_m(phase_rad: np.ndarray, wavelength_m: float, cfg: PreprocConfig) -> np.ndarray:
    return (phase_rad * np.float32(phase_factor(wavelength_m, cfg))).astype(np.float32)


def phase_sigma(coherence: np.ndarray, n_looks: float, cap_rad: float) -> np.ndarray:
    """Cramer-Rao phase std: sqrt(1-g^2) / (g * sqrt(2 L)), capped."""
    g = np.clip(coherence.astype(np.float64), 0.05, 0.995)
    s = np.sqrt(1.0 - g * g) / (g * np.sqrt(2.0 * n_looks))
    return np.minimum(s, cap_rad).astype(np.float32)


def validity(unw: np.ndarray, coh: np.ndarray, cc: np.ndarray, min_coh: float) -> np.ndarray:
    with np.errstate(invalid="ignore"):
        return np.isfinite(unw) & np.isfinite(coh) & (cc >= 1) & (coh >= min_coh)


def displacement_layers(unw, coh, cc, wavelength_m, cfg):
    valid = validity(unw, coh, cc, cfg.min_coherence)
    disp = phase_to_los_m(unw, wavelength_m, cfg)
    sig = (phase_sigma(coh, cfg.n_looks, cfg.sigma_phase_cap_rad) * np.float32(abs(phase_factor(wavelength_m, cfg))))
    disp = np.where(valid, disp, np.nan).astype(np.float32)
    sig = np.where(valid, sig, np.nan).astype(np.float32)
    return disp, sig.astype(np.float32), valid


def run(state: BundleBuilder) -> Dict:
    cfg, w = state.cfg, state.work
    wl = float(state.clip.attrs.get("wavelength_m", cfg.wavelength_m))
    disp, sig, valid = displacement_layers(w["unw_phase"], w["coherence"], w["conncomp"], wl, cfg)
    state.put("unw_phase_rad", w["unw_phase"].astype(np.float32))
    state.put("los_disp_m", disp)
    state.put("los_sigma_m", sig)
    state.put("valid", valid)
    state.meta.update(wavelength_m=wl, los_positive="toward_sensor", referenced=False,
                      phase_means_range_increase=cfg.phase_means_range_increase)
    return {"valid_fraction": float(valid.mean()), "wavelength_m": wl,
            "sigma_median_mm": float(np.nanmedian(sig) * 1e3) if valid.any() else float("nan")}
