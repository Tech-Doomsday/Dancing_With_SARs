"""Self-contained terrain helpers (numpy only). Deliberately NOT imported from
engine.lib.terrain: preprocessing must not depend on code it cannot see or own.
Swap in lib.terrain later if you want a single implementation."""
from __future__ import annotations

from typing import Tuple

import numpy as np


def gradients(dem: np.ndarray, dx: float, dy: float) -> Tuple[np.ndarray, np.ndarray]:
    """Horn gradients: (dz/dEast, dz/dNorth). Edges replicate; NaN propagates."""
    z = np.pad(dem.astype(np.float32, copy=False), 1, mode="edge")
    a, b, c = z[:-2, :-2], z[:-2, 1:-1], z[:-2, 2:]
    d, f = z[1:-1, :-2], z[1:-1, 2:]
    g, h, i = z[2:, :-2], z[2:, 1:-1], z[2:, 2:]
    dz_de = ((c + 2 * f + i) - (a + 2 * d + g)) / (8.0 * dx)
    dz_ds = ((g + 2 * h + i) - (a + 2 * b + c)) / (8.0 * dy)  # rows increase southwards
    return dz_de.astype(np.float32), (-dz_ds).astype(np.float32)


def slope_aspect(dem: np.ndarray, dx: float, dy: float):
    """slope (deg) and aspect (deg clockwise from north of the downslope direction)."""
    dz_de, dz_dn = gradients(dem, dx, dy)
    slope = np.degrees(np.arctan(np.hypot(dz_de, dz_dn))).astype(np.float32)
    aspect = (np.degrees(np.arctan2(-dz_de, -dz_dn)) % 360.0).astype(np.float32)
    aspect[(dz_de == 0) & (dz_dn == 0)] = np.nan
    return slope, aspect


def _running_min(a: np.ndarray, r: int, axis: int) -> np.ndarray:
    out = a.copy()
    for s in range(1, r + 1):
        if axis == 0:
            out[s:] = np.fmin(out[s:], a[:-s])
            out[:-s] = np.fmin(out[:-s], a[s:])
        else:
            out[:, s:] = np.fmin(out[:, s:], a[:, :-s])
            out[:, :-s] = np.fmin(out[:, :-s], a[:, s:])
    return out


def hand_proxy(dem: np.ndarray, radius_px: int) -> np.ndarray:
    """Height above the lowest cell within +/- radius_px (a *proxy* for HAND).

    A true HAND needs flow routing; this is an honest, cheap stand-in and is
    flagged as such in the bundle (``hand_method='local_min_proxy'``).
    """
    z = dem.astype(np.float32, copy=False)
    lm = _running_min(_running_min(z, max(1, radius_px), 0), max(1, radius_px), 1)
    out = z - lm
    out[~np.isfinite(z)] = np.nan
    return np.maximum(out, 0.0).astype(np.float32)


def layover_shadow_from_dem(dem: np.ndarray, dx: float, dy: float, los_e: np.ndarray,
                            los_n: np.ndarray, los_u: np.ndarray) -> np.ndarray:
    """True where terrain geometry implies layover or radar shadow.

    LOS vectors point target->sensor. A slope facing the sensor with angle >=
    incidence is layover; a back slope steeper than (90 - incidence) is shadow.
    """
    dz_de, dz_dn = gradients(dem, dx, dy)
    hn = np.hypot(los_e, los_n)
    with np.errstate(invalid="ignore", divide="ignore"):
        ue, un = los_e / hn, los_n / hn
    dz_du = dz_de * ue + dz_dn * un          # >0: ground rises toward sensor (back slope)
    inc = np.degrees(np.arccos(np.clip(los_u, -1.0, 1.0)))
    fore = np.degrees(np.arctan(-dz_du))
    back = np.degrees(np.arctan(dz_du))
    with np.errstate(invalid="ignore"):
        bad = (fore >= inc) | (back >= (90.0 - inc))
    return bad & np.isfinite(dz_du)
