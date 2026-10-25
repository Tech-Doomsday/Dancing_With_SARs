"""P02 - clip: tile + 1-px margin, requested layers only.

Defines the canonical grids (so every epoch of a tile shares identical pixels),
asks the ClipSource for exactly the layers this product needs, and validates the
answer. It never opens files itself - that is retrieval's job.
"""
from __future__ import annotations

from typing import Dict, Sequence, Tuple

from ._raster import canonical_grids, grid_lonlat_bbox
from ._types import BBox, Clip, ClipError, ClipSource, GranuleRecord, Raster
from .config import PreprocConfig
from .state import F_DEGRADED, BundleBuilder

STAGE = "P02"

GUNW_REQUIRED: Tuple[str, ...] = ("unw_phase", "coherence", "conncomp")
GUNW_OPTIONAL: Tuple[str, ...] = (
    "iono_phase", "iono_sigma", "wet_tropo_phase", "hydro_tropo_phase", "solid_earth_tide_phase",
    "mask", "layover_shadow", "incidence", "los_x", "los_y", "rg_offset", "az_offset",
)
GCOV_POLS: Tuple[str, ...] = ("gamma0_hh", "gamma0_hv", "gamma0_vv", "gamma0_vh")


def requested_layers(granule: GranuleRecord) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    if granule.product == "GUNW":
        return GUNW_REQUIRED, GUNW_OPTIONAL
    if granule.product == "GCOV":
        return (), GCOV_POLS
    raise ClipError(f"unsupported product '{granule.product}' for granule {granule.granule_id}")


def clip_granule(source: ClipSource, granule: GranuleRecord, bbox: BBox) -> Clip:
    req, opt = requested_layers(granule)
    wanted = tuple(req) + tuple(opt)
    clip = source.read_clip(granule, bbox, wanted)
    if not isinstance(clip, Clip):
        raise ClipError(f"ClipSource returned {type(clip).__name__}, expected Clip")
    layers: Dict[str, Raster] = {k: v for k, v in clip.layers.items() if k in wanted}
    missing = [k for k in req if k not in layers]
    if missing:
        raise ClipError(f"granule {granule.granule_id}: missing required layers {missing}")
    if granule.product == "GCOV" and not layers:
        raise ClipError(f"granule {granule.granule_id}: no gamma0 polarisation returned")
    return Clip(clip.granule_id, layers, dict(clip.attrs))


def run(state: BundleBuilder) -> Dict:
    cfg: PreprocConfig = state.cfg
    state.grids = canonical_grids(state.inp.tile, cfg)
    # 1 px of G80 around the *canonical* footprint, expressed as a WGS84 box
    state.clip_bbox = grid_lonlat_bbox(state.grids["G80"], pad_px=cfg.clip_margin_px)
    res = state.resolution
    state.clip = clip_granule(state.inp.clip_source, res.current, state.clip_bbox)
    notes = []
    if res.gcov_current is not None:
        try:
            state.bs_clip = clip_granule(state.inp.clip_source, res.gcov_current, state.clip_bbox)
        except ClipError as exc:  # backscatter is optional; absence is recorded, never hidden
            state.bs_clip = None
            notes.append(f"gcov_clip_failed: {exc}")
    state.degraded_mode = bool(state.inp.degraded_mode or state.clip.attrs.get("degraded_mode")
                               or (state.bs_clip is not None and state.bs_clip.attrs.get("degraded_mode")))
    if state.degraded_mode:
        state.flag(F_DEGRADED)
    return {"layers": sorted(state.clip.layers), "gcov_layers": sorted(state.bs_clip.layers) if state.bs_clip else [],
            "clip_bbox": state.clip_bbox, "notes": notes}
