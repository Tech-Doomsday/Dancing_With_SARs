"""3.01 earthquake_coseismic — dataclasses and constants (ported types.ts subset).

Defaults are the port-authoritative values (see docs/IMPLEMENTATION_PLAN.md,
Appendix A): the app's DEFAULT_PARAMS plus the engine manifest's floors.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

# Attribution screening heuristics (ported from the app; exploratory, never calibrated).
AMPLITUDE_RATIO_FACTOR = 3.0     # predicted vs measured amplitude within a factor of 3
PATTERN_CORRELATION_MIN = 0.25   # Pearson r floor for the forward-model screen
ATMOSPHERIC_PEAK_MM = 30.0       # above this, an atmospheric cause stays on the table

SHEAR_MODULUS_PA = 3.0e10        # Okada uniform half-space default


@dataclass(frozen=True)
class EqParams:
    """Pipeline parameters. Overrides enter via bundle.request.triggers["eq_params"]."""

    coherence_threshold: float = 0.5
    reference_strategy: str = "tileMedian"        # or "farField" / "fullFrameMedian" (app, frame-scale)
    far_field_margin: float = 0.12
    detrend: bool = False
    detection_floor_m: float = 0.01               # manifest: 10 mm
    significance_k: float = 3.0                   # manifest: 3 sigma
    min_valid_pixels: int = 100
    min_reference_pixels: int = 50
    cluster_min_pixels: int = 4
    max_event_distance_km: float = 400.0          # manifest R1 radius
    event_buffer_days: float = 1.0                # R1 query window: ref-1d .. sec+1d
    forward_subgrid_max_points: int = 400_000
    forward_top_events: int = 3
    credible_pixel_factor: int = 4
    cycle_jump_fraction: float = 0.5              # |los| > fraction * lambda  =>  suspect
    # uncatalogued-source branch (docs 8.6) - optimizer budget + convergence rule
    uncat_max_points: int = 500                   # quadtree leaves fed to the optimizer
    uncat_scan_strikes: int = 12                  # coarse circular strike grid (every 15 deg)
    uncat_scan_seeds: int = 1                     # independent DE seeds per strike node
    uncat_scan_popsize: int = 8                   # per-strike 5-D differential evolution
    uncat_scan_iter: int = 25
    uncat_refine_top: int = 12                     # candidates refined locally over all 6 parameters
    uncat_refine_nfev: int = 60                   # least-squares refinement budget (x7 evals / Jacobian)
    uncat_rms_ratio: float = 0.7                  # residual RMS must be < ratio x field RMS
    uncat_seed: int = 20261021

    @classmethod
    def from_triggers(cls, triggers) -> "EqParams":
        """Merge UI/API overrides carried on the bundle build request."""
        import math
        overrides = {}
        try:
            raw = dict(triggers or {}).get("eq_params") or {}
        except TypeError:
            raw = {}
        fields = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        for k, v in dict(raw).items():
            if k in fields and v is not None:
                overrides[k] = v
        # bound the overrides the way the reference API did (zod ranges)
        if "coherence_threshold" in overrides:
            overrides["coherence_threshold"] = min(1.0, max(0.0, float(overrides["coherence_threshold"])))
        if "far_field_margin" in overrides:
            overrides["far_field_margin"] = min(0.4, max(0.02, float(overrides["far_field_margin"])))
        if "detection_floor_m" in overrides:
            overrides["detection_floor_m"] = min(0.2, max(0.001, float(overrides["detection_floor_m"])))
        if "significance_k" in overrides:
            overrides["significance_k"] = min(6.0, max(1.0, float(overrides["significance_k"])))
        return cls(**overrides)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class EventRecord:
    """One ComCat event (R1 adapter output / fixture provider payload)."""

    id: str
    magnitude: float | None
    place: str
    time: str                    # ISO-8601 UTC
    lat: float
    lon: float
    depth_km: float | None
    url: str | None = None
    nodal_planes: tuple = ()     # ((strike, dip, rake), ...) — 0, 1 or 2 planes

    @classmethod
    def from_mapping(cls, m: dict) -> "EventRecord":
        planes = m.get("nodal_planes") or ()
        return cls(
            id=str(m["id"]),
            magnitude=m.get("magnitude"),
            place=str(m.get("place") or ""),
            time=str(m.get("time") or ""),
            lat=float(m["lat"]),
            lon=float(m["lon"]),
            depth_km=(float(m["depth_km"]) if m.get("depth_km") is not None else None),
            url=(str(m["url"]) if m.get("url") else None),
            nodal_planes=tuple(tuple(float(c) for c in p) for p in planes),
        )


@dataclass(frozen=True)
class EventsResult:
    """Fetch outcome — a RESULT, never an exception (READINESS contract)."""

    events: tuple = field(default_factory=tuple)   # tuple[EventRecord]
    source: str = "none"
    degraded: bool = False
    error: str | None = None


@dataclass
class PairAnalysis:
    """Output of prep.analyse_pair for one interferometric pair (core grid)."""

    stack_key: str
    pair_index: int
    granule_ids: tuple
    ref_time: str | None
    sec_time: str | None
    mask: object = None            # bool (n, n) — True = valid measurement
    disp: object = None            # f32, referenced displacement, NaN off-mask, + = TOWARD sensor
    sigma_eff: object = None       # f32, sqrt(sigma_m^2 + ref_sigma^2), NaN off-mask
    ref_offset_m: float = 0.0
    ref_sigma_m: float = 0.0
    warnings: list = field(default_factory=list)
    flags: list = field(default_factory=list)      # {"code", "message", "n"} diagnostics


@dataclass
class DetectionSummary:
    """Engine-relevant subset of the app's ChangeSummary (ported detection stats)."""

    valid_pixels: int = 0
    masked_pixel_count: int = 0
    detected_pixels: int = 0
    threshold_median_m: float = 0.0
    affected_area_km2: float = 0.0
    toward_area_km2: float = 0.0
    away_area_km2: float = 0.0
    max_toward_m: float = 0.0
    max_away_m: float = 0.0
    median_sigma_m: float = 0.0
    median_sigma_anomaly_m: float = 0.0
    anomaly_centroid: tuple | None = None          # (x, y) metres
    anomaly_orientation_deg: float | None = None
    anomaly_elongation: float | None = None
    displacement_integral_m_km2: float = 0.0
    coherence_median: float = 0.0
    coherence_mean: float = 0.0
    valid_fraction: float = 0.0
    pixel_area_km2: float = 0.0
    analysed_area_km2: float = 0.0
    clusters: dict = field(default_factory=dict)   # {count, largest_km2, sizes_km2, min_pixels}
    null_screen: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class LayerPayload:
    """Evidence raster crossing the API (see docs/IMPLEMENTATION_PLAN.md 6.4)."""

    name: str
    dtype: str                     # "<f4" | "<u1"
    shape: tuple
    data_b64: str
    units: str
    colormap: str                  # diverging_teal_terracotta | sequential | binary
    vmin: float | None = None
    vmax: float | None = None
    note: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["shape"] = list(self.shape)
        return d
