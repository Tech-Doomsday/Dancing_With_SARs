"""engine.common - common preprocessing (P01..P13) producing the SceneBundle.

Public surface (everything else is internal):
    build_scene_bundle / run_preprocessing, PreprocInput, PreprocConfig,
    SceneBundle, ClipSource, Clip, Raster, Grid, TileSpec, GranuleRecord.
"""
from ._types import (Clip, ClipError, ClipSource, GranuleRecord, GranuleResolutionError, Grid,
                     InMemoryClipSource, InsufficientInputError, PreprocError, Raster, TileSpec)
from .config import PreprocConfig
from .p13_provenance import __version__
from .runner import PreprocResult, build_scene_bundle, run_preprocessing
from .scene_bundle import CORE_LAYERS, SCHEMA_VERSION, SceneBundle, TemporalStack
from .state import PreprocInput

__all__ = [
    "build_scene_bundle", "run_preprocessing", "PreprocInput", "PreprocConfig", "PreprocResult",
    "SceneBundle", "TemporalStack", "CORE_LAYERS", "SCHEMA_VERSION", "ClipSource", "InMemoryClipSource",
    "Clip", "Raster", "Grid", "TileSpec", "GranuleRecord", "PreprocError", "GranuleResolutionError",
    "ClipError", "InsufficientInputError", "__version__",
]
