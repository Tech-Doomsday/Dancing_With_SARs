"""Working state shared by the steps of the chain (mutable, never leaves engine/common).

P01..P13 each take a `BuildContext`, read what earlier steps left on it and add their own products. The final
SceneBundle is assembled from it by `chain.assemble`. Keeping the hand-offs as small typed dataclasses (rather
than one big dict) is what makes every step unit-testable on its own.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

import numpy as np

from .ancillary import AncillaryProvider, NullAncillary
from .config import CommonConfig
from .frozen import FrozenDict
from .grids import GridSet
from .scene_bundle import Flag, StepRecord


# ---------------------------------------------------------------------------------------------
# time helpers (everything is UTC, ISO strings in the bundle)
# ---------------------------------------------------------------------------------------------
def parse_time(s: str | datetime) -> datetime:
    if isinstance(s, datetime):
        return s if s.tzinfo else s.replace(tzinfo=timezone.utc)
    s = s.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y%m%dT%H%M%S", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError as ex:
        raise ValueError(f"unparseable time {s!r}") from ex


def iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------------------------
# request
# ---------------------------------------------------------------------------------------------
@dataclass
class BuildRequest:
    tile: Any                                   # engine.tiling.mgrs.Tile
    as_of: datetime
    t0: datetime
    t1: datetime
    event_window: tuple | None = None           # (datetime, datetime)
    triggers: dict = field(default_factory=dict)
    active_codes: set | None = None             # None = every measurement code


# ---------------------------------------------------------------------------------------------
# P01 hand-off
# ---------------------------------------------------------------------------------------------
@dataclass
class GranuleMeta:
    ref: Any                                    # retrieval GranuleRef
    product: str
    times: tuple                                # every timestamp found in the id, in order
    stack_key: str
    track: int | None
    direction: str | None
    frame: int | None
    version: int


@dataclass
class EpochGroup:
    """One backscatter pass (adjacent frames merged)."""
    stack_key: str
    time: datetime
    granules: list


@dataclass
class PairGroup:
    stack_key: str
    ref_time: datetime | None
    sec_time: datetime | None
    dt_days: float | None
    flipped: bool
    resolved: bool
    granules: list


@dataclass
class Resolved:
    gunw: dict = field(default_factory=dict)            # stack_key -> [PairGroup] ascending sec_time
    gcov: dict = field(default_factory=dict)            # stack_key -> [EpochGroup] ascending time
    goff: dict = field(default_factory=dict)
    to_fetch: dict = field(default_factory=dict)        # product -> [GranuleRef]
    dropped_older: int = 0
    search_degraded: bool = False
    search_errors: dict = field(default_factory=dict)   # product -> reason


# ---------------------------------------------------------------------------------------------
# P03 hand-off
# ---------------------------------------------------------------------------------------------
@dataclass
class DLayer:
    role: str
    pol: str | None
    key: str
    epsg: int
    x: np.ndarray
    y: np.ndarray
    data: np.ndarray                            # float32 with NaN fill, or an integer layer (cc / masks)
    heights: np.ndarray | None = None           # radarGrid cube axis
    valid_fraction: float = 0.0


@dataclass
class DGranule:
    meta: GranuleMeta
    layers: list = field(default_factory=list)
    source: str = ""
    dropped: bool = False


# ---------------------------------------------------------------------------------------------
# P04 hand-off
# ---------------------------------------------------------------------------------------------
@dataclass
class HarmUnit:
    """One pair or one epoch on the canonical grids."""
    stack_key: str
    kind: str                                   # pair | epoch
    granule_ids: tuple
    group: Any = None                           # the PairGroup / EpochGroup this unit was built from
    layers: dict = field(default_factory=dict)  # (role, pol) -> ndarray on its canonical grid
    coverage: dict = field(default_factory=dict)  # "G80" | "G20" -> bool (ny, nx): inside the source footprint
    n_overlap: int = 0                          # pixels delivered by more than one frame
    heights: np.ndarray | None = None           # radarGrid cube axis (metres above the ellipsoid)
    scalars: dict = field(default_factory=dict)  # e.g. bperp_m


@dataclass
class HarmGeometry:
    layers: dict = field(default_factory=dict)  # role -> (H, n80, n80) or (n80, n80) on G80
    heights: np.ndarray | None = None
    source: str = "none"


# ---------------------------------------------------------------------------------------------
# P06 / P07 / P08 / P10 work objects
# ---------------------------------------------------------------------------------------------
@dataclass
class IfgWork:
    pair_groups: list
    units: list = field(default_factory=list)       # HarmUnits that carry phase, axis-0 order of the arrays below
    pairs: tuple = ()
    los_m: np.ndarray | None = None
    sigma_m: np.ndarray | None = None
    coherence: np.ndarray | None = None
    conn_comp: np.ndarray | None = None
    phase_rad: np.ndarray | None = None
    iono_floor_m: np.ndarray | None = None
    corrections: Any = None
    network: Any = None
    coherence_baseline: Any = None
    offsets: Any = None
    sign: np.ndarray | None = None              # +1 / -1 per pair (flip normalisation)


@dataclass
class CovWork:
    epochs: tuple = ()
    gamma0_db: dict = field(default_factory=dict)
    sigma0_db: dict = field(default_factory=dict)
    hv_hh_db: np.ndarray | None = None
    n_looks: np.ndarray | None = None
    valid: np.ndarray | None = None
    masks: Any = None
    baseline: Any = None
    current_index: int = -1
    gamma0_lin: dict = field(default_factory=dict)   # linear power, kept until P08 has converted and P10 has used looks


# ---------------------------------------------------------------------------------------------
# context
# ---------------------------------------------------------------------------------------------
class BuildContext:
    def __init__(self, request: BuildRequest, cfg: CommonConfig, grids: GridSet, retriever=None,
                 ancillary: AncillaryProvider | None = None):
        self.request, self.cfg, self.grids = request, cfg, grids
        self.tile = request.tile
        self.retriever = retriever
        self.ancillary_provider = ancillary or NullAncillary()
        self.flags: list[Flag] = []
        self.steps: list[StepRecord] = []
        self.assumptions: list[str] = []
        self.input_records: list = []
        # step products
        self.resolved: Resolved | None = None
        self.fetched: dict = {}                  # product -> FetchResult
        self.clips: dict = {}                    # granule_id -> GranuleClip
        self.decoded: dict = {}                  # granule_id -> DGranule
        self.harm_pairs: dict = {}               # stack_key -> [HarmUnit]
        self.harm_epochs: dict = {}
        self.harm_offsets: dict = {}
        self.harm_geometry: dict = {}            # stack_key -> HarmGeometry
        self.geometry: dict = {}                 # stack_key -> scene_bundle.Geometry
        self.ifg: dict = {}                      # stack_key -> IfgWork
        self.cov: dict = {}                      # stack_key -> CovWork
        self.stable: dict = {}                   # stack_key -> StableMask
        self.ancillary = None                    # scene_bundle.AncillaryLayers (P09)
        self._terrain = None
        self.routing = None
        self.depth = None
        self.provenance_parts: dict = {}
        self.bundle_id: str | None = None

    # -- diagnostics --------------------------------------------------------------------------
    def flag(self, code: str, severity: str, step: str, message: str, **detail) -> None:
        self.flags.append(Flag(code, severity, step, message, FrozenDict({k: _plain(v) for k, v in detail.items()})))

    def assume(self, text: str) -> None:
        if text not in self.assumptions:
            self.assumptions.append(text)

    @property
    def stack_keys(self) -> list:
        keys = set(self.ifg) | set(self.cov)
        return sorted(keys)


def _plain(v: Any) -> Any:
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return float(v)
    if isinstance(v, (list, tuple)):
        return tuple(_plain(x) for x in v)
    if isinstance(v, (set, frozenset)):
        return tuple(sorted(_plain(x) for x in v))
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in v.items()}
    if isinstance(v, datetime):
        return iso(v)
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return str(v)


# ---------------------------------------------------------------------------------------------
# steps
# ---------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Step:
    code: str
    name: str
    run: Callable[[BuildContext], None]
    needs: tuple = ()          # ctx attributes that must be non-empty for the step to make sense; otherwise it is skipped


class ChainError(RuntimeError):
    def __init__(self, step: str, cause: BaseException):
        super().__init__(f"{step} failed: {type(cause).__name__}: {cause}")
        self.step, self.cause = step, cause


class Timer:
    def __enter__(self):
        self.t = time.perf_counter()
        return self

    def __exit__(self, *a):
        self.seconds = time.perf_counter() - self.t
