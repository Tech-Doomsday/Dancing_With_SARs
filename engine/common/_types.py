"""Shared value types for engine.common (numpy-only, no I/O).

Nothing here imports from engine.retrieval, engine.pipelines or engine.lib.
The seam to retrieval is the ``ClipSource`` protocol below: retrieval (or a thin
adapter owned by whoever wires the registry) implements ``read_clip``; this
package never reaches into retrieval code.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, Mapping, Optional, Protocol, Sequence, Tuple, runtime_checkable

import numpy as np

BBox = Tuple[float, float, float, float]  # (west, south, east, north), EPSG:4326


class PreprocError(Exception):
    """Base class for all preprocessing failures (never swallowed silently)."""


class GranuleResolutionError(PreprocError):
    """P01: no usable granule / pair for the tile."""


class ClipError(PreprocError):
    """P02/P03: clip missing, malformed, or missing required layers."""


class InsufficientInputError(PreprocError):
    """Input exists but cannot support a bundle (e.g. clip does not overlap tile)."""


@dataclass(frozen=True)
class Grid:
    """North-up regular grid. (x0, y0) is the outer upper-left corner of pixel (0, 0)."""

    name: str
    crs: str
    x0: float
    y0: float
    dx: float
    dy: float
    ny: int
    nx: int

    @property
    def shape(self) -> Tuple[int, int]:
        return (self.ny, self.nx)

    @property
    def bounds(self) -> Tuple[float, float, float, float]:
        """(xmin, ymin, xmax, ymax) in grid CRS units."""
        return (self.x0, self.y0 - self.ny * self.dy, self.x0 + self.nx * self.dx, self.y0)

    def x_centers(self) -> np.ndarray:
        return self.x0 + (np.arange(self.nx) + 0.5) * self.dx

    def y_centers(self) -> np.ndarray:
        return self.y0 - (np.arange(self.ny) + 0.5) * self.dy

    def padded(self, k: int) -> "Grid":
        return Grid(self.name, self.crs, self.x0 - k * self.dx, self.y0 + k * self.dy,
                    self.dx, self.dy, self.ny + 2 * k, self.nx + 2 * k)

    def same_geometry(self, other: "Grid", tol: float = 1e-6) -> bool:
        return (self.crs == other.crs and self.shape == other.shape
                and abs(self.x0 - other.x0) <= tol * max(1.0, self.dx)
                and abs(self.y0 - other.y0) <= tol * max(1.0, self.dy)
                and abs(self.dx - other.dx) <= tol and abs(self.dy - other.dy) <= tol)

    def to_dict(self) -> Dict[str, Any]:
        return dict(name=self.name, crs=self.crs, x0=self.x0, y0=self.y0, dx=self.dx,
                    dy=self.dy, ny=self.ny, nx=self.nx)

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> "Grid":
        return Grid(str(d["name"]), str(d["crs"]), float(d["x0"]), float(d["y0"]),
                    float(d["dx"]), float(d["dy"]), int(d["ny"]), int(d["nx"]))


@dataclass
class Raster:
    """A 2-D array on a specific grid. ``attrs`` may carry ``fill_value``."""

    data: np.ndarray
    grid: Grid
    attrs: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.data = np.asarray(self.data)
        if self.data.shape != self.grid.shape:
            raise ValueError(f"raster shape {self.data.shape} != grid shape {self.grid.shape}")


@dataclass(frozen=True)
class TileSpec:
    """Tile identity + WGS84 footprint. The tiling *scheme* is decided elsewhere
    (config/tiling.yaml is still an open decision); this package only needs the box."""

    tile_id: str
    bbox: BBox

    def __post_init__(self) -> None:
        w, s, e, n = self.bbox
        if not (w < e and s < n and -180 <= w and e <= 180 and -90 <= s and n <= 90):
            raise ValueError(f"invalid tile bbox {self.bbox}")


@dataclass(frozen=True)
class GranuleRecord:
    """Catalog metadata for one granule (what retrieval's poller/ASF query knows)."""

    granule_id: str
    product: str                      # "GUNW" | "GCOV" | ...
    track: int
    frame: int
    direction: str                    # "ascending" | "descending"
    ref_time: datetime                # GUNW: reference acquisition; GCOV: acquisition
    sec_time: Optional[datetime] = None   # GUNW: secondary acquisition
    sensor: str = "NISAR"
    bbox: Optional[BBox] = None
    processing_version: str = ""
    perp_baseline_m: Optional[float] = None
    attrs: Mapping[str, Any] = field(default_factory=dict)

    @property
    def is_pair(self) -> bool:
        return self.sec_time is not None

    @property
    def epoch_date(self) -> date:
        """The date this granule 'belongs' to: secondary date for pairs, else acquisition."""
        return (self.sec_time or self.ref_time).date()

    @property
    def temporal_baseline_days(self) -> Optional[float]:
        if self.sec_time is None:
            return None
        return (self.sec_time - self.ref_time).total_seconds() / 86400.0

    @property
    def group_key(self) -> Tuple[str, int, int, str]:
        return (self.sensor, self.track, self.frame, self.direction.lower())


@dataclass
class Clip:
    """What a ClipSource returns for one granule: canonical-named layers, each on
    its own native grid, plus product-level attributes (wavelength_m,
    corrections_applied, degraded_mode, fill_values, heading_deg, ...)."""

    granule_id: str
    layers: Dict[str, Raster]
    attrs: Dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class ClipSource(Protocol):
    """Seam to retrieval. Implement this in an adapter around nisar_client."""

    def read_clip(self, granule: GranuleRecord, bbox: BBox, layers: Sequence[str]) -> Clip:
        ...


class InMemoryClipSource:
    """Dict-backed ClipSource for tests, fixtures and notebooks."""

    def __init__(self, clips: Mapping[str, Clip]):
        self._clips = dict(clips)

    def read_clip(self, granule: GranuleRecord, bbox: BBox, layers: Sequence[str]) -> Clip:
        try:
            clip = self._clips[granule.granule_id]
        except KeyError:
            raise ClipError(f"no clip for granule {granule.granule_id}") from None
        return Clip(clip.granule_id, {k: v for k, v in clip.layers.items() if k in set(layers)},
                    dict(clip.attrs))
