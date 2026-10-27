"""Tsunami source initialization: earthquake → seafloor displacement → initial free surface.

Pipeline (each step documented + unit-tested; ported from the app's source.ts):
  1. Fault geometry: USGS finite-fault FFM.geojson subfaults (preferred) or a single uniform plane
     from the moment-tensor nodal plane with Wells–Coppersmith (1994) area scaling (labelled
     approximation).
  2. Elastic dislocation: the engine's verified Okada/DC3D (engine/lib/okada.py), summed over
     subfaults (static approximation — rupture duration ≪ wave period).
  3. Tanioka & Seno (1997) bathymetric-slope correction: Δh = u_z + u_x ∂b/∂x + u_y ∂b/∂y with
     3×3-smoothed bed gradients (avoids amplifying DEM noise).
  4. Kajiura (1963) water-column transfer (see kajiura.py).

The initial condition handed to the solver is η(x,y,0) = filtered Δh on wet cells, with the bed
UNCHANGED (static-transfer formulation); cells where the filtered surface would fall below the bed
are clamped to the bed (start dry) — the solver does the rest via hydrostatic reconstruction.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from engine.lib.geo import azimuth_deg, en_offsets_m
from engine.lib.okada import GeoFault, geo_fault_displacement_enu_top_edge, moment_from_mw, \
    rupture_area_from_mw, slip_from_moment

EARTH_RADIUS_M = 6371.0088e3


# ------------------------------------------------------------------ #
# finite-fault model (USGS FFM.geojson)
# ------------------------------------------------------------------ #
@dataclass(frozen=True)
class Subfault:
    clat: float
    clon: float
    top_depth_m: float            # top-edge depth
    strike_deg: float
    dip_deg: float
    rake_deg: float
    slip_m: float
    length_m: float
    width_m: float
    moment_nm: float = 0.0
    rise_s: float | None = None
    trup_s: float | None = None


@dataclass
class SourceOptions:
    slope_correction: bool = True
    kajiura_filter: bool = True


DEFAULT_SOURCE_OPTIONS = SourceOptions()


def parse_ffm_geojson(geo: dict) -> list:
    """Parse a USGS FFM.geojson feature collection into Subfaults.

    Each feature is a closed 5-point polygon: corners [0]-[1] are the TOP edge (shallower),
    [3]-[2] the bottom edge. Strike comes from the top-edge azimuth, dip from the depth contrast
    across the plane, top-edge depth from the shallow corners. (Format verified against the Maule
    2010 product, 236 subfaults.)
    """
    out: list = []
    for f in geo.get("features") or []:
        try:
            ring = (f.get("geometry") or {}).get("coordinates")[0]
            if ring is None or len(ring) < 4:
                continue
            p = f.get("properties") or {}
            slip = float(p.get("slip"))
            rake = float(p.get("rake"))
        except (TypeError, ValueError, KeyError, IndexError):
            continue
        c0, c1, c2, c3 = ring[0], ring[1], ring[2], ring[3]
        clat = (c0[1] + c1[1] + c2[1] + c3[1]) / 4.0
        clon = (c0[0] + c1[0] + c2[0] + c3[0]) / 4.0

        strike = azimuth_deg(c0[1], c0[0], c1[1], c1[0])
        e0, n0 = en_offsets_m(clat, clon, c0[1], c0[0])
        e1, n1 = en_offsets_m(clat, clon, c1[1], c1[0])
        length = math.hypot(e1 - e0, n1 - n0)
        if length < 1.0:
            continue
        e3, n3 = en_offsets_m(clat, clon, c3[1], c3[0])
        horiz = math.hypot(e3 - e0, n3 - n0)
        d_top = (c0[2] + c1[2]) / 2.0
        d_bot = (c2[2] + c3[2]) / 2.0
        width = math.hypot(horiz, d_bot - d_top)
        if width < 1.0:
            continue
        dip = math.degrees(math.atan2(abs(d_bot - d_top), max(horiz, 1e-6)))
        if not math.isfinite(dip) or dip <= 0.05 or dip >= 179.9:
            continue
        try:
            moment = float(p.get("sf_moment"))
        except (TypeError, ValueError):
            moment = 0.0
        rise = p.get("rise")
        trup = p.get("trup")
        out.append(Subfault(
            clat=clat, clon=clon,
            top_depth_m=max(0.0, min(d_top, d_bot)),
            strike_deg=strike % 360.0,
            dip_deg=min(dip, 90.0),
            rake_deg=rake,
            slip_m=slip,
            length_m=length, width_m=width,
            moment_nm=moment if math.isfinite(moment) else 0.0,
            rise_s=(float(rise) if rise not in (None, "") and math.isfinite(float(rise)) else None),
            trup_s=(float(trup) if trup not in (None, "") and math.isfinite(float(trup)) else None),
        ))
    return out


# ------------------------------------------------------------------ #
# uniform single-plane fallback
# ------------------------------------------------------------------ #
def uniform_fault_from_scaling(magnitude: float, depth_km: float, lat: float, lon: float,
                               nodal_plane: tuple) -> tuple:
    """Single rectangular plane from the MT nodal plane with Wells–Coppersmith scaling.

    Honest about the large uncertainty: WC94 regression scatter is ~0.25 orders of magnitude in
    area (factor ≈ 1.8) and uniform slip concentrates the peak uplift — callers label this an
    approximation. Returns (Subfault, notes).
    """
    notes: list = []
    strike, dip, rake = nodal_plane
    area = rupture_area_from_mw(magnitude)               # m²
    m0 = moment_from_mw(magnitude)
    ratio = 2 if magnitude >= 7.5 else 1
    width = math.sqrt(area / ratio)
    length = area / width
    slip = slip_from_moment(m0, area)
    top_depth_m = max(0.0, depth_km * 1000.0 - (width / 2.0) * math.sin(math.radians(dip)))
    notes.append(
        f"Wells & Coppersmith (1994) all-slip-types scaling: log10(A/km²) = 0.91·Mw − 3.49 → "
        f"A = {area / 1e6:.0f} km² (scatter ≈ ×1.8)")
    notes.append(f"uniform slip {slip * 100:.0f} cm from M0 = μ·A·s with μ = 30 GPa")
    notes.append(f"plane {length:.0f} m × {width:.0f} m, top edge at {top_depth_m / 1000.0:.1f} km below sea level")
    fault = Subfault(clat=lat, clon=lon, top_depth_m=top_depth_m, strike_deg=strike % 360.0,
                     dip_deg=dip, rake_deg=rake, slip_m=slip, length_m=length, width_m=width,
                     moment_nm=m0)
    return fault, notes


# ------------------------------------------------------------------ #
# deformation on the simulation grid
# ------------------------------------------------------------------ #
def bed_gradients(res_arcmin: float, lat0: float, bed: np.ndarray) -> tuple:
    """Smoothed bed gradients (3×3 box average of central differences), m/m -> (bx, by)."""
    bed = np.asarray(bed, dtype=np.float64)
    ny, nx = bed.shape
    d_lat_m = math.radians(res_arcmin / 60.0) * EARTH_RADIUS_M
    lats = lat0 + np.arange(ny) * res_arcmin / 60.0
    d_lon_m = math.radians(res_arcmin / 60.0) * EARTH_RADIUS_M * np.cos(np.radians(lats))  # per row

    dx = np.zeros_like(bed)
    dy = np.zeros_like(bed)
    dx[:, 1:-1] = (bed[:, 2:] - bed[:, :-2]) / (2.0 * d_lon_m[:, None])
    dy[1:-1, :] = (bed[2:, :] - bed[:-2, :]) / (2.0 * d_lat_m)

    def box3(f: np.ndarray) -> np.ndarray:
        out = np.zeros_like(f)
        for dj in (-1, 0, 1):
            for di in (-1, 0, 1):
                out += np.roll(np.roll(f, dj, axis=0), di, axis=1)
        return out / 9.0

    return box3(dx), box3(dy)


def compute_deformation(res_arcmin: float, lat0: float, lon0: float, bed: np.ndarray,
                        subfaults: list, opts: SourceOptions | None = None) -> dict:
    """Combined vertical seafloor displacement on the grid (vectorized over each subfault's patch).

    Subfault contributions are evaluated within 1.5·(L+W) of each centroid (beyond that the
    elastic field is sub-cm — irrelevant at tsunami amplitudes and far below ETOPO1 accuracy).
    Returns {"dz", "max_abs_dz", "notes"}.
    """
    opts = opts or DEFAULT_SOURCE_OPTIONS
    bed = np.asarray(bed, dtype=np.float64)
    ny, nx = bed.shape
    dz = np.zeros((ny, nx), dtype=np.float64)
    notes: list = []

    bx = by = None
    if opts.slope_correction:
        bx, by = bed_gradients(res_arcmin, lat0, bed)

    # grid coordinates (lat/lon per cell) — lat/lon of the CELL CENTRES
    lats = lat0 + np.arange(ny) * res_arcmin / 60.0
    lons = lon0 + np.arange(nx) * res_arcmin / 60.0
    lon_grid, lat_grid = np.meshgrid(lons, lats)

    for sf in subfaults:
        fault = GeoFault(strike_deg=sf.strike_deg, dip_deg=sf.dip_deg, rake_deg=sf.rake_deg,
                         length_m=sf.length_m, width_m=sf.width_m, top_depth_m=0.0,
                         slip_m=sf.slip_m, centroid_lat=sf.clat, centroid_lon=sf.clon)
        radius = 1.5 * (sf.length_m + sf.width_m)
        d_lat_deg = math.degrees(radius / EARTH_RADIUS_M)
        d_lon_deg = d_lat_deg / max(0.1, math.cos(math.radians(sf.clat)))
        j_min = max(0, int(math.floor((sf.clat - d_lat_deg - lat0) * 60.0 / res_arcmin)) - 1)
        j_max = min(ny - 1, int(math.ceil((sf.clat + d_lat_deg - lat0) * 60.0 / res_arcmin)) + 1)
        i_min = max(0, int(math.floor((sf.clon - d_lon_deg - lon0) * 60.0 / res_arcmin)) - 1)
        i_max = min(nx - 1, int(math.ceil((sf.clon + d_lon_deg - lon0) * 60.0 / res_arcmin)) + 1)

        sub_lat = lat_grid[j_min:j_max + 1, i_min:i_max + 1]
        sub_lon = lon_grid[j_min:j_max + 1, i_min:i_max + 1]
        e_m, n_m = en_offsets_m(sf.clat, sf.clon, sub_lat, sub_lon)
        near = (e_m * e_m + n_m * n_m) <= radius * radius
        if not near.any():
            continue

        # The top-edge midpoint sits UP-DIP of the centroid by (W/2)·cos(dip) along the dip
        # direction (cos strike, −sin strike).
        dip_dir_e = math.cos(math.radians(sf.strike_deg))
        dip_dir_n = -math.sin(math.radians(sf.strike_deg))
        half_horiz = (sf.width_m / 2.0) * math.cos(math.radians(sf.dip_deg))

        dE, dN, dU, singular = geo_fault_displacement_enu_top_edge(
            fault, sf.top_depth_m, e_m + half_horiz * dip_dir_e, n_m + half_horiz * dip_dir_n)
        v = dU.copy()
        if opts.slope_correction:
            v = v + dE * bx[j_min:j_max + 1, i_min:i_max + 1] + dN * by[j_min:j_max + 1, i_min:i_max + 1]
        v = np.where(np.asarray(singular, dtype=bool), 0.0, v)
        patch = np.where(near, v, 0.0)
        dz[j_min:j_max + 1, i_min:i_max + 1] += patch

    notes.append("Tanioka & Seno (1997) slope correction u_h·∇b applied (3×3-smoothed bed gradients)"
                 if opts.slope_correction else "vertical displacement only (slope correction disabled)")
    max_abs = float(np.max(np.abs(dz))) if dz.size else 0.0
    return {"dz": dz, "max_abs_dz": max_abs, "notes": notes}


def build_source_field(res_arcmin: float, lat0: float, lon0: float, bed: np.ndarray,
                       subfaults: list, source_kind: str, opts: SourceOptions | None = None,
                       detail: str = "", moment_nm: float | None = None) -> dict:
    """Full source construction: deformation + Kajiura filter → source-field dict.

    η0 is clamped so the initial surface never lies below the bed (cells that subside below the
    bed start dry)."""
    from .kajiura import kajiura_filter

    opts = opts or DEFAULT_SOURCE_OPTIONS
    out = compute_deformation(res_arcmin, lat0, lon0, bed, subfaults, opts)
    dz = out["dz"]
    notes = list(out["notes"])
    if opts.kajiura_filter:
        eta0 = kajiura_filter(res_arcmin, lat0, np.asarray(bed, dtype=np.float64), dz)
        notes.append("Kajiura (1963) water-column transfer 1/cosh(k·d) applied (separable local-depth form)")
    else:
        eta0 = dz.copy()
        notes.append("no water-column filtering (instantaneous static transfer)")
    clamped = int(np.sum(eta0 < np.asarray(bed, dtype=np.float64)))
    eta0 = np.maximum(eta0, np.asarray(bed, dtype=np.float64))
    if clamped:
        notes.append(f"{clamped} cells where the filtered surface fell below the bed were set dry (η = b)")
    return {"dz": dz, "eta0": eta0, "max_abs_dz": out["max_abs_dz"], "source_kind": source_kind,
            "moment_nm": moment_nm, "detail": detail, "notes": notes}


__all__ = ["Subfault", "SourceOptions", "DEFAULT_SOURCE_OPTIONS", "parse_ffm_geojson",
           "uniform_fault_from_scaling", "bed_gradients", "compute_deformation", "build_source_field"]
