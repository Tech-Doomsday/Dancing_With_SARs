"""engine.lib.okada — Okada (1985/1992) rectangular dislocation in an elastic
half-space, surface displacement (z = 0).

Lineage of this implementation (do not "simplify" any branch — the benchmark
test suite pins every term at rel < 1e-8 against the official DC3D Fortran):

    Okada 1985/1992  ->  dc3d.py (Satoshi Pes, Python port of the Fortran)
                     ->  TypeScript port (src/lib/science/okada.ts in the
                         "Dancing with SARs" app, cross-validated at rel < 1e-8
                         against tools/dc3d_reference.py)
                     ->  THIS module (engine/lib/okada.py), the shared physics
                         home for earthquake_coseismic AND tsunami_inundation.

Two entry layers:
    * scalar reference path  — okada_surface_displacement_raw() /
      okada_surface_displacement(): term-for-term translation of the validated
      port. Exact; used by tests and by anything needing one point.
    * vectorised grid path   — okada_surface_grid(): same mathematics with
      NumPy broadcasting over arrays of observation points (per-fault-corner
      branches stay scalar; per-point singularity masking is exact, and the
      switch-branch overrides are applied only where the switch fires).

SLIP-SENSE CONVENTIONS (inherited verbatim; pinned by tests):
    U1 (strike) positive = SINISTRAL; U2 (dip) positive = NORMAL sense
    (hanging wall down over the +y side); U3 (tensile) positive = OPENING.
    Aki & Richards rake mapping: u1 = slip*cos(rake), u2 = -slip*sin(rake).

Fault frame: x along strike, y horizontal pointing in the dip direction
(hanging-wall side), z up. DEPTH is the top-edge depth; the down-dip extent
is expressed via DC3D's aw range (positive aw = up-dip, so a fault with its
top edge at DEPTH spanning width W down-dip is aw = [-W, 0]).

Units: arbitrary but consistent (metres everywhere in the engine).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

PI2 = 2.0 * math.pi
NearlyZero = 1e-5
DipSwitchEps = 1e-10
RangeSwitchEps = 1e-6


# --------------------------------------------------------------------------- #
# shared fault descriptions
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class OkadaFault:
    """Friendly fault description: top edge at `depth`, extends `width` down-dip."""

    depth: float            # top-edge depth (same length unit as coordinates)
    dip_deg: float          # dip from horizontal, degrees
    length: float           # along-strike extent
    width: float            # down-dip extent
    u1: float = 0.0         # strike dislocation (positive = sinistral)
    u2: float = 0.0         # dip dislocation (positive = normal sense)
    u3: float = 0.0         # tensile dislocation (positive = opening)


@dataclass(frozen=True)
class GeoFault:
    """Geographic fault for forward modelling (strike/dip/rake + scaling relations)."""

    strike_deg: float       # clockwise from north
    dip_deg: float
    rake_deg: float         # Aki & Richards: 0 = sinistral, +/-180 = dextral, +90 = reverse, -90 = normal
    length_m: float
    width_m: float
    top_depth_m: float      # informational (computed from centroid depth in geo placement)
    slip_m: float
    centroid_lat: float
    centroid_lon: float


@dataclass(frozen=True)
class SurfaceDisplacement:
    ux: float
    uy: float
    uz: float
    singular: bool


# --------------------------------------------------------------------------- #
# DC3D coefficient helpers (exact translations)
# --------------------------------------------------------------------------- #
def _dccon0(alpha: float, dip_deg: float) -> dict:
    alp1 = (1.0 - alpha) / 2.0
    alp2 = alpha / 2.0
    alp3 = (1.0 - alpha) / alpha
    alp4 = 1.0 - alpha
    alp5 = alpha
    sd = math.sin(dip_deg * math.pi / 180.0)
    cd = math.cos(dip_deg * math.pi / 180.0)
    if abs(cd) < DipSwitchEps:
        cd = 0.0
        sd = 1.0 if sd > 0 else (-1.0 if sd < 0 else 0.0)
    return {
        "alp1": alp1, "alp2": alp2, "alp3": alp3, "alp4": alp4, "alp5": alp5,
        "sd": sd, "cd": cd,
        "sdsd": sd * sd, "cdcd": cd * cd, "sdcd": sd * cd,
    }


def _nearly_zero(a: float) -> bool:
    return abs(a) < NearlyZero


def _dccon2(xi: float, et: float, q: float, sd: float, cd: float, kxi: int, ket: int) -> dict | None:
    """Displacement coefficients for one fault corner (scalar, exact)."""
    if _nearly_zero(xi):
        xi = 0.0
    if _nearly_zero(et):
        et = 0.0
    if _nearly_zero(q):
        q = 0.0

    xi2 = xi * xi
    et2 = et * et
    q2 = q * q
    r2 = xi2 + et2 + q2
    r = math.sqrt(r2)
    if _nearly_zero(r):
        return None

    r3 = r * r2
    y = et * cd + q * sd
    d = et * sd - q * cd

    tt = 0.0
    if q != 0.0:
        tt = math.atan((xi * et) / (q * r))

    if kxi == 1:
        alx = -math.log(r - xi)
        x11 = 0.0
        x32 = 0.0
    else:
        rxi = r + xi
        alx = math.log(rxi)
        x11 = 1.0 / (r * rxi)
        x32 = ((r + rxi) * x11 * x11) / r

    if ket == 1:
        ale = -math.log(r - et)
        y11 = 0.0
        y32 = 0.0
    else:
        ret = r + et
        ale = math.log(ret)
        y11 = 1.0 / (r * ret)
        y32 = ((r + ret) * y11 * y11) / r

    return {
        "xi2": xi2, "et2": et2, "q2": q2, "r": r, "r2": r2, "r3": r3,
        "y": y, "d": d, "tt": tt, "alx": alx, "ale": ale,
        "x11": x11, "y11": y11, "x32": x32, "y32": y32,
        "ey": sd / r - (y * q) / r3,
        "ez": cd / r + (d * q) / r3,
        "fy": d / r3 + xi2 * y32 * sd,
        "fz": y / r3 + xi2 * y32 * cd,
        "gy": 2.0 * x11 * sd - y * q * x32,
        "gz": 2.0 * x11 * cd + d * q * x32,
        "hy": d * q * x32 + xi * q * y32 * sd,
        "hz": y * q * x32 + xi * q * y32 * cd,
    }


def _ua_disp(xi: float, et: float, q: float, disl1: float, disl2: float, disl3: float, c0: dict, c2: dict) -> tuple:
    """Part-a (real source) displacement contribution."""
    alp1, alp2 = c0["alp1"], c0["alp2"]
    y11, x11, r, ale, tt, alx = c2["y11"], c2["x11"], c2["r"], c2["ale"], c2["tt"], c2["alx"]

    qx = q * x11
    qy = q * y11
    dux = duy = duz = 0.0

    if disl1 != 0.0:
        dux += (disl1 / PI2) * (tt / 2.0 + alp2 * xi * qy)
        duy += (disl1 / PI2) * (alp2 * q / r)
        duz += (disl1 / PI2) * (alp1 * ale - alp2 * q * qy)
    if disl2 != 0.0:
        dux += (disl2 / PI2) * (alp2 * q / r)
        duy += (disl2 / PI2) * (tt / 2.0 + alp2 * et * qx)
        duz += (disl2 / PI2) * (alp1 * alx - alp2 * q * qx)
    if disl3 != 0.0:
        dux += (disl3 / PI2) * (-alp1 * ale - alp2 * q * qy)
        duy += (disl3 / PI2) * (-alp1 * alx - alp2 * q * qx)
        duz += (disl3 / PI2) * (tt / 2.0 - alp2 * (et * qx + xi * qy))
    return dux, duy, duz


def _ub_disp(xi: float, et: float, q: float, disl1: float, disl2: float, disl3: float, c0: dict, c2: dict) -> tuple:
    """Part-b (image source) displacement contribution."""
    alp3, sd, cd = c0["alp3"], c0["sd"], c0["cd"]
    sdsd, cdcd, sdcd = c0["sdsd"], c0["cdcd"], c0["sdcd"]
    xi2, q2, r, y, d, tt, ale, x11, y11 = c2["xi2"], c2["q2"], c2["r"], c2["y"], c2["d"], c2["tt"], c2["ale"], c2["x11"], c2["y11"]

    rd = r + d
    d11 = 1.0 / (r * rd)
    aj2 = (xi * y * d11) / rd
    aj5 = -(d + (y * y) / rd) * d11

    if cd != 0.0:
        if xi == 0.0:
            ai4 = 0.0
        else:
            x_ = math.sqrt(xi2 + q * q)
            ai4 = (1.0 / cdcd) * (
                (xi / rd) * sdcd
                + 2.0 * math.atan((et * (x_ + q * cd) + x_ * (r + x_) * sd) / (xi * (r + x_) * cd))
            )
        ai3 = ((y * cd) / rd - ale + sd * math.log(rd)) / cdcd
        ak1 = (xi * (d11 - y11 * sd)) / cd
        ak3 = (q * y11 - y * d11) / cd
        aj3 = (ak1 - aj2 * sd) / cd
        aj6 = (ak3 - aj5 * sd) / cd
    else:
        rd2 = rd * rd
        ai3 = (et / rd + (y * q) / rd2 - ale) / 2.0
        ai4 = (xi * y) / rd2 / 2.0
        ak1 = ((xi * q) / rd) * d11
        ak3 = (sd / rd) * (xi2 * d11 - 1.0)
        aj3 = (-xi / rd2) * (q2 * d11 - 0.5)
        aj6 = (-y / rd2) * (xi2 * d11 - 0.5)

    ai1 = (-xi / rd) * cd - ai4 * sd
    ai2 = math.log(rd) + ai3 * sd
    qx = q * x11
    qy = q * y11

    dux = duy = duz = 0.0

    if disl1 != 0.0:
        dux += (disl1 / PI2) * (-xi * qy - tt - alp3 * ai1 * sd)
        duy += (disl1 / PI2) * (-q / r + (alp3 * y * sd) / rd)
        duz += (disl1 / PI2) * (q * qy - alp3 * ai2 * sd)
    if disl2 != 0.0:
        dux += (disl2 / PI2) * (-q / r + alp3 * ai3 * sdcd)
        duy += (disl2 / PI2) * (-et * qx - tt - (alp3 * xi * sdcd) / rd)
        duz += (disl2 / PI2) * (q * qx + alp3 * ai4 * sdcd)
    if disl3 != 0.0:
        dux += (disl3 / PI2) * (q * qy - alp3 * ai3 * sdsd)
        duy += (disl3 / PI2) * (q * qx + (alp3 * xi * sdsd) / rd)
        duz += (disl3 / PI2) * (et * qx + xi * qy - tt - alp3 * ai4 * sdsd)
    return dux, duy, duz


# --------------------------------------------------------------------------- #
# scalar reference path (exact translation — benchmarked, do not touch)
# --------------------------------------------------------------------------- #
def okada_surface_displacement_raw(
    x: float, y: float, depth: float, dip_deg: float,
    al1: float, al2: float, aw1: float, aw2: float,
    u1: float, u2: float, u3: float, alpha: float = 0.5,
) -> SurfaceDisplacement:
    """Raw DC3D-style surface displacement at fault-frame (x, y).

    Returns displacement in the fault frame (x, y, up). singular=True near a
    fault edge (DC3D IRET=1 semantics) carries NaN components.
    """
    c0 = _dccon0(alpha, dip_deg)
    sd, cd = c0["sd"], c0["cd"]

    xi = [x - al1, x - al2]
    if _nearly_zero(xi[0]):
        xi[0] = 0.0
    if _nearly_zero(xi[1]):
        xi[1] = 0.0

    # At z = 0 the real-source and image-source geometries coincide:
    # d = depth (+/- 0), p = y*cd + d*sd, q = y*sd - d*cd.
    d = depth
    p = y * cd + d * sd
    q = y * sd - d * cd
    et = [p - aw1, p - aw2]
    if _nearly_zero(et[0]):
        et[0] = 0.0
    if _nearly_zero(et[1]):
        et[1] = 0.0

    # Reject singular points on the fault edge (DC3D IRET=1 semantics).
    if q == 0.0 and (
        (xi[0] * xi[1] <= 0.0 and et[0] * et[1] == 0.0)
        or (et[0] * et[1] <= 0.0 and xi[0] * xi[1] == 0.0)
    ):
        return SurfaceDisplacement(math.nan, math.nan, math.nan, True)

    r12 = math.sqrt(xi[0] * xi[0] + et[1] * et[1] + q * q)
    r21 = math.sqrt(xi[1] * xi[1] + et[0] * et[0] + q * q)
    r22 = math.sqrt(xi[1] * xi[1] + et[1] * et[1] + q * q)
    # Cross-indexed branch switches, exactly as in the Fortran (KXI(JET), KET(JXI)):
    kxi = [0, 0]
    ket = [0, 0]
    if xi[0] < 0.0 and r21 + xi[1] < RangeSwitchEps:
        kxi[0] = 1
    if xi[0] < 0.0 and r22 + xi[1] < RangeSwitchEps:
        kxi[1] = 1
    if et[0] < 0.0 and r12 + et[1] < RangeSwitchEps:
        ket[0] = 1
    if et[0] < 0.0 and r22 + et[1] < RangeSwitchEps:
        ket[1] = 1

    ux = uy = uz = 0.0
    singular = False

    for k in range(2):          # et index  (Fortran JET)
        for j in range(2):      # xi index  (Fortran JXI)
            c2 = _dccon2(xi[j], et[k], q, sd, cd, kxi[k], ket[j])
            if c2 is None:
                singular = True
                continue
            uax, uay, uaz = _ua_disp(xi[j], et[k], q, u1, u2, u3, c0, c2)
            ubx, uby, ubz = _ub_disp(xi[j], et[k], q, u1, u2, u3, c0, c2)

            # Real-source contribution (rotation with sign flips).
            r_x = -uax
            r_y = -uay * cd + uaz * sd
            r_z = -uay * sd - uaz * cd
            # Image-source contribution.
            i_x = uax + ubx
            i_y = (uay + uby) * cd - (uaz + ubz) * sd
            i_z = (uay + uby) * sd + (uaz + ubz) * cd

            sgn = -1.0 if (j + k) == 1 else 1.0
            ux += sgn * (r_x + i_x)
            uy += sgn * (r_y + i_y)
            uz += sgn * (r_z + i_z)

    return SurfaceDisplacement(ux, uy, uz, singular)


def okada_surface_displacement(x: float, y: float, fault: OkadaFault, alpha: float = 0.5) -> SurfaceDisplacement:
    """Friendly wrapper: fault with TOP EDGE at fault.depth, extending width DOWN-DIP (aw = [-W, 0])."""
    return okada_surface_displacement_raw(
        x, y, fault.depth, fault.dip_deg,
        -fault.length / 2.0, fault.length / 2.0,
        -fault.width, 0.0,
        fault.u1, fault.u2, fault.u3, alpha,
    )


# --------------------------------------------------------------------------- #
# vectorised grid path (same mathematics; verified against the scalar path)
# --------------------------------------------------------------------------- #
def _dccon2_vec(xi, et, q, sd, cd, kxi: int, ket: int):
    """Vectorised dccon2 with point-wise xi/et/q. Returns dict of arrays + singular mask."""
    xi = np.where(np.abs(xi) < NearlyZero, 0.0, xi)
    et = np.where(np.abs(et) < NearlyZero, 0.0, et)
    q = np.where(np.abs(q) < NearlyZero, 0.0, q)

    xi2 = xi * xi
    et2 = et * et
    q2 = q * q
    r2 = xi2 + et2 + q2
    r = np.sqrt(r2)
    singular = r < NearlyZero
    r_safe = np.where(singular, 1.0, r)

    r3 = r_safe * r2
    y = et * cd + q * sd
    d = et * sd - q * cd

    with np.errstate(divide="ignore", invalid="ignore"):
        tt = np.where(q != 0.0, np.arctan((xi * et) / (q * r_safe)), 0.0)
        if kxi == 1:
            alx = -np.log(np.clip(r_safe - xi, 1e-300, None))
            x11 = np.zeros_like(r)
            x32 = np.zeros_like(r)
        else:
            rxi = r_safe + xi
            alx = np.log(rxi)
            x11 = 1.0 / (r_safe * rxi)
            x32 = ((r_safe + rxi) * x11 * x11) / r_safe
        if ket == 1:
            ale = -np.log(np.clip(r_safe - et, 1e-300, None))
            y11 = np.zeros_like(r)
            y32 = np.zeros_like(r)
        else:
            ret = r_safe + et
            ale = np.log(ret)
            y11 = 1.0 / (r_safe * ret)
            y32 = ((r_safe + ret) * y11 * y11) / r_safe

    return {
        "xi2": xi2, "et2": et2, "q2": q2, "r": r_safe, "r3": r3, "y": y, "d": d,
        "tt": tt, "alx": alx, "ale": ale, "x11": x11, "y11": y11, "x32": x32, "y32": y32,
        "singular": singular,
    }


def _ua_vec(xi, et, q, disl1, disl2, disl3, c0, c2):
    alp1, alp2 = c0["alp1"], c0["alp2"]
    y11, x11, r, ale, tt, alx = c2["y11"], c2["x11"], c2["r"], c2["ale"], c2["tt"], c2["alx"]
    qx = q * x11
    qy = q * y11
    dux = np.zeros_like(r)
    duy = np.zeros_like(r)
    duz = np.zeros_like(r)
    if disl1 != 0.0:
        dux += (disl1 / PI2) * (tt / 2.0 + alp2 * xi * qy)
        duy += (disl1 / PI2) * (alp2 * q / r)
        duz += (disl1 / PI2) * (alp1 * ale - alp2 * q * qy)
    if disl2 != 0.0:
        dux += (disl2 / PI2) * (alp2 * q / r)
        duy += (disl2 / PI2) * (tt / 2.0 + alp2 * et * qx)
        duz += (disl2 / PI2) * (alp1 * alx - alp2 * q * qx)
    if disl3 != 0.0:
        dux += (disl3 / PI2) * (-alp1 * ale - alp2 * q * qy)
        duy += (disl3 / PI2) * (-alp1 * alx - alp2 * q * qx)
        duz += (disl3 / PI2) * (tt / 2.0 - alp2 * (et * qx + xi * qy))
    return dux, duy, duz


def _ub_vec(xi, et, q, disl1, disl2, disl3, c0, c2):
    alp3, sd, cd = c0["alp3"], c0["sd"], c0["cd"]
    sdsd, cdcd, sdcd = c0["sdsd"], c0["cdcd"], c0["sdcd"]
    # q2 MUST be unpacked here: the dip-90 branch (cd == 0) uses it in aj3/aj6.
    # Regression-pinned by test_okada.py::test_vertical_fault_vectorised_matches_scalar
    # (a missing unpack was a silent NameError swallowed into a 1e9 fit sentinel).
    xi2, q2, r, y, d, tt, ale, x11, y11 = (
        c2["xi2"], c2["q2"], c2["r"], c2["y"], c2["d"], c2["tt"], c2["ale"], c2["x11"], c2["y11"])

    rd = r + d
    d11 = 1.0 / (r * rd)
    aj2 = (xi * y * d11) / rd
    aj5 = -(d + (y * y) / rd) * d11

    with np.errstate(divide="ignore", invalid="ignore"):
        if cd != 0.0:
            x_ = np.sqrt(xi2 + q * q)
            atan_term = 2.0 * np.arctan((et * (x_ + q * cd) + x_ * (r + x_) * sd) / (xi * (r + x_) * cd))
            ai4 = np.where(xi == 0.0, 0.0, (1.0 / cdcd) * ((xi / rd) * sdcd + atan_term))
            ai3 = ((y * cd) / rd - ale + sd * np.log(rd)) / cdcd
            ak1 = (xi * (d11 - y11 * sd)) / cd
            ak3 = (q * y11 - y * d11) / cd
            aj3 = (ak1 - aj2 * sd) / cd
            aj6 = (ak3 - aj5 * sd) / cd
        else:
            rd2 = rd * rd
            ai3 = (et / rd + (y * q) / rd2 - ale) / 2.0
            ai4 = (xi * y) / rd2 / 2.0
            ak1 = ((xi * q) / rd) * d11
            ak3 = (sd / rd) * (xi2 * d11 - 1.0)
            aj3 = (-xi / rd2) * (q2 * d11 - 0.5)
            aj6 = (-y / rd2) * (xi2 * d11 - 0.5)

        ai1 = (-xi / rd) * cd - ai4 * sd
        ai2 = np.log(rd) + ai3 * sd
        qx = q * x11
        qy = q * y11

        dux = np.zeros_like(r)
        duy = np.zeros_like(r)
        duz = np.zeros_like(r)
        if disl1 != 0.0:
            dux += (disl1 / PI2) * (-xi * qy - tt - alp3 * ai1 * sd)
            duy += (disl1 / PI2) * (-q / r + (alp3 * y * sd) / rd)
            duz += (disl1 / PI2) * (q * qy - alp3 * ai2 * sd)
        if disl2 != 0.0:
            dux += (disl2 / PI2) * (-q / r + alp3 * ai3 * sdcd)
            duy += (disl2 / PI2) * (-et * qx - tt - (alp3 * xi * sdcd) / rd)
            duz += (disl2 / PI2) * (q * qx + alp3 * ai4 * sdcd)
        if disl3 != 0.0:
            dux += (disl3 / PI2) * (q * qy - alp3 * ai3 * sdsd)
            duy += (disl3 / PI2) * (q * qx + (alp3 * xi * sdsd) / rd)
            duz += (disl3 / PI2) * (et * qx + xi * qy - tt - alp3 * ai4 * sdsd)
    return dux, duy, duz


def okada_surface_grid(x, y, fault: OkadaFault, alpha: float = 0.5):
    """Surface displacement over arrays of observation points (fault frame).

    Returns (ux, uy, uz, singular) arrays; singular points carry NaN.
    Must stay consistent with okada_surface_displacement_raw (test-pinned).
    """
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    c0 = _dccon0(alpha, fault.dip_deg)
    sd, cd = c0["sd"], c0["cd"]

    xi = [np.where(np.abs(x - a) < NearlyZero, 0.0, x - a) for a in (-fault.length / 2.0, fault.length / 2.0)]
    d = fault.depth
    p = y * cd + d * sd
    q = y * sd - d * cd
    q = np.where(np.abs(q) < NearlyZero, 0.0, q)
    et = [np.where(np.abs(p - a) < NearlyZero, 0.0, p - a) for a in (-fault.width, 0.0)]

    xi0, xi1 = xi[0], xi[1]
    et0, et1 = et[0], et[1]
    q2v = q * q
    r12 = np.sqrt(xi0 * xi0 + et1 * et1 + q2v)
    r21 = np.sqrt(xi1 * xi1 + et0 * et0 + q2v)
    r22 = np.sqrt(xi1 * xi1 + et1 * et1 + q2v)

    # Cross-indexed branch switches, per point (exact mirror of the raw path;
    # points where a switch fires are at/near the singular set and masked, but
    # we still apply the switch branch there for exactness).
    kxi_flags = [                                     # indexed by et-loop (k), like the raw path
        (xi0 < 0.0) & (r21 + xi1 < RangeSwitchEps),
        (xi0 < 0.0) & (r22 + xi1 < RangeSwitchEps),
    ]
    ket_flags = [                                     # indexed by xi-loop (j)
        (et0 < 0.0) & (r12 + et1 < RangeSwitchEps),
        (et0 < 0.0) & (r22 + et1 < RangeSwitchEps),
    ]

    ux = np.zeros_like(x)
    uy = np.zeros_like(x)
    uz = np.zeros_like(x)
    singular = np.zeros(x.shape, dtype=bool)

    for k in range(2):
        for j in range(2):
            c2 = _dccon2_vec(xi[j], et[k], q, sd, cd, 0, 0)
            if np.any(kxi_flags[k]):
                with np.errstate(divide="ignore", invalid="ignore"):
                    alx_sw = -np.log(np.clip(c2["r"] - xi[j], 1e-300, None))
                    c2["alx"] = np.where(kxi_flags[k], alx_sw, c2["alx"])
                    c2["x11"] = np.where(kxi_flags[k], 0.0, c2["x11"])
                    c2["x32"] = np.where(kxi_flags[k], 0.0, c2["x32"])
            if np.any(ket_flags[j]):
                with np.errstate(divide="ignore", invalid="ignore"):
                    ale_sw = -np.log(np.clip(c2["r"] - et[k], 1e-300, None))
                    c2["ale"] = np.where(ket_flags[j], ale_sw, c2["ale"])
                    c2["y11"] = np.where(ket_flags[j], 0.0, c2["y11"])
                    c2["y32"] = np.where(ket_flags[j], 0.0, c2["y32"])

            uax, uay, uaz = _ua_vec(xi[j], et[k], q, fault.u1, fault.u2, fault.u3, c0, c2)
            ubx, uby, ubz = _ub_vec(xi[j], et[k], q, fault.u1, fault.u2, fault.u3, c0, c2)

            r_x = -uax
            r_y = -uay * cd + uaz * sd
            r_z = -uay * sd - uaz * cd
            i_x = uax + ubx
            i_y = (uay + uby) * cd - (uaz + ubz) * sd
            i_z = (uay + uby) * sd + (uaz + ubz) * cd

            sgn = -1.0 if (j + k) == 1 else 1.0
            corner_sing = c2["singular"]
            valid = ~corner_sing

            ux += sgn * np.where(valid, r_x + i_x, 0.0)
            uy += sgn * np.where(valid, r_y + i_y, 0.0)
            uz += sgn * np.where(valid, r_z + i_z, 0.0)
            singular |= corner_sing

    if np.any(singular):
        ux = np.where(singular, np.nan, ux)
        uy = np.where(singular, np.nan, uy)
        uz = np.where(singular, np.nan, uz)
    return ux, uy, uz, singular


# --------------------------------------------------------------------------- #
# geographic placement + scaling
# --------------------------------------------------------------------------- #
def rake_to_dislocations(slip_m: float, rake_deg: float) -> tuple:
    """Aki & Richards rake mapping: u1 = s*cos(rake), u2 = -s*sin(rake), u3 = 0."""
    r = math.radians(rake_deg)
    return slip_m * math.cos(r), -slip_m * math.sin(r), 0.0


def geo_fault_displacement_enu(fault: GeoFault, centroid_depth_m: float, east_m, north_m):
    """Surface displacement (E, N, Up) at offsets from the centroid's surface projection.

    The fault is placed with its along-dip CENTROID at (0, 0, centroid_depth):
    the top-edge midpoint sits up-dip of the centroid by (W/2)·cos(dip) and
    top_depth = max(500, centroid_depth − (W/2)·sin(dip)).
    Vectorised: east_m/north_m may be scalars or arrays.
    Returns (east, north, up, singular).
    """
    u1, u2, u3 = rake_to_dislocations(fault.slip_m, fault.rake_deg)
    strike = math.radians(fault.strike_deg)

    sx, sy = math.sin(strike), math.cos(strike)   # along-strike unit (E, N)
    dx, dy = math.cos(strike), -math.sin(strike)  # dip-direction unit (E, N)

    half_width = fault.width_m / 2.0
    centroid_offset_dip = half_width * math.cos(math.radians(fault.dip_deg))
    top_depth_m = max(500.0, centroid_depth_m - half_width * math.sin(math.radians(fault.dip_deg)))

    rel_e = np.asarray(east_m, dtype=np.float64) + centroid_offset_dip * dx
    rel_n = np.asarray(north_m, dtype=np.float64) + centroid_offset_dip * dy
    fx = rel_e * sx + rel_n * sy
    fy = rel_e * dx + rel_n * dy

    ux, uy, uz, singular = okada_surface_grid(
        fx, fy, OkadaFault(top_depth_m, fault.dip_deg, fault.length_m, fault.width_m, u1, u2, u3)
    )
    east = ux * sx + uy * dx
    north = ux * sy + uy * dy
    return east, north, uz, singular


def geo_fault_displacement_enu_top_edge(fault: GeoFault, top_edge_depth_m: float, east_m, north_m):
    """Top-edge variant (tsunami finite-fault sources): offsets from the TOP-EDGE midpoint."""
    u1, u2, u3 = rake_to_dislocations(fault.slip_m, fault.rake_deg)
    strike = math.radians(fault.strike_deg)
    sx, sy = math.sin(strike), math.cos(strike)
    dx, dy = math.cos(strike), -math.sin(strike)

    fx = np.asarray(east_m, dtype=np.float64) * sx + np.asarray(north_m, dtype=np.float64) * sy
    fy = np.asarray(east_m, dtype=np.float64) * dx + np.asarray(north_m, dtype=np.float64) * dy

    ux, uy, uz, singular = okada_surface_grid(
        fx, fy, OkadaFault(max(0.0, top_edge_depth_m), fault.dip_deg, fault.length_m, fault.width_m, u1, u2, u3)
    )
    east = ux * sx + uy * dx
    north = ux * sy + uy * dy
    return east, north, uz, singular


def moment_from_mw(mw: float) -> float:
    """Hanks & Kanamori (1979): M0 = 10^(1.5·Mw + 9.1) N·m."""
    return 10.0 ** (1.5 * mw + 9.1)


def rupture_area_from_mw(mw: float) -> float:
    """Wells & Coppersmith (1994, all slip types): log10(A/km²) = 0.91·Mw − 3.49. Returns m²."""
    return 10.0 ** (0.91 * mw - 3.49) * 1e6


def slip_from_moment(moment_nm: float, area_m2: float, shear_modulus: float = 3.0e10) -> float:
    """Uniform slip implied by a seismic moment over a rupture area."""
    return moment_nm / (shear_modulus * area_m2)


__all__ = [
    "OkadaFault", "GeoFault", "SurfaceDisplacement",
    "okada_surface_displacement_raw", "okada_surface_displacement", "okada_surface_grid",
    "geo_fault_displacement_enu", "geo_fault_displacement_enu_top_edge",
    "moment_from_mw", "rupture_area_from_mw", "slip_from_moment", "rake_to_dislocations",
]
