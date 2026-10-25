"""Plain data containers shared by every module (no logic, no heavy imports)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Granule:
    """One search hit (an interferogram, a backscatter product, ...)."""
    id: str
    name: str
    url: Optional[str] = None
    level: Optional[str] = None
    t0: str = ""                      # ISO start (for pairs: min(reference, secondary))
    t1: str = ""                      # ISO stop  (for pairs: max(reference, secondary))
    ref: Optional[str] = None         # reference date ISO (pairs only)
    sec: Optional[str] = None         # secondary date ISO (pairs only)
    baseline_days: Optional[float] = None
    size_mb: Optional[float] = None
    dir: str = "?"                    # A / D
    track: Optional[int] = None
    frame: Optional[int] = None
    ref_cycle: Optional[int] = None
    sec_cycle: Optional[int] = None
    pol: Optional[str] = None
    maturity: Optional[str] = None
    footprint: Optional[list] = None  # list of rings, each a list of (lon, lat)
    props: dict = field(default_factory=dict)


@dataclass
class LayerData:
    """One 2-D raster that was actually read (already windowed and, if needed, decimated)."""
    path: str                         # HDF5 path (or npz key turned into a path)
    label: str
    kind: str                         # data | amp | phase | cat
    category: str                     # unwrapped | coherence | components | ... (see anatomy.CATEGORIES)
    array: Any                        # 2-D float32, NaN = invalid / fill
    full_shape: tuple = ()            # shape of the dataset in the file
    dtype: str = ""
    units: Optional[str] = None
    fill: Any = None
    step: int = 1                     # decimation step used while reading
    window: Optional[tuple] = None    # (x0, x1, y0, y1) in file pixels
    corners: Optional[list] = None    # lon/lat of UL, UR, LL corners of the window
    grid: Optional[str] = None
    secs: float = 0.0                 # read time
    remote_bytes: Optional[int] = None
    nz: Optional[int] = None          # for 3-D cubes: number of slices (the middle one is shown)
    source: str = ""                  # which file / clip it came from


@dataclass
class Check:
    """One line in the health table."""
    status: str                       # ok | warn | fail | info
    name: str
    detail: str = ""
