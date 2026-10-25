# P12 — tile quality, land/water/ice fractions and per-pipeline applicability (yes / no / unknown)
"""Which pipelines make sense for this tile, decided from the bundle's own data and the request's triggers.

Every rule returns one of
    yes      the manifest's `applies:` clause holds
    no       it does not (reasons say why)
    unknown  it cannot be evaluated: an input is missing (`needs` lists what). Never silently turned into yes or no;
             the registry runs `unknown` pipelines only when `registry.run_unknown` is set.
`proxy=True` marks a decision made on a stand-in (e.g. water from backscatter instead of a land-cover map).

Applicability is about the TILE, not about how much history exists: a hopeless time series is the registry's
INSUFFICIENT_EVIDENCE gate (`min_epochs`), not a routing "no".

All areal fractions are measured on the tile proper (the 10 km tile without its halo) on G20; G80 rasters are
upsampled through the grid index map. Landcover histogram keys are WorldCover codes as strings.
"""
from __future__ import annotations

import warnings

import numpy as np

from .ancillary import WC_BUILT, WC_MANGROVE, WC_SNOWICE, WC_TREE, WC_WATER, WC_SHRUB, WC_GRASS
from .context import BuildContext, parse_time
from .frozen import FrozenDict
from .scene_bundle import RoutingDecision, Routing, StackQuality, TileQuality

_VEG = (WC_TREE, WC_SHRUB, WC_GRASS, WC_MANGROVE)
_FOREST = (WC_TREE, WC_MANGROVE)


# ---------------------------------------------------------------------------------------------
# quality
# ---------------------------------------------------------------------------------------------
def _frac(mask: np.ndarray, sl: tuple) -> float:
    return float(np.asarray(mask)[sl].mean())


def stack_quality(ctx: BuildContext, key: str) -> StackQuality:
    rs8, cs8 = ctx.grids.g80.core_slices()
    rs2, cs2 = ctx.grids.g20.core_slices()
    w, c, g = ctx.ifg.get(key), ctx.cov.get(key), ctx.geometry.get(key)
    cov80 = val80 = 0.0
    mean_coh = None
    if w is not None and w.los_m is not None and len(w.los_m):
        fin = np.isfinite(w.los_m[:, rs8, cs8])
        cov80, val80 = float(fin.any(0).mean()), float(fin[-1].mean())
        if w.coherence is not None and np.isfinite(w.coherence[-1, rs8, cs8]).any():
            mean_coh = float(np.nanmean(w.coherence[-1, rs8, cs8]))
    cov20 = val20 = 0.0
    if c is not None and c.valid is not None and len(c.valid):
        cov20, val20 = float(c.valid[:, rs2, cs2].any(0).mean()), float(c.valid[c.current_index, rs2, cs2].mean())
    ls = None
    if g is not None and g.layover.g20 is not None:
        ls = float((np.asarray(g.layover.g20, bool) | np.asarray(g.shadow.g20, bool))[rs2, cs2].mean())
    return StackQuality(cov80, cov20, mean_coh, val80, val20, ls)


# ---------------------------------------------------------------------------------------------
# facts about the tile (computed lazily, once)
# ---------------------------------------------------------------------------------------------
class Facts:
    def __init__(self, ctx: BuildContext):
        self.ctx, self.cfg, self.anc = ctx, ctx.cfg.routing, ctx.ancillary
        self.sl = ctx.grids.g20.core_slices()
        self.sl80 = ctx.grids.g80.core_slices()
        self.triggers = ctx.request.triggers or {}
        self._cache: dict = {}
        self.water20, self.water_source = self._water()
        self.water_proxy = self.water_source == "backscatter_proxy"

    # -- plumbing ---------------------------------------------------------------------------
    def up(self, a80: np.ndarray) -> np.ndarray:
        return np.asarray(a80).ravel()[self.ctx.grids.index_map_20_to_80]

    def raster20(self, name: str):
        """Context raster on G20 (float, NaN = no data) or None."""
        d = self.anc.context.get(name) if self.anc is not None else None
        if d is None:
            return None
        return d.g20 if d.g20 is not None else self.up(d.g80)

    def _lc(self):
        if self.anc is None or not self.anc.landcover.available:
            return None
        lc = self.anc.landcover.g20 if self.anc.landcover.g20 is not None else self.up(self.anc.landcover.g80)
        return lc if float((lc[self.sl] > 0).mean()) >= 0.5 else None

    @property
    def lc(self):
        if "lc" not in self._cache:
            self._cache["lc"] = self._lc()
        return self._cache["lc"]

    def _water(self):
        cfg, a = self.cfg, self.anc
        lc = self.lc
        if lc is not None:
            return (lc == WC_WATER), "landcover"
        if a is not None and a.water_occurrence_pct.available:
            wo = a.water_occurrence_pct.g20 if a.water_occurrence_pct.g20 is not None else self.up(a.water_occurrence_pct.g80)
            if float(np.isfinite(wo[self.sl]).mean()) >= 0.5:
                with np.errstate(invalid="ignore"):
                    return (wo >= 50.0), "water_occurrence"
        # backscatter proxy: calm open water is far darker than any land at L-band co-pol
        for key in self.ctx.stack_keys:
            c = self.ctx.cov.get(key)
            if c is None or not c.gamma0_db:
                continue
            pol = next((p for p in ("HH", "VV") if p in c.gamma0_db), None)
            if pol is None:
                continue
            cur = c.gamma0_db[pol][c.current_index]
            if float(np.isfinite(cur[self.sl]).mean()) >= 0.5:
                with np.errstate(invalid="ignore"):
                    w = cur < cfg.water_proxy_gamma0_db
                return w & np.isfinite(cur), "backscatter_proxy"
        return None, "unknown"

    # -- fractions --------------------------------------------------------------------------
    @property
    def water_frac(self):
        return None if self.water20 is None else _frac(self.water20, self.sl)

    @property
    def land_frac(self):
        w = self.water_frac
        return None if w is None else 1.0 - w

    def land20(self):
        return None if self.water20 is None else ~self.water20

    def lc_hist(self) -> dict:
        lc = self.lc
        if lc is None:
            return {}
        codes, counts = np.unique(lc[self.sl], return_counts=True)
        n = lc[self.sl].size
        return {str(int(c)): float(k) / n for c, k in zip(codes, counts)}

    def lc_frac(self, codes):
        lc = self.lc
        return None if lc is None else _frac(np.isin(lc, codes), self.sl)

    def ice_frac(self):
        parts = []
        lcf = self.lc_frac((WC_SNOWICE,))
        if lcf is not None:
            parts.append(np.isin(self.lc, (WC_SNOWICE,)))
        g = self.raster20("glacier")
        if g is not None:
            parts.append(np.nan_to_num(g, nan=0.0) > 0)
        return None if not parts else _frac(np.logical_or.reduce(parts), self.sl)

    def coherent_land(self):
        """(fraction, proxy) of the tile that is land with coherence >= threshold; None without interferograms."""
        best = None
        for key in self.ctx.stack_keys:
            w = self.ctx.ifg.get(key)
            if w is None or w.coherence is None or not len(w.coherence):
                continue
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                ok = np.nan_to_num(w.coherence[-1], nan=0.0) >= self.cfg.coherence_land_min
            ok20 = self.up(ok)
            land = self.land20()
            f = _frac(ok20 & land, self.sl) if land is not None else _frac(ok20, self.sl)
            if best is None or f > best:
                best = f
        return None if best is None else (best, self.water20 is None or self.water_proxy)

    def mask_frac(self, mask20):
        return _frac(mask20, self.sl)

    def pairs(self):
        return [p for k in self.ctx.stack_keys if (w := self.ctx.ifg.get(k)) is not None for p in w.pairs]


# ---------------------------------------------------------------------------------------------
# rules
# ---------------------------------------------------------------------------------------------
def _d(name, applies, reasons, needs=(), proxy=False) -> RoutingDecision:
    return RoutingDecision(name, applies, tuple(reasons), tuple(needs), bool(proxy))


def _need(name, *needs):
    return _d(name, "unknown", [f"cannot evaluate: {', '.join(needs)} not available"], needs)


def _pct(x):
    return f"{100 * x:.0f}%"


def _coherent_land(name, f: Facts, extra=None):
    cl = f.coherent_land()
    if cl is None:
        return None, _need(name, "M1 interferograms (coherence)")
    frac, proxy = cl
    if frac < f.cfg.coherent_land_min_fraction:
        return None, _d(name, "no", [f"only {_pct(frac)} of the tile is coherent land (< {_pct(f.cfg.coherent_land_min_fraction)})"],
                        proxy=proxy)
    return (frac, proxy), None


def r_earthquake_coseismic(f: Facts):
    n = "earthquake_coseismic"
    got, dec = _coherent_land(n, f)
    if dec:
        return dec
    frac, proxy = got
    why = [f"{_pct(frac)} of the tile is coherent land"]
    ew = f.ctx.request.event_window
    pairs = [p for p in f.pairs() if p.times_resolved]
    if ew and ew[0] is not None:
        t0, t1 = iso_cmp(ew[0]), iso_cmp(ew[1] if ew[1] is not None else ew[0])
        span = [p for p in pairs if p.ref_time <= t0 and p.sec_time >= t1]
        if not span:
            return _d(n, "no", why + ["no interferogram spans the event window"], proxy=proxy)
        return _d(n, "yes", why + [f"{len(span)} pair(s) span the event window"], proxy=proxy)
    return _d(n, "yes", why + ["no event window given: scan mode, any pair qualifies"], proxy=proxy)


def iso_cmp(t) -> str:
    from .context import iso
    return iso(parse_time(t))


def r_land_subsidence(f: Facts):
    got, dec = _coherent_land("land_subsidence", f)
    if dec:
        return dec
    return _d("land_subsidence", "yes", [f"{_pct(got[0])} of the tile is coherent land"], proxy=got[1])


def r_volcanic_deformation(f: Facts):
    n, cfg = "volcanic_deformation", f.cfg
    dist = (f.anc.facts.get("volcano_distance_km") if f.anc is not None else None)
    if dist is not None and dist <= cfg.volcano_radius_km:
        return _d(n, "yes", [f"nearest S7 volcano {dist:.0f} km away (<= {cfg.volcano_radius_km:g} km)"])
    dem = f.anc is not None and f.anc.elevation_m.available
    if dem:
        e = f.anc.elevation_m.g80[f.sl80]
        hi, rel = float(np.nanmax(e)), float(np.nanmax(e) - np.nanmin(e))
        if hi >= cfg.high_terrain_elev_m and rel >= cfg.high_terrain_relief_m:
            return _d(n, "yes", [f"high terrain (max {hi:.0f} m, relief {rel:.0f} m): global radial-pattern scan"]
                      + ([f"nearest S7 volcano {dist:.0f} km away"] if dist is not None else []), proxy=True)
    if dist is None and not dem:
        return _need(n, "S7 volcano distance (facts.volcano_distance_km)", "dem")
    why = [f"nearest S7 volcano {dist:.0f} km away (> {cfg.volcano_radius_km:g} km)" if dist is not None
           else "volcano distance unknown"]
    if not dem:
        return _d(n, "unknown", why + ["no DEM to test high terrain"], ("dem",))
    return _d(n, "no", why + ["terrain not high enough for the radial scan"])


def r_glacier_ice_flow(f: Facts):
    n = "glacier_ice_flow"
    ice = f.ice_frac()
    if ice is None:
        return _need(n, "landcover or glacier outlines (S8)")
    if ice >= f.cfg.ice_min_fraction:
        return _d(n, "yes", [f"{_pct(ice)} of the tile is ice/snow"])
    return _d(n, "no", [f"only {_pct(ice)} of the tile is ice/snow (< {_pct(f.cfg.ice_min_fraction)})"])


def r_permafrost_thaw(f: Facts):
    n = "permafrost_thaw"
    pf = f.raster20("permafrost")
    if pf is None:
        return _need(n, "S12 permafrost zones")
    land = f.land20()
    zone = np.nan_to_num(pf, nan=0.0) > 0
    frac = _frac(zone & land, f.sl) if land is not None else _frac(zone, f.sl)
    if frac >= f.cfg.land_min_fraction:
        return _d(n, "yes", [f"{_pct(frac)} of the tile is land inside a permafrost zone"], proxy=land is None)
    return _d(n, "no", [f"{_pct(frac)} of the tile is in a permafrost zone (< {_pct(f.cfg.land_min_fraction)})"], proxy=land is None)


def r_landslide(f: Facts):
    n, cfg = "landslide", f.cfg
    if f.anc is None or not f.anc.slope_deg.available:
        return _need(n, "dem")
    ok = f.anc.slope_deg.g20 >= cfg.slope_landslide_deg
    why_not = []
    land = f.land20()
    if land is not None:
        ok = ok & land
    else:
        why_not.append("water not excluded (no water source)")
    for key in f.ctx.stack_keys:               # layover / shadow of the best stack removed
        g = f.ctx.geometry.get(key)
        if g is not None and g.layover.g20 is not None:
            ok = ok & ~(np.asarray(g.layover.g20, bool) | np.asarray(g.shadow.g20, bool))
            break
    frac = _frac(ok, f.sl)
    if frac >= cfg.slope_landslide_min_fraction:
        return _d(n, "yes", [f"{_pct(frac)} of the tile is land at >= {cfg.slope_landslide_deg:g} deg slope (not layover/shadow)"]
                  + why_not, proxy=land is None or f.water_proxy)
    return _d(n, "no", [f"{_pct(frac)} of the tile is steep land (< {_pct(cfg.slope_landslide_min_fraction)})"] + why_not,
              proxy=land is None or f.water_proxy)


def r_flood_inundation(f: Facts):
    n, cfg = "flood_inundation", f.cfg
    land = f.land_frac
    if land is None:
        return _need(n, "land/water (landcover, water occurrence or backscatter)")
    ice = f.ice_frac()
    why = [f"{_pct(land)} of the tile is land"]
    if land < cfg.land_min_fraction:
        return _d(n, "no", why + [f"less than {_pct(cfg.land_min_fraction)} land"], proxy=f.water_proxy)
    if ice is not None and ice > cfg.permanent_ice_max_fraction:
        return _d(n, "no", why + [f"{_pct(ice)} of the tile is permanent ice"], proxy=f.water_proxy)
    return _d(n, "yes", why + ([f"{_pct(ice)} ice"] if ice is not None else ["ice cover unknown"]), proxy=f.water_proxy)


def r_deforestation(f: Facts):
    n, cfg = "deforestation", f.cfg
    parts = []
    if f.lc is not None:
        parts.append(np.isin(f.lc, _FOREST))
    tc = f.raster20("tree_cover")
    if tc is not None:
        with np.errstate(invalid="ignore"):
            parts.append(np.nan_to_num(tc, nan=0.0) >= cfg.tree_cover_min_pct)
    if not parts:
        return _need(n, "landcover or S4 tree cover")
    land = f.land20()
    m = np.logical_or.reduce(parts)
    if land is not None:
        m = m & land
    frac = _frac(m, f.sl)
    if frac >= cfg.forest_min_fraction:
        return _d(n, "yes", [f"{_pct(frac)} of the tile is forest or >= {cfg.tree_cover_min_pct:g}% tree cover"], proxy=f.water_proxy)
    return _d(n, "no", [f"{_pct(frac)} forest / tree cover (< {_pct(cfg.forest_min_fraction)})"], proxy=f.water_proxy)


def r_wildfire_burn_scar(f: Facts):
    n, cfg = "wildfire_burn_scar", f.cfg
    veg = f.lc_frac(_VEG)
    if veg is None:
        return _need(n, "landcover")
    if veg >= cfg.vegetated_min_fraction:
        return _d(n, "yes", [f"{_pct(veg)} of the tile is forest, shrub or grass"])
    return _d(n, "no", [f"{_pct(veg)} vegetated (< {_pct(cfg.vegetated_min_fraction)})"])


def _coast_fracs(f: Facts):
    """(fraction within the coast buffer, fraction in the coastal strip, proxy) or None."""
    cfg = f.cfg
    sd = f.raster20("shoreline_distance_m")
    if sd is not None:
        near = np.isfinite(sd) & (np.abs(sd) <= cfg.coast_buffer_km * 1000.0)
        strip = np.isfinite(sd) & (sd >= 0) & (sd <= cfg.coast_buffer_km * 1000.0)
        if f.anc is not None and f.anc.elevation_m.available:
            strip = strip & (f.anc.elevation_m.g20 <= cfg.coastal_elev_max_m)
            return _frac(near, f.sl), _frac(strip, f.sl), False
        return _frac(near, f.sl), None, False
    wf = f.water_frac
    if wf is not None and cfg.coastal_min_fraction <= wf <= 1.0 - cfg.coastal_min_fraction:
        return 1.0, None, True                         # proxy: land and water both present inside the tile
    if wf is not None:
        return 0.0, None, True
    return None


def r_coastal_erosion(f: Facts):
    n = "coastal_erosion"
    got = _coast_fracs(f)
    if got is None:
        return _need(n, "S10 shoreline distance or a land/water source")
    near, _strip, proxy = got
    if near >= f.cfg.coastal_min_fraction:
        return _d(n, "yes", [f"{_pct(near)} of the tile lies within {f.cfg.coast_buffer_km:g} km of the shoreline"
                              if not proxy else "land and water both inside the tile (shoreline distance not available)"],
                  proxy=proxy)
    return _d(n, "no", ["tile is not within the coast buffer" if not proxy else "tile is all land or all water"], proxy=proxy)


def r_tsunami_inundation(f: Facts):
    n, cfg = "tsunami_inundation", f.cfg
    tr = f.triggers
    if not (tr.get("quake") or tr.get("tsunami")):
        return _d(n, "no", ["no earthquake / tsunami trigger in the request"])
    got = _coast_fracs(f)
    if got is None:
        return _need(n, "S10 shoreline distance")
    near, strip, proxy = got
    if strip is None:
        return _d(n, "unknown", ["trigger present, but the elevation band needs a DEM"], ("dem",), proxy)
    if strip >= cfg.coastal_min_fraction:
        return _d(n, "yes", [f"trigger present; {_pct(strip)} of the tile is coastal strip (<= {cfg.coast_buffer_km:g} km, "
                              f"<= {cfg.coastal_elev_max_m:g} m)"], proxy=proxy)
    return _d(n, "no", ["trigger present, but the tile has no coastal low-lying strip"], proxy=proxy)


def r_river_erosion(f: Facts):
    n, cfg = "river_erosion", f.cfg
    parts = []
    lo, hi = cfg.river_occurrence_pct
    if f.anc is not None and f.anc.water_occurrence_pct.available:
        wo = f.anc.water_occurrence_pct.g20 if f.anc.water_occurrence_pct.g20 is not None else f.up(f.anc.water_occurrence_pct.g80)
        with np.errstate(invalid="ignore"):
            parts.append((wo >= lo) & (wo <= hi))
    rb = f.raster20("river_buffer")
    if rb is not None:
        parts.append(np.nan_to_num(rb, nan=0.0) > 0)
    if not parts:
        return _need(n, "S11 river buffer or S3 water occurrence")
    frac = _frac(np.logical_or.reduce(parts), f.sl)
    if frac >= cfg.river_min_fraction:
        return _d(n, "yes", [f"{_pct(frac)} of the tile is river corridor / intermittent water"])
    return _d(n, "no", [f"{_pct(frac)} river corridor (< {_pct(cfg.river_min_fraction)})"])


def _ocean(f: Facts):
    """(ocean fraction, offshore fraction or None, proxy)."""
    wf = f.water_frac
    if wf is None:
        return None
    sd = f.raster20("shoreline_distance_m")
    off = None
    if sd is not None:
        off = _frac(np.isfinite(sd) & (sd <= -f.cfg.ocean_min_offshore_km * 1000.0) & f.water20, f.sl)
    return wf, off, f.water_proxy or sd is None


def r_tropical_cyclone(f: Facts):
    n, cfg = "tropical_cyclone", f.cfg
    tr = f.triggers
    if not (tr.get("storm") or tr.get("high_wind")):
        return _d(n, "no", ["no active/recent storm or high-wind trigger in the request"])
    o = _ocean(f)
    if o is None:
        return _need(n, "land/water source")
    wf, off, proxy = o
    if wf < cfg.ocean_min_fraction:
        return _d(n, "no", [f"only {_pct(wf)} of the tile is water"], proxy=proxy)
    if off is None:
        return _d(n, "unknown", ["storm trigger and open water, but distance offshore needs S10 shoreline distance"],
                  ("shoreline_distance_m",), proxy)
    if off >= cfg.ocean_min_fraction:
        return _d(n, "yes", [f"storm trigger; {_pct(off)} of the tile is water > {cfg.ocean_min_offshore_km:g} km offshore"])
    return _d(n, "no", [f"only {_pct(off)} of the tile is > {cfg.ocean_min_offshore_km:g} km offshore"])


def r_oil_spill(f: Facts):
    n, cfg = "oil_spill", f.cfg
    o = _ocean(f)
    if o is None:
        return _need(n, "land/water source")
    wf, _off, proxy = o
    if wf < cfg.ocean_min_fraction:
        return _d(n, "no", [f"only {_pct(wf)} of the tile is water"], proxy=proxy)
    wind = f.triggers.get("wind_ms")
    if wind is not None:
        if not (cfg.wind_min_ms <= float(wind) <= cfg.wind_max_ms):
            return _d(n, "no", [f"wind {float(wind):.1f} m/s outside {cfg.wind_min_ms:g}-{cfg.wind_max_ms:g} m/s"], proxy=proxy)
        return _d(n, "yes", [f"{_pct(wf)} water; wind {float(wind):.1f} m/s inside the window"], proxy=proxy)
    return _d(n, "yes", [f"{_pct(wf)} water; wind not known at routing time (the pipeline's own R5 fetch applies the "
                          f"{cfg.wind_min_ms:g}-{cfg.wind_max_ms:g} m/s window)"], proxy=proxy)


def r_urban_damage(f: Facts):
    n, cfg = "urban_damage", f.cfg
    parts = []
    if f.lc is not None:
        parts.append(f.lc == WC_BUILT)
    b = f.raster20("builtup")
    if b is not None:
        parts.append(np.nan_to_num(b, nan=0.0) >= cfg.builtup_pct_threshold)
    if not parts:
        return _need(n, "S9 built-up surface or landcover")
    frac = _frac(np.logical_or.reduce(parts), f.sl)
    if frac >= cfg.builtup_min_fraction:
        return _d(n, "yes", [f"{_pct(frac)} of the tile is built-up"])
    return _d(n, "no", [f"{_pct(frac)} built-up (< {_pct(cfg.builtup_min_fraction)})"])


def r_iceberg_calving_drift(f: Facts):
    n, cfg = "iceberg_calving_drift", f.cfg
    wf, ice = f.water_frac, f.ice_frac()
    dist = f.anc.facts.get("ice_front_distance_km") if f.anc is not None else None
    if wf is None:
        return _need(n, "land/water source")
    if wf < cfg.iceberg_water_min_fraction:
        return _d(n, "no", [f"only {_pct(wf)} of the tile is water"], proxy=f.water_proxy)
    if dist is not None and dist <= cfg.ice_front_radius_km:
        return _d(n, "yes", [f"water present; ice front {dist:.0f} km away"], proxy=f.water_proxy)
    if ice is None and dist is None:
        return _need(n, "glacier/ice outlines (S8) or ice-front distance")
    if ice is not None and ice >= cfg.ice_min_fraction:
        return _d(n, "yes", [f"water {_pct(wf)} and ice {_pct(ice)} in the tile"], proxy=f.water_proxy)
    return _d(n, "no", ["no ice in or near the tile"], proxy=f.water_proxy)


RULES = {fn.__name__[2:]: fn for fn in (
    r_earthquake_coseismic, r_land_subsidence, r_volcanic_deformation, r_glacier_ice_flow, r_permafrost_thaw,
    r_landslide, r_flood_inundation, r_deforestation, r_wildfire_burn_scar, r_coastal_erosion, r_tsunami_inundation,
    r_river_erosion, r_tropical_cyclone, r_oil_spill, r_urban_damage, r_iceberg_calving_drift)}


# ---------------------------------------------------------------------------------------------
def run(ctx: BuildContext) -> None:
    f = Facts(ctx)
    quals = {k: stack_quality(ctx, k) for k in ctx.stack_keys}
    ls = [q.layover_shadow_fraction for q in quals.values() if q.layover_shadow_fraction is not None]
    mc = [q.mean_coherence for q in quals.values() if q.mean_coherence is not None]
    tq = TileQuality(
        valid_fraction_g80=max((q.valid_fraction_g80 for q in quals.values()), default=0.0),
        valid_fraction_g20=max((q.valid_fraction_g20 for q in quals.values()), default=0.0),
        mean_coherence=float(np.mean(mc)) if mc else None,
        layover_shadow_fraction=max(ls) if ls else None,
        water_fraction=f.water_frac, land_fraction=f.land_frac, water_source=f.water_source, n_stacks=len(quals))
    if f.water_proxy:
        ctx.flag("water_proxy", "warn", "P12", f"no land cover / water occurrence: water inferred from backscatter < "
                 f"{ctx.cfg.routing.water_proxy_gamma0_db:g} dB (proxy)")
    if f.water_source == "unknown":
        ctx.flag("water_unknown", "warn", "P12", "no land/water information at all; routing decisions that need it are 'unknown'")
    decisions = {}
    for name, rule in RULES.items():
        try:
            decisions[name] = rule(f)
        except Exception as ex:                                  # a broken rule must not hide the other fifteen
            ctx.flag("routing_rule_error", "error", "P12", f"{name}: {type(ex).__name__}: {ex}", pipeline=name)
            decisions[name] = _d(name, "unknown", [f"rule raised {type(ex).__name__}: {ex}"], ("routing rule",))
    ctx.routing = Routing(tq, FrozenDict(f.lc_hist()), FrozenDict(decisions),
                          tuple(sorted(n for n, d in decisions.items() if d.applies == "yes")),
                          tuple(sorted(n for n, d in decisions.items() if d.applies == "unknown")))
