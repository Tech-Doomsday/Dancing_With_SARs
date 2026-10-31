"""3.01 earthquake_coseismic — detection glue on top of engine.lib.detect.

The kernels (threshold rule, clusters, anomaly moments, null screen) live in engine/lib/detect.py
so they are reusable without importing a pipeline; this module is the pipeline's thin adapter to
its own params/types. Behavior is unchanged and remains pinned by tests/test_detection.py.
"""
from __future__ import annotations

import numpy as np

from engine.lib import detect as _k

from .types import DetectionSummary

NULL_SCREEN_NOTE = _k.NULL_SCREEN_NOTE
NO_VALID_PIXELS_NOTE = _k.NO_VALID_PIXELS_NOTE
NULL_SCREEN_SCENES = _k.NULL_SCREEN_SCENES
NULL_SCREEN_CORR_LEN_PX = _k.NULL_SCREEN_CORR_LEN_PX
NULL_SCREEN_SEED = _k.NULL_SCREEN_SEED

detected_cluster_stats = _k.detected_cluster_stats
run_null_scene_screen = _k.run_null_scene_screen
threshold_rule = _k.threshold_rule
mulberry32 = _k.mulberry32


def detection_pass(disp, sigma_eff, mask, coherence, iono_floor_m, pixel_size_m,
                   k, floor_m, cluster_min_pixels: int = 4) -> tuple[DetectionSummary, dict]:
    """Run the detection rule + summary stats + clusters (see engine.lib.detect.detection_summary)."""
    summary, grids = _k.detection_summary(disp, sigma_eff, mask, coherence, iono_floor_m,
                                          pixel_size_m, k, floor_m, cluster_min_pixels)
    return DetectionSummary(**summary), grids
