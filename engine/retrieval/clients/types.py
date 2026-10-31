from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True)
class GranuleRef:
    granule_id: str            # fileID without extension; includes the processing version tail
    url: str
    product: str               # processing level: GUNW, GCOV, ...
    start: str | None = None
    stop: str | None = None
    track: int | None = None
    frame: int | None = None
    direction: str | None = None
    size_bytes: int | None = None
    footprint: list | None = None   # [[lon, lat], ...] outer ring from ASF, if known
    # Provider-side metadata for the granule Explorer (E8): facts the search provider (ASF/CMR)
    # states about the granule, passed through verbatim. Optional and never guessed; the
    # Explorer treats an absent key as "the provider did not say".
    extra: dict | None = None

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class GridClip:
    grid: str                      # flat grid key, e.g. science__LSAR__GUNW__grids__frequencyA
    epsg: int
    x: np.ndarray
    y: np.ndarray
    extras: dict[str, np.ndarray]
    layers: dict[str, np.ndarray]  # flat layer key -> array (raw HDF5 values, native dtype)


@dataclass
class GranuleClip:
    granule: GranuleRef
    bbox: tuple
    grids: dict[str, GridClip]
    source: str                    # "memory" | "clip-cache" | "network" | "stale-clip-cache"
    stats: dict = field(default_factory=dict)


@dataclass
class FetchResult:
    clips: list[GranuleClip]
    errors: dict[str, str]         # granule_id -> reason (never silent)
    skipped: dict[str, str]        # granule_id -> why nothing to clip (no overlap, ...)
    degraded: bool = False         # True if anything was served stale / search fell back to cache
    stats: dict = field(default_factory=dict)
