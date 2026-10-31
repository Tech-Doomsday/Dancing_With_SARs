# P08 — backscatter prep: gamma0 / sigma0 in dB, HV-HH, looks, per-epoch validity, static geometry masks
"""GCOV covariance diagonal terms (linear power, RTC gamma0) -> dB layers on G20, one stack of epochs per track.

* `gamma0_db[pol]`  = 10 log10(HHHH | HVHV | ...) ; NaN where the power is <= 0 or not finite.
* `sigma0_db[pol]`  = gamma0_db + 10 log10(rtcGammaToSigmaFactor), only when the product carries the factor.
* `hv_hh_db`        = HV dB - HH dB, the cross-pol ratio (deforestation / wildfire use it).
* `n_looks`         = numberOfLooks on G20 (NaN when absent: P10 then falls back to `temporal.default_looks`).
* `valid`           = finite gamma0 in at least one polarization, per epoch.
* masks             = layover / shadow from P05 (G20) and low / high incidence from the ellipsoid incidence layer.
  They are STATIC for a stack's geometry and are attached, never applied: backscatter stays untouched.

The product's own `mask` layer is carried by P03/P04 but not interpreted here: its bit coding is not in the clip
(see assumptions), so nothing is hidden behind a guess.
"""
from __future__ import annotations

import numpy as np

from .context import BuildContext, CovWork
from .scene_bundle import AcquisitionRef, BackscatterMasks


def _get(unit, role, pol=None):
    return unit.layers.get((role, pol))


def _db(power: np.ndarray) -> np.ndarray:
    out = np.full(power.shape, np.nan, np.float32)
    ok = np.isfinite(power) & (power > 0)
    out[ok] = 10.0 * np.log10(power[ok].astype(np.float64))
    return out


def acquisition_ref(group) -> AcquisitionRef:
    from .context import iso
    ms = group.granules
    frames = tuple(sorted({m.frame for m in ms if m.frame is not None}))
    return AcquisitionRef(tuple(m.ref.granule_id for m in ms), iso(group.time), ms[0].product, ms[0].track,
                          ms[0].direction, frames)


def static_masks(ctx: BuildContext, key: str) -> BackscatterMasks:
    g = ctx.geometry.get(key)
    cfg = ctx.cfg.geometry
    layover = shadow = low = high = None
    if g is not None:
        if g.layover.g20 is not None:
            layover, shadow = np.asarray(g.layover.g20, bool), np.asarray(g.shadow.g20, bool)
        inc = g.incidence_deg.g20
        if inc is None and g.incidence_deg.g80 is not None:
            inc = np.repeat(np.repeat(g.incidence_deg.g80, ctx.grids.ratio, 0), ctx.grids.ratio, 1) \
                if g.incidence_deg.g80.shape[0] * ctx.grids.ratio == ctx.grids.g20.shape[0] else None
        if inc is not None:
            with np.errstate(invalid="ignore"):
                low, high = inc < cfg.min_incidence_deg, inc > cfg.max_incidence_deg
            low, high = low & np.isfinite(inc), high & np.isfinite(inc)
    return BackscatterMasks(layover, shadow, low, high)


def run(ctx: BuildContext) -> None:
    shape = ctx.grids.g20.shape
    ctx.assume("GCOV diagonal covariance terms are RTC gamma0 in linear power; sigma0 = gamma0 * rtcGammaToSigmaFactor "
               "- unverified on real NISAR data")
    ctx.assume("GCOV `mask` layer is carried but not interpreted (bit coding is not in the clip)")
    for key, units in ctx.harm_epochs.items():
        units = sorted((u for u in units if any(r == "cov" for (r, _p) in u.layers)),
                       key=lambda u: u.group.time.timestamp())
        if not units:
            continue
        E = len(units)
        pols = sorted({p for u in units for (r, p) in u.layers if r == "cov"})
        g0 = {p: np.full((E,) + shape, np.nan, np.float32) for p in pols}
        s0 = {p: np.full((E,) + shape, np.nan, np.float32) for p in pols}
        looks = np.full((E,) + shape, np.nan, np.float32)
        have_looks = have_factor = False
        for i, u in enumerate(units):
            fac = _get(u, "rtc_g2s")
            lk = _get(u, "n_looks")
            if lk is not None:
                looks[i], have_looks = lk, True
            for p in pols:
                a = _get(u, "cov", p)
                if a is None:
                    continue
                g0[p][i] = _db(a)
                if fac is not None:
                    have_factor = True
                    f = np.where(np.isfinite(fac) & (fac > 0), fac, np.nan)
                    with np.errstate(invalid="ignore", divide="ignore"):
                        s0[p][i] = g0[p][i] + (10.0 * np.log10(f)).astype(np.float32)
        valid = np.zeros((E,) + shape, bool)
        for p in pols:
            valid |= np.isfinite(g0[p])
        hv = None
        if "HH" in g0 and "HV" in g0:
            hv = (g0["HV"] - g0["HH"]).astype(np.float32)
        elif "VV" in g0 and "VH" in g0:
            hv = (g0["VH"] - g0["VV"]).astype(np.float32)
        if not have_looks:
            ctx.flag("looks_missing", "info", "P08", f"{key}: numberOfLooks not retrieved; speckle floor will use "
                     f"temporal.default_looks={ctx.cfg.temporal.default_looks:g}", stack=key)
        if not have_factor:
            s0 = {}
            ctx.flag("sigma0_unavailable", "info", "P08", f"{key}: rtcGammaToSigmaFactor not retrieved; sigma0 not produced",
                     stack=key)
        for i in range(E):
            if not valid[i].any():
                ctx.flag("epoch_all_invalid", "warn", "P08", f"{key}: epoch {i} has no finite backscatter in the tile", stack=key)
        masks = static_masks(ctx, key)
        w = CovWork(epochs=tuple(acquisition_ref(u.group) for u in units), gamma0_db=g0, sigma0_db=s0, hv_hh_db=hv,
                    n_looks=looks if have_looks else None, valid=valid, masks=masks, current_index=E - 1)
        ctx.cov[key] = w
    ctx.harm_epochs.clear()
