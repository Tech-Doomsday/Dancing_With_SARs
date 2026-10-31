"""Kajiura (1963) water-column transfer: η̂(k) = Δĥ(k) / cosh(k·d).

Applied as a SEPARABLE row-then-column pass, each line filtered with its own mean still-water
depth (documented approximation of the radial constant-depth response — identical to leading order
for long waves, which is the regime of seismic sources). Long waves (k·d ≪ 1) pass unchanged;
short waves are exponentially suppressed (the water column cannot transmit them).

NumPy rfft replaces the app's hand-rolled radix-2 FFT (fft.ts) — same mathematics, better
conditioning, no power-of-two constraint. Acceptance test: a wave with wavelength 20× the depth is
attenuated ≤ 2 % (benchmark from the port spec §14).
"""
from __future__ import annotations

import math

import numpy as np

EARTH_RADIUS_M = 6371.0088e3


def _mean_wet_depth(depth: np.ndarray) -> float:
    wet = depth[depth > 0]
    return float(wet.mean()) if wet.size else 0.0


def _filter_line(line: np.ndarray, depth_line: np.ndarray, cell_m: float) -> np.ndarray:
    """One 1-D Kajiura pass: FFT, multiply by 1/cosh(k·d_mean), inverse FFT."""
    n = line.size
    spec = np.fft.rfft(line)
    d_mean = _mean_wet_depth(depth_line)
    k = 2.0 * np.pi * np.arange(spec.size) / (n * cell_m)
    arg = k * d_mean
    scale = np.where(arg > 50.0, 0.0, 1.0 / np.cosh(np.where(arg > 50.0, 50.0, arg)))
    return np.fft.irfft(spec * scale, n=n)


def kajiura_filter(res_arcmin: float, lat0: float, bed: np.ndarray, dz: np.ndarray) -> np.ndarray:
    """Kajiura transfer of the seafloor displacement `dz` on a lat/lon grid.

    res_arcmin: cell size in arc-minutes; lat0: latitude of row 0; bed: (ny, nx) metres, positive
    up (negative = water); dz: (ny, nx) combined vertical seafloor displacement.
    Returns the filtered initial surface elevation η0 (same shape).
    """
    dz = np.asarray(dz, dtype=np.float64)
    bed = np.asarray(bed, dtype=np.float64)
    ny, nx = dz.shape
    dy_m = math.radians(res_arcmin / 60.0) * EARTH_RADIUS_M
    out = np.empty_like(dz)

    # Row pass (x): per-row cell width from the row's latitude
    for j in range(ny):
        lat = lat0 + j * res_arcmin / 60.0
        dx_m = math.radians(res_arcmin / 60.0) * EARTH_RADIUS_M * math.cos(math.radians(lat))
        depth_row = np.maximum(0.0, -bed[j, :])
        out[j, :] = _filter_line(dz[j, :], depth_row, dx_m)

    # Column pass (y): constant meridional cell width
    for i in range(nx):
        depth_col = np.maximum(0.0, -bed[:, i])
        out[:, i] = _filter_line(out[:, i], depth_col, dy_m)

    return out


__all__ = ["kajiura_filter"]
