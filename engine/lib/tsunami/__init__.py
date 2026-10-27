"""Tsunami shared library — source, water-column transfer, NSWE solver, travel times, gauges.

Everything here is engine-level science shared by the tsunami pipelines (3.14 tsunami_inundation's
SAR coastal strip reuses lib.okada + lib.tsunami.source; 3.17 tsunami_ocean_evidence uses the
whole package). Ported from the "Dancing with SARs" app's src/lib/tsunami/* (NumPy idiom, same
stages, same thresholds, behavior-level tests — docs/IMPLEMENTATION_PLAN.md §14).
"""
from __future__ import annotations

from .source import (DEFAULT_SOURCE_OPTIONS, SourceOptions, Subfault, build_source_field,
                     compute_deformation, parse_ffm_geojson, uniform_fault_from_scaling)
from .kajiura import kajiura_filter
from .eikonal import travel_time_at, travel_time_field
from .swe import NsweSolver, SimParams, build_geometry
from .gauges import (DEFAULT_DETECT_OPTIONS, DetectOptions, classify_evidence,
                     detect_tsunami_signal, local_quadratic_detide, screen_seismic,
                     station_consistency, time_series_from_lists)

__all__ = [
    # source
    "Subfault", "SourceOptions", "DEFAULT_SOURCE_OPTIONS", "parse_ffm_geojson",
    "uniform_fault_from_scaling", "compute_deformation", "build_source_field",
    # kajiura
    "kajiura_filter",
    # solver
    "NsweSolver", "SimParams", "build_geometry",
    # eikonal
    "travel_time_field", "travel_time_at",
    # gauges / detection / evidence
    "DetectOptions", "DEFAULT_DETECT_OPTIONS", "local_quadratic_detide", "detect_tsunami_signal",
    "screen_seismic", "station_consistency", "classify_evidence", "time_series_from_lists",
]
