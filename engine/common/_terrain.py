"""Terrain derivatives needed by P05 (local incidence, layover/shadow) and P09 (slope, aspect, curvature, HAND).

Private to engine/common on purpose: `engine/lib/terrain.py` is a different work item. When that module
exists these functions can be swapped for it behind the same signatures.

Conventions: rows run north -> south, columns west -> east, dx/dy are positive metres. Gradients are returned
as (dz/dEast, dz/dNorth). Aspect is the downslope azimuth (direction the slope faces), degrees clockwise from
(grid) north; undefined (NaN) on flat ground.
"""
from __future__ import annotations

import heapq

import numpy as np
from scipy import ndimage as ndi


def fill_voids(dem: np.ndarray) -> tuple[np.ndarray, float]:
    """Replace NaN with the nearest valid value. Returns (filled, void_fraction)."""
    bad = ~np.isfinite(dem)
    frac = float(bad.mean())
    if frac == 0.0:
        return dem.astype(np.float64), 0.0
    if frac == 1.0:
        return dem.astype(np.float64), 1.0
    idx = ndi.distance_transform_edt(bad, return_distances=False, return_indices=True)
    return dem[tuple(idx)].astype(np.float64), frac


def gradients(dem: np.ndarray, dx: float, dy: float) -> tuple[np.ndarray, np.ndarray]:
    """Horn (1981) 3x3 gradients (dz/dEast, dz/dNorth). Edges replicate."""
    z = np.pad(dem, 1, mode="edge")
    a, b, c = z[:-2, :-2], z[:-2, 1:-1], z[:-2, 2:]
    d, f = z[1:-1, :-2], z[1:-1, 2:]
    g, h, i = z[2:, :-2], z[2:, 1:-1], z[2:, 2:]
    dzdx = ((c + 2 * f + i) - (a + 2 * d + g)) / (8 * dx)
    dzdn = ((a + 2 * b + c) - (g + 2 * h + i)) / (8 * dy)
    return dzdx, dzdn


def slope_aspect(dem: np.ndarray, dx: float, dy: float, flat_deg: float = 0.1):
    dzdx, dzdn = gradients(dem, dx, dy)
    slope = np.degrees(np.arctan(np.hypot(dzdx, dzdn)))
    aspect = np.degrees(np.arctan2(-dzdx, -dzdn)) % 360.0
    aspect = np.where(slope < flat_deg, np.nan, aspect)
    return slope.astype(np.float32), aspect.astype(np.float32), dzdx, dzdn


def curvature_zt(dem: np.ndarray, dx: float, dy: float):
    """Zevenbergen & Thorne (1987) profile and plan curvature, 1/m. Sign check: in a bowl (z = r^2) profile
    curvature is negative and plan curvature positive; on a dome the signs flip. Uses the mean spacing for the second derivatives (square pixels in every grid this package builds)."""
    L = 0.5 * (dx + dy)
    z = np.pad(dem, 1, mode="edge")
    z1, z2, z3 = z[:-2, :-2], z[:-2, 1:-1], z[:-2, 2:]
    z4, z5, z6 = z[1:-1, :-2], z[1:-1, 1:-1], z[1:-1, 2:]
    z7, z8, z9 = z[2:, :-2], z[2:, 1:-1], z[2:, 2:]
    D = ((z4 + z6) / 2 - z5) / L ** 2
    E = ((z2 + z8) / 2 - z5) / L ** 2
    F = (-z1 + z3 + z7 - z9) / (4 * L ** 2)
    G = (-z4 + z6) / (2 * L)
    H = (z2 - z8) / (2 * L)
    q = G ** 2 + H ** 2
    with np.errstate(invalid="ignore", divide="ignore"):
        prof = np.where(q > 1e-12, -2 * (D * G ** 2 + E * H ** 2 + F * G * H) / q, 0.0)
        plan = np.where(q > 1e-12, 2 * (D * H ** 2 + E * G ** 2 - F * G * H) / q, 0.0)
    return prof.astype(np.float32), plan.astype(np.float32)


# ---------------------------------------------------------------------------------------------
# HAND
# ---------------------------------------------------------------------------------------------
_NB = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


def _priority_flood(z: np.ndarray) -> np.ndarray:
    """Barnes et al. (2014) Priority-Flood + epsilon: fill depressions and give flats a gradient so every
    cell has a strictly lower neighbour toward an edge outlet."""
    ny, nx = z.shape
    out = z.astype(np.float64).copy()
    seen = np.zeros((ny, nx), bool)
    heap: list = []
    for r in range(ny):
        for c in ((0, nx - 1) if nx > 1 else (0,)):
            if not seen[r, c]:
                seen[r, c] = True
                heapq.heappush(heap, (out[r, c], r * nx + c))
    for c in range(nx):
        for r in ((0, ny - 1) if ny > 1 else (0,)):
            if not seen[r, c]:
                seen[r, c] = True
                heapq.heappush(heap, (out[r, c], r * nx + c))
    zl = out.tolist()
    seen_l = seen.tolist()
    heap = [(zl[i // nx][i % nx], i) for _, i in heap]
    heapq.heapify(heap)
    nextafter = np.nextafter
    while heap:
        zc, i = heapq.heappop(heap)
        r, c = divmod(i, nx)
        for dr, dc in _NB:
            rr, cc = r + dr, c + dc
            if 0 <= rr < ny and 0 <= cc < nx and not seen_l[rr][cc]:
                seen_l[rr][cc] = True
                zn = zl[rr][cc]
                if zn <= zc:
                    zn = float(nextafter(zc, np.inf))
                    zl[rr][cc] = zn
                heapq.heappush(heap, (zn, rr * nx + cc))
    return np.asarray(zl, dtype=np.float64)


def _flow_targets(zf: np.ndarray, dx: float, dy: float) -> np.ndarray:
    """D8 steepest descent on the filled DEM -> flat index of the receiving cell, -1 at outlets."""
    ny, nx = zf.shape
    best = np.zeros((ny, nx))
    tgt = np.full((ny, nx), -1, dtype=np.int64)
    idx = np.arange(ny * nx).reshape(ny, nx)
    zp = np.pad(zf, 1, mode="constant", constant_values=np.inf)
    ip = np.pad(idx, 1, mode="constant", constant_values=-1)
    for dr, dc in _NB:
        dist = np.hypot(dr * dy, dc * dx)
        nb = zp[1 + dr:1 + dr + ny, 1 + dc:1 + dc + nx]
        drop = (zf - nb) / dist
        better = drop > best
        best = np.where(better, drop, best)
        tgt = np.where(better, ip[1 + dr:1 + dr + ny, 1 + dc:1 + dc + nx], tgt)
    return tgt.ravel()


def hand(dem: np.ndarray, dx: float, dy: float, *, min_drainage_km2: float, extra_drainage: np.ndarray | None = None,
         max_m: float = 300.0) -> tuple[np.ndarray, float]:
    """Height Above Nearest Drainage, metres. Drainage = cells draining at least `min_drainage_km2` plus any
    cell in `extra_drainage` (e.g. permanent water). Cells whose flow path leaves the window before meeting
    drainage are NaN (unknown), never guessed. Returns (hand, fraction_valid)."""
    ny, nx = dem.shape
    z, _ = fill_voids(dem)
    zf = _priority_flood(z)
    tgt = _flow_targets(zf, dx, dy)
    n = ny * nx
    order = np.argsort(zf.ravel(), kind="stable")
    t = tgt.tolist()
    acc = [1.0] * n
    for i in order[::-1].tolist():              # high -> low: push accumulation downstream
        j = t[i]
        if j >= 0:
            acc[j] += acc[i]
    cell_km2 = dx * dy / 1e6
    stream = np.asarray(acc) * cell_km2 >= min_drainage_km2
    if extra_drainage is not None:
        stream |= np.asarray(extra_drainage, bool).ravel()
    ref = [-1] * n
    st = stream.tolist()
    for i in order.tolist():                    # low -> high: the receiver is already resolved
        if st[i]:
            ref[i] = i
        else:
            j = t[i]
            ref[i] = ref[j] if j >= 0 else -1
    refa = np.asarray(ref)
    zz = z.ravel()
    out = np.full(n, np.nan)
    ok = refa >= 0
    out[ok] = np.clip(zz[ok] - zz[refa[ok]], 0.0, max_m)
    return out.reshape(ny, nx).astype(np.float32), float(ok.mean())


# ---------------------------------------------------------------------------------------------
# radar geometry on terrain
# ---------------------------------------------------------------------------------------------
def local_geometry(dzdx: np.ndarray, dzdn: np.ndarray, los_enu: np.ndarray, incidence_deg: np.ndarray,
                   layover_margin_deg: float = 0.0, shadow_margin_deg: float = 0.0):
    """Local incidence angle and the slope-based layover / shadow test.

    `los_enu` (3, ny, nx) points from the ground toward the sensor. A slope facing the sensor is steeper than
    the incidence angle -> layover; a slope facing away steeper than (90 - incidence) -> shadow. This is the
    local test only: occlusion by distant relief is not modelled."""
    n = np.stack([-dzdx, -dzdn, np.ones_like(dzdx)]).astype(np.float64)
    n /= np.linalg.norm(n, axis=0, keepdims=True)
    cos_loc = np.clip((n * los_enu).sum(axis=0), -1.0, 1.0)
    local_inc = np.degrees(np.arccos(cos_loc))
    hx, hy = los_enu[0].astype(np.float64), los_enu[1].astype(np.float64)
    hn = np.hypot(hx, hy)
    with np.errstate(invalid="ignore", divide="ignore"):
        hx, hy = hx / hn, hy / hn
    s = dzdx * hx + dzdn * hy                    # dz/dh toward the sensor; < 0 on slopes that face it
    alpha = np.degrees(np.arctan(-s))
    signed = incidence_deg - alpha
    layover = signed <= layover_margin_deg
    shadow = signed >= 90.0 - shadow_margin_deg
    bad = ~np.isfinite(signed)
    layover[bad] = False
    shadow[bad] = False
    return local_inc.astype(np.float32), layover, shadow
