"""Travel-time field from the eikonal equation ‖∇T‖ = 1/c(x, y), c = √(g·h) on the still-water
depth — Gauss–Seidel fast sweeping (alternating sweep orders; Zhao 2005).

Seeding: T = 0 on wet cells where the source deformation |Δh| exceeds seedFraction·max|Δh| (the
deformed sea-floor region). Gives an independent first-arrival estimate that does not depend on
the nonlinear solver, used to cross-check model arrivals and to mask seismically-contaminated
DART triggers (a water wave cannot outrun the ray time).

The local update is the Godunov (upwind) discretization — a naive neighbour+distance update
converges to the L1 metric and overestimates diagonal travel times by up to √2. Acceptance test:
flat-bottom point-seed arrival within 4 % of distance/√(gh) (port spec §14).
"""
from __future__ import annotations

import math

import numpy as np

G = 9.81
EARTH_RADIUS_M = 6371.0088e3
INF = 1e18


def travel_time_field(res_arcmin: float, lat0: float, bed: np.ndarray, dz: np.ndarray,
                      seed_fraction: float = 0.1, rounds: int = 6) -> np.ndarray:
    """-> T (seconds; NaN on land / unreachable), shape (ny, nx). Row 0 = southernmost row.

    bed: metres, positive up (negative = water). dz: seafloor displacement on the same grid.
    """
    bed = np.asarray(bed, dtype=np.float64)
    dz = np.asarray(dz, dtype=np.float64)
    ny, nx = bed.shape
    depth = np.maximum(0.0, -bed)

    max_abs = float(np.nanmax(np.abs(dz))) if dz.size else 0.0
    seed_level = seed_fraction * max_abs
    T = np.full((ny, nx), INF, dtype=np.float64)
    T[(bed < 0) & (np.abs(dz) >= seed_level)] = 0.0

    dy = math.radians(res_arcmin / 60.0) * EARTH_RADIUS_M
    lats = lat0 + np.arange(ny) * res_arcmin / 60.0
    dxs = math.radians(res_arcmin / 60.0) * EARTH_RADIUS_M * np.cos(np.radians(lats))   # per row

    def update(j: int, i: int) -> None:
        hk = depth[j, i]
        if hk <= 0.0:
            return
        dx = dxs[j]
        Tx = INF
        Tx_idx = -1
        if i > 0 and T[j, i - 1] < Tx:
            Tx, Tx_idx = T[j, i - 1], i - 1
        if i < nx - 1 and T[j, i + 1] < Tx:
            Tx, Tx_idx = T[j, i + 1], i + 1
        Ty = INF
        Ty_idx = -1
        if j > 0 and T[j - 1, i] < Ty:
            Ty, Ty_idx = T[j - 1, i], j - 1
        if j < ny - 1 and T[j + 1, i] < Ty:
            Ty, Ty_idx = T[j + 1, i], j + 1

        def speed(jn: int, in_: int) -> float:
            hmin = min(hk, depth[jn, in_])
            return math.sqrt(G * hmin) if hmin > 0 else 0.0

        one_x = Tx + dx / speed(j, Tx_idx) if Tx_idx >= 0 else INF
        one_y = Ty + dy / speed(Ty_idx, i) if Ty_idx >= 0 else INF
        cand = min(one_x, one_y)

        if Tx_idx >= 0 and Ty_idx >= 0:
            cx = speed(j, Tx_idx)
            cy = speed(Ty_idx, i)
            if cx > 0 and cy > 0:
                c = min(cx, cy)
                A = dy * dy + dx * dx
                B = -2.0 * (dy * dy * Tx + dx * dx * Ty)
                C = dy * dy * Tx * Tx + dx * dx * Ty * Ty - (dx * dx * dy * dy) / (c * c)
                disc = B * B - 4.0 * A * C
                if disc >= 0.0:
                    # the eikonal root is the PLUS root (>= max(Tx, Ty)); the minus root is the
                    # non-upwind branch (verified on the flat-plane benchmark)
                    r = (-B + math.sqrt(disc)) / (2.0 * A)
                    if r >= max(Tx, Ty) - 1e-9:
                        cand = min(cand, r)
        if cand < T[j, i]:
            T[j, i] = cand

    for _ in range(rounds):
        before = T.copy()
        for j in range(ny):                       # top-down, left-right
            for i in range(nx):
                update(j, i)
        for j in range(ny):                       # top-down, right-left
            for i in range(nx - 1, -1, -1):
                update(j, i)
        for j in range(ny - 1, -1, -1):           # bottom-up, left-right
            for i in range(nx):
                update(j, i)
        for j in range(ny - 1, -1, -1):           # bottom-up, right-left
            for i in range(nx - 1, -1, -1):
                update(j, i)
        if float(np.max(np.abs(T - before))) < 1e-3:
            break

    T[T >= INF] = np.nan
    return T


def travel_time_at(T: np.ndarray, lat0: float, lon0: float, res_arcmin: float,
                   lat: float, lon: float) -> float:
    """Travel time (s) at the grid cell nearest (lat, lon); NaN if unreachable/out of range."""
    ny, nx = T.shape
    j = int(round((lat - lat0) * 60.0 / res_arcmin))
    i = int(round((lon - lon0) * 60.0 / res_arcmin))
    if j < 0 or j >= ny or i < 0 or i >= nx:
        return math.nan
    t = T[j, i]
    return float(t) if math.isfinite(t) else math.nan


__all__ = ["travel_time_field", "travel_time_at"]
