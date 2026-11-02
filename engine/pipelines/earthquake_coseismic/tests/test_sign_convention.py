"""A10: the sign convention end to end, pinned by physics (not by a calibrated fixture).

Engine convention: radar.phase_sign = -1 (ATBD: range increase = positive phase, so toward = -lambda*phi/4pi), LOS unit
vector ground->sensor, '+ = toward the sensor'. Under it a thrust (reverse) fault UPLIFTS = moves toward the sensor, so it must
correlate POSITIVELY with the fixture's toward-sensor bowl, and the same plane as a normal fault (subsidence) NEGATIVELY.
(The old fixture paired a NORMAL fault with an uplift bowl: it had been calibrated under a flipped sign and failed 4 tests
by default; see fixtures_eq.make_event.)"""
from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("pyproj")

from engine.lib.okada import GeoFault, geo_fault_displacement_enu  # noqa: E402
from engine.pipelines.earthquake_coseismic import model, prep  # noqa: E402
from engine.pipelines.earthquake_coseismic.types import EqParams  # noqa: E402
from tests.common_fixtures import default_tile, make_scene  # noqa: E402

from . import test_pipeline as tp  # noqa: E402
from .fixtures_eq import make_event  # noqa: E402


def _uz(rake):
    x = np.linspace(-40e3, 40e3, 81)
    E, N = np.meshgrid(x, x)
    f = GeoFault(strike_deg=0.0, dip_deg=45.0, rake_deg=rake, length_m=30e3, width_m=15e3, top_depth_m=2e3,
                 slip_m=1.0, centroid_lat=0.0, centroid_lon=0.0)
    return np.asarray(geo_fault_displacement_enu(f, 8e3, E, N)[2])


def test_okada_thrust_uplifts_and_normal_subsides():
    up, down = _uz(90.0), _uz(-90.0)
    assert np.nanmax(up) > 10 * abs(np.nanmin(up)) > 0 and np.nanmin(down) < -10 * np.nanmax(down)


def test_default_radar_convention_is_the_atbd_one():
    from engine.common.config import load_common_config
    r = load_common_config().radar
    assert (r.phase_sign, r.los_vector_points) == (-1, "ground_to_sensor")


def _screen(rake):
    tile = default_tile()
    lat, lon = tp._event_lonlat(tile)
    bundle = tp._build_bundle(Path(tempfile.mkdtemp()), tile, make_scene(tile, n_epochs=4, pairs=((0, 1), (1, 2), (2, 3)), dlos=0.05),
                              tp.PAIR_WINDOW)
    key = bundle.stack_keys_by_quality()[0]
    stack = bundle.stacks[key]
    idx = next(i for i, p in enumerate(stack.interferometric.pairs) if (p.sec_time or "").startswith("2026-08-06"))
    analysis, _ = prep.analyse_pair(bundle, stack, idx, EqParams.defaults())
    ev = make_event(lat=lat, lon=lon, time="2026-08-01T00:00:00Z", nodal_planes=((60.0, 40.0, rake),))
    return model.forward_screen(analysis, ev, tile.epsg, EqParams.defaults())


def test_thrust_matches_an_uplift_bowl_and_the_same_plane_as_normal_anticorrelates():
    thrust, normal = _screen(90.0), _screen(-90.0)
    assert thrust["pattern_correlation"] > 0.25 and 1 / 3 < thrust["amplitude_ratio"] < 3
    assert normal["pattern_correlation"] < -0.25                         # the sign check can tell uplift from subsidence
    assert thrust["pattern_correlation"] == pytest.approx(-normal["pattern_correlation"], abs=1e-6)   # u(thrust) = -u(normal)
