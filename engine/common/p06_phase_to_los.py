# P06 — phase to LOS displacement, per-pixel sigma, ionosphere floor layer
"""Interferometric stack per (direction, track): unwrapped phase -> line-of-sight displacement.

    los_m = sign * phase_sign * wavelength / (4 pi) * phase          (metres, + toward the sensor, later - earlier)

* `sign` is -1 for a pair the product stores with the secondary EARLIER than the reference (P01 marks it
  `flipped`), so every pair in the bundle reads "later minus earlier".
* `sigma_m` is the Cramer-Rao bound from coherence and the effective number of looks,
  sigma_phase = sqrt((1 - g^2) / (2 L g^2)), capped, times wavelength / (4 pi). It is the statistical noise only;
  it says nothing about atmosphere or unwrapping errors. Pixels below `radar.min_coherence` get no sigma.
* `iono_floor_m` is the product's ionosphere-uncertainty layer converted to metres (NaN when absent), the third
  term of the usual detection floor max(3 sigma, 10 mm, iono floor).
* Offsets (M3) are carried over per pair without conversion.

Wavelength, phase sign and looks come from config because the clip carries no HDF5 attributes; they are listed in the
bundle's assumptions.
"""
from __future__ import annotations

import math

import numpy as np

from .context import BuildContext, IfgWork
from .scene_bundle import PairRef


def _get(unit, role):
    for (r, _pol), a in unit.layers.items():
        if r == role:
            return a
    return None


def cramer_rao_sigma_m(coherence: np.ndarray, looks: float, wavelength: float, max_phase: float, min_coh: float) -> np.ndarray:
    g = np.clip(coherence.astype(np.float64), 1e-6, 0.9999)
    with np.errstate(invalid="ignore", divide="ignore"):
        sp = np.sqrt((1.0 - g * g) / (2.0 * looks * g * g))
    sp = np.minimum(sp, max_phase)
    out = (wavelength / (4.0 * math.pi)) * sp
    out[~(coherence >= min_coh)] = np.nan
    return out.astype(np.float32)


def pair_ref(unit, group, bperp) -> PairRef:
    g0 = group.granules[0]
    frames = tuple(sorted({m.frame for m in group.granules if m.frame is not None}))
    ids = tuple(m.ref.granule_id for m in group.granules)
    from .context import iso
    return PairRef(ids, iso(group.ref_time) if group.resolved else None, iso(group.sec_time) if group.resolved else None,
                   group.dt_days, bool(group.flipped), bool(group.resolved), g0.track, g0.direction, frames, bperp)


def run(ctx: BuildContext) -> None:
    r = ctx.cfg.radar
    lam = r.wavelength_m
    k = r.phase_sign * lam / (4.0 * math.pi)
    ctx.assume(f"L-band wavelength {lam} m (the clip carries no centerFrequency)")
    ctx.assume(f"phase sign {r.phase_sign:+d}: toward-sensor displacement = {r.phase_sign:+d} * lambda/(4 pi) * phase "
               "for ref*conj(sec) with ref earlier - unverified on real NISAR data")
    ctx.assume(f"{r.gunw_effective_looks:g} effective looks per 80 m pixel for sigma (not carried by the clip)")
    shape = ctx.grids.g80.shape
    for key, units in ctx.harm_pairs.items():
        phase_units = [u for u in units if _get(u, "unw_phase") is not None]
        off_units = [u for u in units if _get(u, "off_range") is not None or _get(u, "off_azimuth") is not None]
        w = IfgWork(pair_groups=[u.group for u in units])
        if phase_units:
            P = len(phase_units)
            los = np.full((P,) + shape, np.nan, np.float32)
            sig, coh = np.full_like(los, np.nan), np.full_like(los, np.nan)
            cc = np.zeros((P,) + shape, np.uint16)
            ph, io = np.full_like(los, np.nan), np.full_like(los, np.nan)
            sign = np.ones(P, np.float32)
            pairs = []
            for i, u in enumerate(phase_units):
                g = u.group
                sign[i] = -1.0 if g.flipped else 1.0
                phase = _get(u, "unw_phase")
                c = _get(u, "unw_coherence")
                if c is None:
                    ctx.flag("coherence_missing", "warn", "P06", f"{key}: pair {u.granule_ids[0]} has no coherence; sigma undefined",
                             stack=key)
                    c = np.full(shape, np.nan, np.float32)
                ccl = _get(u, "conn_comp")
                if ccl is None:
                    ccl = np.isfinite(phase).astype(np.uint16)      # ungated (P03 flagged cc_missing): 1 where the phase exists
                ph[i] = phase
                los[i] = (sign[i] * k) * phase
                coh[i] = c
                cc[i] = ccl
                sig[i] = cramer_rao_sigma_m(c, r.gunw_effective_looks, lam, r.max_sigma_phase_rad, r.min_coherence)
                sig[i][~np.isfinite(los[i])] = np.nan
                isig = _get(u, "iono_sigma")
                if isig is not None:
                    io[i] = np.abs(isig) * (lam / (4.0 * math.pi))
                pairs.append(pair_ref(u, g, u.scalars.get("bperp_m")))
            w.pairs, w.los_m, w.sigma_m, w.coherence = tuple(pairs), los, sig, coh
            w.conn_comp, w.phase_rad, w.iono_floor_m, w.sign = cc, ph, io, sign
            w.pair_groups = [u.group for u in phase_units]
            w.units = phase_units
            if any(p.flipped for p in pairs):
                ctx.flag("pairs_flipped", "info", "P06", f"{key}: {sum(p.flipped for p in pairs)} pair(s) stored secondary-before-reference; "
                         "sign normalised", stack=key)
        if off_units:
            P = len(off_units)
            a_r, a_a, a_p = (np.full((P,) + shape, np.nan, np.float32) for _ in range(3))
            for i, u in enumerate(off_units):
                for arr, role in ((a_r, "off_range"), (a_a, "off_azimuth"), (a_p, "off_peak")):
                    v = _get(u, role)
                    if v is not None:
                        arr[i] = v
            w.offsets = (tuple(pair_ref(u, u.group, u.scalars.get("bperp_m")) for u in off_units), a_r, a_a, a_p)
        if w.los_m is not None or w.offsets is not None:
            ctx.ifg[key] = w
