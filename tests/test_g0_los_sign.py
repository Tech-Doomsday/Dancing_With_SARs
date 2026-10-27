"""Gate G0 — the LOS sign convention, pinned before any verdict is trusted (port decision 5).

Three layers:
  G0-a  REAL-PRODUCT PIN: a NISAR L2 GUNW stores losUnitVectorX/Y "from target to sensor"
        (JPL D-102272; matched numerically on one real product — config radar.los_vector_points).
        Building the vector from a documented descending left-looking geometry, a +UP ground
        displacement must project POSITIVE (toward the sensor) — the engine's los_m convention.
  G0-b  THE APP-CONVENTION FLIP (finding F1): the old app worked away-positive and negated the
        dot product. The engine prediction MUST equal minus the app's projection for the same
        vectors — pinning that the historical sign flip can never silently return.
  G0-c  METAMORPHIC: flip the bundle's los_enu and the pipeline's verdict for the SAME scene MUST
        CHANGE (SUPPORTED -> NOT_SUPPORTED). If it does not, the sign leaked somewhere.
"""
from __future__ import annotations

import dataclasses
import math
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.common import DirectoryAncillary  # noqa: E402
from engine.common.chain import build_scene_bundle  # noqa: E402
from engine.common.frozen import Dual, FrozenDict  # noqa: E402
from engine.lib.los import los_flip, los_project, los_unit_ok  # noqa: E402
from tests.common_fixtures import (  # noqa: E402
    FakeRetriever, default_tile, make_scene, write_ancillary)


# --------------------------------------------------------------------------- #
# G0-a: real-product convention
# --------------------------------------------------------------------------- #
def _real_product_los_enu(heading_deg: float = -168.0, incidence_deg: float = 35.0, n: int = 8):
    """los_enu exactly as a NISAR L2 GUNW stores it: unit vector FROM THE TARGET (ground) TO the
    SENSOR, in (E, N, Up). NISAR is LEFT-looking; the look vector is heading + 90° for a
    left-looking radar (the direction the antenna points at the ground)."""
    h = math.radians(heading_deg)
    look_az = h + math.radians(90.0)                      # left-looking: look = heading + 90
    i = math.radians(incidence_deg)
    # horizontal look direction unit + the incidence tilt: the vector from the ground cell TOWARD
    # the sensor points opposite the look azimuth, raised by (90° - incidence) above the horizon
    east = -math.cos(look_az) * math.sin(i)
    north = math.sin(look_az) * math.sin(i)
    up = math.cos(i)
    return np.stack([np.full((n, n), east), np.full((n, n), north), np.full((n, n), up)])


def test_g0a_real_product_vector_is_ground_to_sensor_and_toward_positive():
    los = _real_product_los_enu()
    assert los_unit_ok(los)
    # +UP ground motion (uplift) must read POSITIVE (toward the sensor) in engine convention
    toward = los_project(np.zeros_like(los[0]), np.zeros_like(los[0]), np.ones_like(los[0]), los)
    assert np.allclose(toward, math.cos(math.radians(35.0)))
    # range increase (ground moving AWAY) would be negative
    away = los_project(np.zeros_like(los[0]), np.zeros_like(los[0]), -np.ones_like(los[0]), los)
    assert np.allclose(away, -math.cos(math.radians(35.0)))


def test_g0b_engine_prediction_is_minus_the_app_convention():
    """Finding F1: the app's projectDisplacementToLOS returned −(d·u_gs) (away-positive).
    The engine's prediction is +(d·u_gs). Same vectors, opposite signs, by design."""
    rng = np.random.default_rng(7)
    los = _real_product_los_enu()
    d_e, d_n, d_u = rng.normal(size=los.shape[1:]), rng.normal(size=los.shape[1:]), rng.normal(size=los.shape[1:])
    engine = los_project(d_e, d_n, d_u, los)
    app_style = -(d_e * los[0] + d_n * los[1] + d_u * los[2])      # the app's away-positive value
    assert np.allclose(engine, -app_style)


def test_g0a2_metamorphic_flip_operator_is_exact_negation():
    los = _real_product_los_enu()
    d = (np.ones_like(los[0]), np.zeros_like(los[0]), np.zeros_like(los[0]))
    assert np.allclose(los_project(*d, los_flip(los)), -los_project(*d, los))


# --------------------------------------------------------------------------- #
# G0-c: metamorphic — flip the bundle's LOS sign, the verdict must change
# --------------------------------------------------------------------------- #
def _flip_bundle_los(bundle):
    """A copy of the bundle whose geometry los_enu points sensor->ground (the flipped world)."""
    stacks = {}
    for key, stack in bundle.stacks.items():
        geom = stack.geometry
        flipped80 = -np.asarray(geom.los_enu.g80, dtype=np.float64)
        flipped20 = (-np.asarray(geom.los_enu.g20, dtype=np.float64)
                     if geom.los_enu.g20 is not None else None)
        new_geom = dataclasses.replace(geom, los_enu=Dual(g80=flipped80, g20=flipped20))
        stacks[key] = dataclasses.replace(stack, geometry=new_geom)
    return dataclasses.replace(bundle, stacks=FrozenDict(stacks))


@pytest.fixture(scope="module")
def _scene(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("g0")
    tile = default_tile()
    specs = make_scene(tile, n_epochs=4, pairs=((0, 1), (1, 2), (2, 3)), dlos=0.05)
    anc_root = write_ancillary(tmp / "ancillary", tile)
    bundle = build_scene_bundle(
        tile, FakeRetriever(specs), as_of="2026-08-10T00:00:00Z",
        ancillary=DirectoryAncillary(anc_root),
        event_window=("2026-07-25T00:00:00Z", "2026-08-06T00:00:00Z"),
    )
    from engine.pipelines.earthquake_coseismic import fetch as eq_fetch
    from pyproj import Transformer
    tr = Transformer.from_crs(tile.epsg, 4326, always_xy=True)
    lon, lat = tr.transform(tile.e0 + 1000.0, tile.n0 + 2500.0)
    event = {"id": "eq-fixture-001", "magnitude": 5.6, "place": "fixture", "time": "2026-08-01T00:00:00Z",
             "lat": lat, "lon": lon, "depth_km": 5.0, "url": None, "nodal_planes": ((90.0, 40.0, -90.0),)}
    eq_fetch.set_event_provider(lambda bbox, t0, t1: {"source": "fixture", "events": [event],
                                                      "degraded": False, "error": None})
    yield bundle
    eq_fetch.reset_event_provider()


def test_g0c_flip_the_los_sign_and_the_verdict_must_change(_scene):
    from engine.pipelines.earthquake_coseismic.verdict import run as eq_run

    normal = eq_run(_scene)
    flipped = eq_run(_flip_bundle_los(_scene))
    # the same measured field, the same event: only the LOS convention changed
    assert normal.status == "SUPPORTED", normal.detail.get("decision")
    assert flipped.status in ("NOT_SUPPORTED", "UNCATALOGUED_CANDIDATE"), flipped.detail.get("decision")
    # and specifically: the screen correlation flipped from positive to (strongly) negative
    scr_n = [s for s in normal.detail["forward_screens"] if not s.get("skipped")]
    scr_f = [s for s in flipped.detail["forward_screens"] if not s.get("skipped")]
    assert scr_n and scr_f
    assert scr_n[0]["pattern_correlation"] > 0.25
    assert scr_f[0]["pattern_correlation"] < -0.25


def test_g0c_flipped_vectors_fail_the_unit_contract_direction_but_not_norm(_scene):
    geom = _scene.stack().geometry
    los = np.asarray(geom.los_enu.g80)
    flipped = los_flip(los)
    # still unit vectors (the norm is unchanged) — but the Up component now points DOWN,
    # which los_unit_ok's ground->sensor contract rejects
    assert np.allclose(np.linalg.norm(flipped, axis=0), 1.0, atol=1e-6)
    assert los_unit_ok(los)
    assert not los_unit_ok(flipped)
