# P09 — align DEM, slope/aspect/curvature, HAND, land cover, water occurrence and context rasters to the canonical grids
"""Everything the `AncillaryProvider` can deliver, on G80 and G20, plus an honest per-layer status.

* DEM-derived layers (elevation, slope, aspect, profile/plan curvature, HAND) come from `terrain_set` (shared with
  P05, computed once). HAND is computed on an extended grid so drainage outside the tile is seen.
* `landcover` (WorldCover codes, uint8, 0 = unknown) and `water_occurrence` are resampled to both grids
  (nearest / bilinear); the context rasters (tree cover, built-up, permafrost, glacier, river buffer, shoreline
  distance) are aligned to G80, and to G20 only where cheap and meaningful.
* A missing or unreadable layer is a `status` entry with `available=False` and a flag - never a made-up default.
  Provider scalar facts (e.g. `volcano_distance_km`) are passed through untouched.
"""
from __future__ import annotations

import numpy as np

from .ancillary import RASTER_NAMES
from .context import BuildContext
from .frozen import Dual, FrozenDict
from .scene_bundle import AncillaryLayers, AncillaryStatus
from .terrain_set import aligned_raster, ensure_hand, get_terrain

# name -> (resampling method on G80, also on G20?)
_CONTEXT = {
    "tree_cover": ("bilinear", False),
    "builtup": ("bilinear", False),
    "permafrost": ("nearest", False),
    "glacier": ("nearest", False),
    "river_buffer": ("nearest", False),
    "shoreline_distance_m": ("bilinear", False),
}


def _core_cover(ctx: BuildContext, cov: np.ndarray, grid) -> float:
    rs, cs = grid.core_slices()
    return float(np.asarray(cov)[rs, cs].mean())


def _as_uint8(a: np.ndarray) -> np.ndarray:
    if a.dtype.kind == "f":
        a = np.nan_to_num(a, nan=0.0)
    return np.clip(a, 0, 255).astype(np.uint8)


def _align(ctx: BuildContext, name: str, method: str, both: bool, integer: bool = False):
    """-> (Dual, AncillaryStatus). Failures become a status + flag, not an exception."""
    try:
        got = aligned_raster(ctx, name, ctx.grids.g80, method)
    except Exception as ex:
        ctx.flag("ancillary_error", "error", "P09", f"{name}: {type(ex).__name__}: {ex}", layer=name)
        return Dual(), AncillaryStatus(name, False, "", None, f"unreadable: {type(ex).__name__}: {ex}")
    if got is None:
        return Dual(), AncillaryStatus(name, False, "", None, "not provided")
    a80, layer, cov80 = got
    a20 = None
    if both:
        try:
            g = aligned_raster(ctx, name, ctx.grids.g20, method)
            a20 = g[0] if g is not None else None
        except Exception as ex:
            ctx.flag("ancillary_error", "error", "P09", f"{name} (G20): {type(ex).__name__}: {ex}", layer=name)
    covered = _core_cover(ctx, cov80, ctx.grids.g80)
    if covered < 0.5:
        ctx.flag("ancillary_partial", "warn", "P09", f"{name}: covers only {covered:.0%} of the tile", layer=name, coverage=covered)
    if integer:
        a80 = _as_uint8(a80)
        a20 = _as_uint8(a20) if a20 is not None else None
    note = f"covers {covered:.0%} of the tile" + ("" if covered > 0 else " (tile outside the raster)")
    return Dual(a80, a20), AncillaryStatus(name, covered > 0, layer.source, layer.version, note)


def run(ctx: BuildContext) -> None:
    status: dict = {}
    ts = get_terrain(ctx)
    if ts.available:
        try:
            ensure_hand(ctx, ts)
        except Exception as ex:
            ctx.flag("ancillary_error", "error", "P09", f"hand: {type(ex).__name__}: {ex}", layer="hand")
    dual = lambda d: Dual(d.get("G80"), d.get("G20"))      # noqa: E731
    if ts.available:
        for n in ("dem", "slope", "aspect", "curvature", "hand"):
            status[n] = AncillaryStatus(n, n != "hand" or bool(ts.hand), ts.source if n != "hand" else "derived:dem",
                                        ts.version, "" if n != "hand" or ts.hand else "HAND could not be computed")
    else:
        status["dem"] = AncillaryStatus("dem", False, "", None, ts.note or "not provided")
        for n in ("slope", "aspect", "curvature", "hand"):
            status[n] = AncillaryStatus(n, False, "", None, "needs dem")
    lc, status["landcover"] = _align(ctx, "landcover", "nearest", True, integer=True)
    wo, status["water_occurrence"] = _align(ctx, "water_occurrence", "bilinear", True)
    context = {}
    for name, (method, both) in _CONTEXT.items():
        d, st = _align(ctx, name, method, both, integer=False)
        status[name] = st
        if d.available:
            context[name] = d
    try:
        facts = dict(ctx.ancillary_provider.facts(ctx.tile) or {})
    except Exception as ex:
        ctx.flag("ancillary_error", "error", "P09", f"facts: {type(ex).__name__}: {ex}", layer="facts")
        facts = {}
    status["facts"] = AncillaryStatus("facts", bool(facts), type(ctx.ancillary_provider).__name__, None,
                                      ", ".join(sorted(facts)) if facts else "none")
    ctx.ancillary = AncillaryLayers(
        elevation_m=dual(ts.elevation) if ts.available else Dual(),
        slope_deg=dual(ts.slope) if ts.available else Dual(),
        aspect_deg=dual(ts.aspect) if ts.available else Dual(),
        curvature_profile=dual(ts.curv_profile) if ts.available else Dual(),
        curvature_plan=dual(ts.curv_plan) if ts.available else Dual(),
        hand_m=dual(ts.hand) if ts.available and ts.hand else Dual(),
        landcover=lc, water_occurrence_pct=wo, context=FrozenDict(context), facts=FrozenDict(facts),
        status=FrozenDict(status), hand_valid_fraction=ts.hand_valid_fraction)
    missing = sorted(n for n in (*RASTER_NAMES, "hand") if n in status and not status[n].available)
    if missing:
        ctx.flag("ancillary_missing", "info", "P09", "ancillary layers not available: " + ", ".join(missing), missing=missing)
