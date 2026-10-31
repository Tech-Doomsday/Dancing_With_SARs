"""SceneBundle - the read-only object P01-P13 build and every pipeline receives.

Everything here is a frozen dataclass; every array is read-only (and, when loaded from disk, memory-mapped), so a
pipeline cannot change what another pipeline will read. Pipelines import only this module's types, never the
chain that built them.

Conventions that hold for every array in the bundle
---------------------------------------------------
* Grids: `grids.g80` (80 m, GUNW/GOFF-derived, geometry, terrain context) and `grids.g20` (20 m, GCOV-derived).
  Both live in the tile's UTM CRS and cover the same extent: the 10 km tile plus a halo. The tile proper is
  `grid.core_slices()`. Row 0 is north. `grids.index_map_20_to_80` maps every G20 pixel to its G80 pixel.
* Invalid data is NaN (floats) or 0 (`conn_comp`). Floating point layers are float32. Nothing is silently
  corrected: every correction that *could* be applied is a diagnostic layer in `CorrectionAudit`.
* LOS displacement `los_m` is metres, positive TOWARD the sensor, later date minus earlier date.
* `los_enu` is the unit vector from the ground toward the sensor in (East, North, Up) of the tile's projected
  axes (grid north, not true north).
* Backscatter is dB of RTC gamma0 (`gamma0_db`) and of sigma0 where the product carries the conversion factor.
* Times are ISO-8601 UTC strings ("2026-07-01T00:00:00Z").
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any

import numpy as np

from .frozen import Dual, FrozenDict, freeze
from .grids import GridSet, GridSpec

CONTRACT_VERSION = "1.0"

STATUS_OK, STATUS_PARTIAL, STATUS_EMPTY = "ok", "partial", "empty"


def _freeze_fields(self) -> None:
    for f in fields(self):
        v = getattr(self, f.name)
        nv = freeze(v)
        if nv is not v:
            object.__setattr__(self, f.name, nv)


def _dc(cls):
    """frozen dataclass whose fields are frozen on construction."""
    cls.__post_init__ = _freeze_fields
    return dataclass(frozen=True)(cls)


# ---------------------------------------------------------------------------------------------
# identity of the scene
# ---------------------------------------------------------------------------------------------
@_dc
class TileInfo:
    id: str
    epsg: int
    e0: float                       # SW corner, UTM metres
    n0: float
    size_m: float
    bbox_lonlat: tuple              # (W, S, E, N) of the tile proper
    center_lonlat: tuple            # (lon, lat)

    @staticmethod
    def from_tile(tile) -> "TileInfo":
        return TileInfo(tile.id, int(tile.epsg), float(tile.e0), float(tile.n0), float(tile.size),
                        tuple(float(v) for v in tile.bbox(0.0)), tuple(float(v) for v in tile.center))


@_dc
class RequestInfo:
    as_of: str
    t0: str
    t1: str
    event_window: tuple | None = None        # (start, end) of the triggering event, if any
    triggers: FrozenDict = field(default_factory=FrozenDict)   # scheduler context: {"quake": {...}, "storm": True, "wind_ms": 6.1}
    active_codes: tuple | None = None        # measurement codes the active manifests asked for (None = all)


@_dc
class Flag:
    """Something the consumer should know about. Never raised silently: lands in SceneBundle.flags."""
    code: str                       # machine id, e.g. "fetch_error", "cold_start", "ancillary_missing"
    severity: str                   # info | warn | error
    step: str                       # P01..P13 | chain
    message: str
    detail: FrozenDict = field(default_factory=FrozenDict)


@_dc
class AcquisitionRef:
    """One radar pass over the tile (adjacent frames of the same pass are merged)."""
    granule_ids: tuple
    time: str
    product: str
    track: int | None
    direction: str | None           # "A" | "D"
    frames: tuple = ()


@_dc
class PairRef:
    """One interferogram. `ref_time` < `sec_time` always: pairs the product stores the other way round are
    flagged `flipped` and their phase sign is normalised in P06."""
    granule_ids: tuple
    ref_time: str | None
    sec_time: str | None
    dt_days: float | None           # sec - ref, > 0
    flipped: bool
    times_resolved: bool
    track: int | None
    direction: str | None
    frames: tuple = ()
    bperp_m: float | None = None


# ---------------------------------------------------------------------------------------------
# M1 / M3: interferometric stack (G80)
# ---------------------------------------------------------------------------------------------
@_dc
class CorrectionItem:
    name: str                       # iono | tropo_hydrostatic | tropo_wet | solid_earth_tide
    available: bool
    applied_by_product: bool | None  # None = unknown (the clip carries no processing metadata)
    applied_by_engine: bool          # always False: this chain never applies a correction
    layer_m: np.ndarray | None       # (P, n80, n80) LOS metres, same sign/direction as los_m, DIAGNOSTIC only
    rms_m: tuple = ()                # per pair, over valid pixels
    note: str = ""


@_dc
class CorrectionAudit:
    items: FrozenDict = field(default_factory=FrozenDict)      # name -> CorrectionItem
    residual_los_m: np.ndarray | None = None                   # los_m minus every available diagnostic not applied by the product
    residual_components: tuple = ()                            # names subtracted to make residual_los_m
    note: str = ""
    # per-pair diagnostics of los_m itself, each a tuple of length P (NaN = not computable): planar_ramp_amplitude_mm,
    # ramp_gradient_x_mm_per_km, ramp_gradient_y_mm_per_km (least-squares plane over the tile), elevation_correlation_r
    # (Pearson r between los_m and the DEM). They include any real signal; they are hints about atmosphere, never corrections.
    diagnostics: FrozenDict = field(default_factory=FrozenDict)


@_dc
class PairNetwork:
    n_pairs: int = 0
    n_dates: int = 0
    n_components: int = 0
    largest_component_pairs: int = 0
    largest_component_dates: int = 0
    min_dt_days: float | None = None
    max_dt_days: float | None = None
    connected: bool = False
    dates: tuple = ()
    n_unresolved: int = 0           # pairs whose dates could not be parsed


@_dc
class CoherenceBaseline:
    median: np.ndarray              # (n80, n80) over prior pairs
    mad: np.ndarray
    n: np.ndarray                   # int16 valid count
    latest: np.ndarray              # coherence of the newest pair
    n_pairs: int


@_dc
class InterferometricData:
    pairs: tuple                    # tuple[PairRef], axis-0 order of every array below, oldest sec_time first
    los_m: np.ndarray               # (P, n80, n80) float32
    sigma_m: np.ndarray             # 1-sigma from coherence + looks (Cramer-Rao), NaN where undefined
    coherence: np.ndarray
    conn_comp: np.ndarray           # uint16, 0 = no usable unwrapping
    phase_rad: np.ndarray           # gated unwrapped phase as stored in the product
    iono_floor_m: np.ndarray        # per-pixel ionosphere 1-sigma (NaN where the product has none)
    corrections: CorrectionAudit
    network: PairNetwork
    coherence_baseline: CoherenceBaseline | None = None
    sigma_model: str = "cramer_rao"


@_dc
class OffsetData:
    pairs: tuple
    range_m: np.ndarray             # (P, n80, n80) slant-range offset (as stored; metres in NISAR products)
    azimuth_m: np.ndarray           # along-track offset
    peak: np.ndarray                # correlation surface peak, 0-1


# ---------------------------------------------------------------------------------------------
# M2: backscatter stack (G20)
# ---------------------------------------------------------------------------------------------
@_dc
class Baseline:
    median_db: FrozenDict           # pol -> (n20, n20): median over the prior epochs
    mad_db: FrozenDict              # raw median absolute deviation (not scaled)
    sigma_db: FrozenDict            # robust sigma = max(1.4826 * MAD, speckle floor)
    n: FrozenDict                   # pol -> int16 valid-epoch count
    n_epochs: int                   # prior epochs used
    epoch_indices: tuple            # indices into BackscatterData.epochs
    history_days: float
    cold_start: bool
    seasonal_ok: bool               # harmonic fits allowed (history >= seasonal_min_history_days)
    speckle_floor_db: float | None = None


@_dc
class BackscatterMasks:
    layover: np.ndarray | None      # (n20, n20) bool, static for the stack's geometry; None = could not be evaluated
    shadow: np.ndarray | None
    low_incidence: np.ndarray | None
    high_incidence: np.ndarray | None


@_dc
class BackscatterData:
    epochs: tuple                   # tuple[AcquisitionRef], ascending time
    gamma0_db: FrozenDict           # pol -> (E, n20, n20) float32
    sigma0_db: FrozenDict           # pol -> (E, n20, n20); empty when the product has no rtcGammaToSigmaFactor
    hv_hh_db: np.ndarray | None     # (E, n20, n20) HV dB minus HH dB
    n_looks: np.ndarray | None
    valid: np.ndarray               # (E, n20, n20) bool: finite gamma0 in at least one polarization
    masks: BackscatterMasks
    baseline: Baseline
    current_index: int              # the newest epoch (what pipelines compare against the baseline)


# ---------------------------------------------------------------------------------------------
# geometry / masks per stack
# ---------------------------------------------------------------------------------------------
@_dc
class Geometry:
    source: str                     # radarGrid:GUNW | radarGrid:GCOV | none
    height_source: str              # dem | fallback | none
    los_enu: Dual                   # (3, n, n) ground->sensor unit vector (E, N, Up)
    incidence_deg: Dual             # (n, n) ellipsoid incidence from the product
    heading_deg: float | None       # platform heading, clockwise from grid north
    convergence_deg: float          # grid convergence at the tile centre (true north = grid north + this)
    local_incidence_deg: Dual       # terrain-aware; empty without a DEM
    layover: Dual                   # bool; empty without a DEM
    shadow: Dual
    consistency_deg: float | None   # median |incidence - arccos(Up)|: a large value means the LOS convention is wrong
    vectors_available: bool = True


@_dc
class StableMask:
    mask: Dual                      # bool, True = stable
    criteria: tuple                 # criteria that were applied
    missing: tuple                  # criteria that could not be evaluated (no DEM, no coherence, ...)
    components: FrozenDict = field(default_factory=FrozenDict)   # criterion -> Dual(bool)


@_dc
class StackQuality:
    coverage_g80: float             # fraction of the tile proper with any interferometric data
    coverage_g20: float
    mean_coherence: float | None
    valid_fraction_g80: float       # fraction of the tile proper with finite LOS in the newest pair
    valid_fraction_g20: float       # ... with finite gamma0 in the newest epoch
    layover_shadow_fraction: float | None


@_dc
class StackBundle:
    key: str                        # e.g. "A005": direction + relative orbit
    track: int | None
    direction: str | None
    interferometric: InterferometricData | None
    offsets: OffsetData | None
    backscatter: BackscatterData | None
    geometry: Geometry | None
    stable: StableMask | None
    quality: StackQuality


# ---------------------------------------------------------------------------------------------
# ancillary + routing + depth + provenance
# ---------------------------------------------------------------------------------------------
@_dc
class AncillaryStatus:
    name: str
    available: bool
    source: str = ""
    version: str | None = None
    note: str = ""


@_dc
class AncillaryLayers:
    elevation_m: Dual = field(default_factory=Dual)
    slope_deg: Dual = field(default_factory=Dual)
    aspect_deg: Dual = field(default_factory=Dual)
    curvature_profile: Dual = field(default_factory=Dual)
    curvature_plan: Dual = field(default_factory=Dual)
    hand_m: Dual = field(default_factory=Dual)
    landcover: Dual = field(default_factory=Dual)               # uint8 WorldCover codes, 0 = unknown
    water_occurrence_pct: Dual = field(default_factory=Dual)
    context: FrozenDict = field(default_factory=FrozenDict)     # name -> Dual (tree_cover, builtup, ...)
    facts: FrozenDict = field(default_factory=FrozenDict)       # tile-level scalars from the provider
    status: FrozenDict = field(default_factory=FrozenDict)      # name -> AncillaryStatus
    hand_valid_fraction: float | None = None


@_dc
class TileQuality:
    valid_fraction_g80: float
    valid_fraction_g20: float
    mean_coherence: float | None
    layover_shadow_fraction: float | None
    water_fraction: float | None
    land_fraction: float | None
    water_source: str               # landcover | water_occurrence | backscatter_proxy | unknown
    n_stacks: int


@_dc
class RoutingDecision:
    pipeline: str
    applies: str                    # yes | no | unknown
    reasons: tuple
    needs: tuple = ()               # inputs that were missing when applies == "unknown"
    proxy: bool = False             # decided on a proxy rather than the nominal input


@_dc
class Routing:
    quality: TileQuality
    landcover_hist: FrozenDict      # WorldCover code -> fraction of the tile proper
    decisions: FrozenDict           # pipeline name -> RoutingDecision
    applicable: tuple               # names with applies == "yes"
    unknown: tuple                  # names with applies == "unknown"


@_dc
class DataDepth:
    n_gunw_pairs: int = 0
    n_gunw_pairs_connected: int = 0
    n_gunw_dates: int = 0
    n_gcov_epochs: int = 0
    history_days: float = 0.0
    cold_start: bool = True
    per_stack: FrozenDict = field(default_factory=FrozenDict)

    def metric(self, name: str) -> int:
        return {"pairs": self.n_gunw_pairs, "pairs_connected": self.n_gunw_pairs_connected,
                "epochs": max(self.n_gunw_dates, self.n_gcov_epochs), "dates": self.n_gunw_dates,
                "gcov_epochs": self.n_gcov_epochs}[name]


@_dc
class InputRecord:
    granule_id: str
    product: str
    source: str                     # network | clip-cache | stale-clip-cache | memory
    grids: tuple
    layers: tuple
    sha: str                        # digest of every array that came out of the clip


@_dc
class StepRecord:
    code: str
    name: str
    status: str                     # ok | skipped | error
    seconds: float
    message: str = ""


@_dc
class Provenance:
    bundle_id: str
    contract_version: str
    code_version: str               # digest of engine/common sources
    params_hash: str
    inputs_hash: str
    inputs: tuple
    steps: tuple
    assumptions: tuple              # conventions applied that were not verified against real data
    git_commit: str | None
    created_at: str                 # not part of the bundle id


# ---------------------------------------------------------------------------------------------
# the bundle
# ---------------------------------------------------------------------------------------------
@_dc
class SceneBundle:
    contract_version: str
    bundle_id: str
    status: str                     # ok | partial | empty
    tile: TileInfo
    request: RequestInfo
    epoch: str | None               # newest acquisition used
    grids: GridSet
    stacks: FrozenDict              # key -> StackBundle
    ancillary: AncillaryLayers
    routing: Routing | None
    depth: DataDepth
    capabilities: frozenset         # M1 M2 M3 geometry terrain landcover water_occurrence stable_mask baseline corrections
    flags: tuple
    provenance: Provenance

    # -- conveniences for pipeline authors (read-only views, no state) -----------------------------
    def has(self, capability: str) -> bool:
        return capability in self.capabilities

    @property
    def primary_key(self) -> str | None:
        """The stack with the most data (pairs + epochs)."""
        best, score = None, -1
        for k, s in self.stacks.items():
            n = (len(s.interferometric.pairs) if s.interferometric else 0) + \
                (len(s.backscatter.epochs) if s.backscatter else 0)
            if n > score:
                best, score = k, n
        return best

    def stack(self, key: str | None = None) -> StackBundle | None:
        key = key or self.primary_key
        return self.stacks.get(key) if key else None

    def pairs_spanning(self, t0: str, t1: str) -> list:
        """[(stack_key, index, PairRef)] of pairs whose reference is <= t0 and secondary >= t1."""
        out = []
        for k, s in self.stacks.items():
            if s.interferometric is None:
                continue
            for i, p in enumerate(s.interferometric.pairs):
                if p.times_resolved and p.ref_time <= t0 and p.sec_time >= t1:
                    out.append((k, i, p))
        return out

    def flags_of(self, code: str) -> tuple:
        return tuple(f for f in self.flags if f.code == code)


__all__ = [n for n, v in list(globals().items())
           if isinstance(v, type) and getattr(v, "__module__", "") == __name__] + [
    "CONTRACT_VERSION", "STATUS_OK", "STATUS_PARTIAL", "STATUS_EMPTY", "Dual", "FrozenDict", "GridSet", "GridSpec"]
