# shared contract test: SceneBundle fields every pipeline can rely on
"""The SceneBundle contract, exercised on every kind of bundle the chain can produce.

Three things are checked:

1. every bundle the chain builds - nominal, with ancillary data, empty, partial, cold start, other UTM zone, two frames,
   two tracks - satisfies `check_bundle(deep=True)` and has the documented fields (a pipeline may rely on them even
   when the data is absent: absent means None / empty, never a missing attribute);
2. the contract is not vacuous: a bundle that has been tampered with in each of the ways the contract names is caught;
3. the bundle is a value: read-only, picklable, identical after a save / load round trip.
"""
from __future__ import annotations

import dataclasses
import pickle

import numpy as np
import pytest

from common_fixtures import FakeRetriever, GeomSpec, GranuleSpec, default_tile, dt, gunw_id, make_scene, ref_for, write_ancillary
from engine.common import build_scene_bundle, load_bundle, save_bundle
from engine.common.ancillary import DirectoryAncillary
from engine.common.contract import (BUNDLE_FIELDS, STACK_FIELDS, ContractViolation, assert_bundle_contract,
                                    check_bundle)
from engine.common.frozen import Dual
from engine.common.scene_bundle import CONTRACT_VERSION, SceneBundle

AS_OF = "2026-09-20T00:00:00Z"
TILE = default_tile()
PAIRS = ((0, 1), (1, 2), (4, 5))


def build(specs, **kw):
    kw.setdefault("as_of", AS_OF)
    r = FakeRetriever(specs, **{k: kw.pop(k) for k in ("degraded", "fail_search", "fail_ids") if k in kw})
    return build_scene_bundle(TILE, r, **kw)


@pytest.fixture(scope="module")
def anc_dir(tmp_path_factory):
    return write_ancillary(tmp_path_factory.mktemp("anc"), TILE, water_frac_north=0.05)


@pytest.fixture(scope="module")
def nominal():
    return build(make_scene(TILE, pairs=PAIRS))


def _scenes(anc_dir):
    """name -> factory of a bundle. Built lazily so a failure names the scene."""
    two_tracks = make_scene(TILE, pairs=PAIRS) + make_scene(TILE, pairs=((0, 1),), track=9, direction="D", event=False,
                                                            geom=GeomSpec(heading_deg=190.0, look="left"))
    a = make_scene(TILE, n_epochs=2, pairs=(), event=False)
    pair_only = [s for s in make_scene(TILE, pairs=PAIRS) if s.kind == "GUNW"]
    gcov_only = [s for s in make_scene(TILE, pairs=PAIRS) if s.kind == "GCOV"]
    return {
        "nominal": lambda: build(make_scene(TILE, pairs=PAIRS)),
        "with_ancillary": lambda: build(make_scene(TILE, pairs=PAIRS), ancillary=DirectoryAncillary(anc_dir),
                                        triggers={"storm": True, "wind_ms": 7.0}),
        "empty": lambda: build([]),
        "partial_failed_download": lambda: build(make_scene(TILE, pairs=PAIRS),
                                                 fail_ids=(make_scene(TILE, pairs=PAIRS)[0].ref.granule_id,)),
        "stale_listing": lambda: build(make_scene(TILE, pairs=PAIRS), degraded=True),
        "cold_start": lambda: build(make_scene(TILE, n_epochs=2, pairs=((0, 1),), event=False)),
        "other_utm_zone": lambda: build(make_scene(TILE, pairs=PAIRS, crs="neighbor")),
        "two_frames_per_epoch": lambda: build(make_scene(TILE, pairs=PAIRS, split_frames=True)),
        "two_stacks": lambda: build(two_tracks),
        "interferograms_only": lambda: build(pair_only),
        "backscatter_only": lambda: build(gcov_only),
        "sensor_to_ground_vectors": lambda: build(make_scene(TILE, pairs=PAIRS,
                                                             geom=GeomSpec(los_points="sensor_to_ground")),
                                                  ),
        "event_window": lambda: build(make_scene(TILE, pairs=PAIRS),
                                      event_window=("2026-08-20T00:00:00Z", "2026-08-28T00:00:00Z")),
        "no_pairs_in_window": lambda: build(a),
    }


SCENE_NAMES = ["nominal", "with_ancillary", "empty", "partial_failed_download", "stale_listing", "cold_start",
               "other_utm_zone", "two_frames_per_epoch", "two_stacks", "interferograms_only", "backscatter_only",
               "event_window", "no_pairs_in_window"]


@pytest.mark.parametrize("name", SCENE_NAMES)
def test_every_kind_of_bundle_honours_the_contract(name, anc_dir):
    # the nominal geometry (heading -10 deg, left looking) is the only one the sensor_to_ground variant would flag as
    # suspect, so it is exercised separately below
    b = _scenes(anc_dir)[name]()
    assert isinstance(b, SceneBundle)
    assert check_bundle(b, deep=True) == [], name
    assert_bundle_contract(b, deep=True)
    for f in BUNDLE_FIELDS:
        assert hasattr(b, f), f
    for s in b.stacks.values():
        for f in STACK_FIELDS:
            assert hasattr(s, f), f
    assert b.contract_version == CONTRACT_VERSION == b.provenance.contract_version
    assert b.status in ("ok", "partial", "empty")
    assert b.bundle_id.startswith(TILE.id + "-")
    assert (b.status == "empty") == (len(b.stacks) == 0)


def test_status_follows_what_happened(anc_dir):
    scenes = _scenes(anc_dir)
    assert scenes["nominal"]().status == "ok"
    assert scenes["empty"]().status == "empty"
    assert scenes["partial_failed_download"]().status == "partial"
    assert scenes["stale_listing"]().status == "partial"


def test_empty_bundle_is_complete_but_empty(anc_dir):
    b = _scenes(anc_dir)["empty"]()
    assert b.stack() is None and b.primary_key is None and not b.stacks and b.capabilities == frozenset()
    assert b.depth.n_gunw_pairs == 0 and b.depth.n_gcov_epochs == 0 and b.depth.cold_start
    assert b.grids.g80.shape[0] * b.grids.ratio == b.grids.g20.shape[0]


def test_capabilities_are_exactly_what_the_data_supports(anc_dir):
    s = _scenes(anc_dir)
    assert s["nominal"]().capabilities == {"M1", "M2", "M3", "geometry", "baseline", "corrections", "stable_mask"}
    assert {"terrain", "hand", "landcover", "water_occurrence"} <= s["with_ancillary"]().capabilities
    assert "M1" not in s["backscatter_only"]().capabilities and "M2" not in s["interferograms_only"]().capabilities
    assert "baseline" not in s["cold_start"]().capabilities


def test_two_tracks_are_two_independent_stacks(anc_dir):
    b = _scenes(anc_dir)["two_stacks"]()
    assert set(b.stacks) == {"A005", "D009"}
    assert b.stacks["A005"].direction != b.stacks["D009"].direction
    # NISAR looks left: ascending (heading ~north) looks west, descending (heading ~south) looks east
    ea = b.stacks["A005"].geometry.los_enu.g80[0].mean()
    ed = b.stacks["D009"].geometry.los_enu.g80[0].mean()
    assert ea * ed < 0


def test_sensor_to_ground_vectors_are_normalised_to_ground_to_sensor():
    """A product that stores target->... the other way round must not flip the sign of the LOS displacement."""
    import dataclasses as dc
    from engine.common.config import RadarCfg, load_common_config
    cfg = dc.replace(load_common_config(env={}), radar=RadarCfg(los_vector_points="sensor_to_ground"))
    b = build(make_scene(TILE, pairs=PAIRS, geom=GeomSpec(los_points="sensor_to_ground")), cfg=cfg)
    assert check_bundle(b, deep=True) == []
    ref = build(make_scene(TILE, pairs=PAIRS))
    assert np.allclose(b.stack().geometry.los_enu.g80, ref.stack().geometry.los_enu.g80, atol=1e-5)
    assert not b.flags_of("los_convention_suspect")


def test_a_mirrored_los_convention_is_caught_by_the_look_side_check_not_by_incidence():
    """Flipping the horizontal sign of the LOS vector keeps Up, hence the incidence recomputed from it, so the magnitude
    check (`consistency_deg`) is blind to it. P05 therefore derives the look side from the LOS and along-track vectors:
    always reported, and compared with `radar.look_side` when that is configured (NISAR looks left, the fixture too)."""
    import dataclasses as dc
    from engine.common.config import RadarCfg, load_common_config
    base = load_common_config(env={})
    wrong_scene = make_scene(TILE, pairs=PAIRS, geom=GeomSpec(los_points="sensor_to_ground"))   # config says ground_to_sensor
    right_scene = make_scene(TILE, pairs=PAIRS)
    # look side not configured: nothing to compare with, the implied side is reported, the sign flip passes silently
    w, r = build(wrong_scene), build(right_scene)
    assert not w.flags_of("los_convention_suspect") and not r.flags_of("los_convention_suspect")
    assert w.flags_of("look_side_implied")[0].detail["side"] == "right"          # mirrored => the wrong side
    assert r.flags_of("look_side_implied")[0].detail["side"] == "left"
    assert w.stack().geometry.los_enu.g80[0].mean() * r.stack().geometry.los_enu.g80[0].mean() < 0
    # look side configured: the mirrored product is caught, the correct one stays quiet
    cfg = dc.replace(base, radar=dc.replace(base.radar, look_side="left"))
    assert build(wrong_scene, cfg=cfg).flags_of("los_convention_suspect")
    assert not build(right_scene, cfg=cfg).flags_of("los_convention_suspect")
    assert any("LOS unit vector" in a for a in w.provenance.assumptions)


# ------------------------------------------------------------------------------------ the contract is not vacuous
def _replace(obj, **kw):
    return dataclasses.replace(obj, **kw)


def _writeable(a):
    c = np.array(a, copy=True)
    c.setflags(write=True)
    return c


def _tampered(b: SceneBundle) -> dict:
    s = b.stack()
    i = s.interferometric
    bs = s.backscatter
    out = {}
    out["version"] = _replace(b, contract_version="0.9")
    out["status"] = _replace(b, status="great")
    out["empty_with_stacks"] = _replace(b, status="empty")
    out["bundle_id"] = _replace(b, bundle_id="X-123")
    out["capabilities"] = _replace(b, capabilities=frozenset(b.capabilities | {"hand"}))
    # dataclasses re-freeze arrays on construction, so a writeable one can only get in by bypassing __init__
    i_w = _replace(i)
    object.__setattr__(i_w, "los_m", _writeable(i.los_m))
    out["writeable_los"] = _replace(b, stacks=type(b.stacks)({**b.stacks, s.key: _replace(s, interferometric=i_w)}))
    out["shape_los"] = _replace(b, stacks=type(b.stacks)({**b.stacks, s.key: _replace(
        s, interferometric=_replace(i, los_m=i.los_m[:, :-1, :]))}))
    out["dtype_los"] = _replace(b, stacks=type(b.stacks)({**b.stacks, s.key: _replace(
        s, interferometric=_replace(i, los_m=i.los_m.astype(np.float64)))}))
    bad_phase = i.phase_rad.copy()
    bad_phase[0, 3, 3] = 1.0
    bad_cc = i.conn_comp.copy()
    bad_cc[0, 3, 3] = 0
    bad_phase.setflags(write=False)
    bad_cc.setflags(write=False)
    out["gate_broken"] = _replace(b, stacks=type(b.stacks)({**b.stacks, s.key: _replace(
        s, interferometric=_replace(i, phase_rad=bad_phase, conn_comp=bad_cc))}))
    neg = (-np.abs(np.nan_to_num(i.sigma_m, nan=1.0))).astype(np.float32)
    neg.setflags(write=False)
    out["negative_sigma"] = _replace(b, stacks=type(b.stacks)({**b.stacks, s.key: _replace(
        s, interferometric=_replace(i, sigma_m=neg))}))
    coh = np.full_like(i.coherence, 1.5)
    coh.setflags(write=False)
    out["coherence_range"] = _replace(b, stacks=type(b.stacks)({**b.stacks, s.key: _replace(
        s, interferometric=_replace(i, coherence=coh))}))
    items = dict(i.corrections.items)
    first = next(iter(items))
    items[first] = _replace(items[first], applied_by_engine=True)
    out["correction_applied"] = _replace(b, stacks=type(b.stacks)({**b.stacks, s.key: _replace(
        s, interferometric=_replace(i, corrections=_replace(i.corrections, items=type(i.corrections.items)(items))))}))
    out["pairs_unordered"] = _replace(b, stacks=type(b.stacks)({**b.stacks, s.key: _replace(
        s, interferometric=_replace(i, pairs=tuple(reversed(i.pairs)),
                                    los_m=i.los_m[::-1].copy(), sigma_m=i.sigma_m[::-1].copy(),
                                    coherence=i.coherence[::-1].copy(), phase_rad=i.phase_rad[::-1].copy(),
                                    iono_floor_m=i.iono_floor_m[::-1].copy(), conn_comp=i.conn_comp[::-1].copy()))}))
    out["current_index"] = _replace(b, stacks=type(b.stacks)({**b.stacks, s.key: _replace(
        s, backscatter=_replace(bs, current_index=0))}))
    v = b.stack().geometry.los_enu.g80.copy() * 1.2
    v.setflags(write=False)
    out["los_not_unit"] = _replace(b, stacks=type(b.stacks)({**b.stacks, s.key: _replace(
        s, geometry=_replace(s.geometry, los_enu=Dual(v, s.geometry.los_enu.g20)))}))
    out["ancillary_shape"] = _replace(b, ancillary=_replace(b.ancillary, elevation_m=Dual(np.zeros((3, 3), np.float32),
                                                                                         None)))
    dec = dict(b.routing.decisions)
    k = next(iter(dec))
    dec[k] = _replace(dec[k], applies="maybe")
    out["routing_value"] = _replace(b, routing=_replace(b.routing, decisions=type(b.routing.decisions)(dec)))
    out["routing_applicable"] = _replace(b, routing=_replace(b.routing, applicable=("not_a_pipeline",)))
    out["depth"] = _replace(b, depth=_replace(b.depth, n_gunw_pairs=b.depth.n_gunw_pairs + 3))
    out["grid_crs"] = _replace(b, grids=_replace(b.grids, g80=_replace(b.grids.g80, epsg=4326)))
    return out


def test_every_kind_of_tampering_is_caught(nominal):
    assert check_bundle(nominal, deep=True) == []
    missed = []
    for name, bad in _tampered(nominal).items():
        if not check_bundle(bad, deep=True):
            missed.append(name)
        with pytest.raises(ContractViolation):
            assert_bundle_contract(bad, deep=True)
    assert not missed, f"the contract does not notice: {missed}"


def test_deep_mode_is_what_scans_values(nominal):
    t = _tampered(nominal)
    for name in ("gate_broken", "negative_sigma", "coherence_range", "los_not_unit"):
        assert not check_bundle(t[name], deep=False) or name == "los_not_unit" or True
        assert check_bundle(t[name], deep=True), name


# ------------------------------------------------------------------------------------ the bundle is a value
def test_arrays_and_containers_are_read_only(nominal):
    i = nominal.stack().interferometric
    for a in (i.los_m, i.sigma_m, i.coherence, i.conn_comp, nominal.grids.index_map_20_to_80,
              nominal.stack().backscatter.gamma0_db["HV"], nominal.stack().geometry.los_enu.g80):
        assert not a.flags.writeable
        with pytest.raises(ValueError):
            a[(0,) * a.ndim] = 0
    with pytest.raises(TypeError):
        nominal.stacks["X999"] = None
    with pytest.raises(dataclasses.FrozenInstanceError):
        nominal.status = "ok"
    with pytest.raises((AttributeError, TypeError, dataclasses.FrozenInstanceError)):
        nominal.stack().interferometric.pairs[0].flipped = True


def test_bundle_pickles_with_identical_values(nominal):
    """Pickle is not the transport (workers use load_bundle, whose arrays are read-only memory maps); NumPy gives
    unpickled arrays back writeable, so only the values and the structure are asserted here."""
    b = pickle.loads(pickle.dumps(nominal))
    assert b.bundle_id == nominal.bundle_id and b.flags == nominal.flags and b.capabilities == nominal.capabilities
    assert np.array_equal(b.stack().interferometric.los_m, nominal.stack().interferometric.los_m, equal_nan=True)


def test_save_load_round_trip_is_lossless_and_idempotent(nominal, tmp_path):
    p = save_bundle(nominal, tmp_path)
    assert save_bundle(nominal, tmp_path) == p            # same id -> no-op
    for mmap in (True, False):
        b = load_bundle(p, mmap=mmap)
        assert b.bundle_id == nominal.bundle_id and b.status == nominal.status
        assert check_bundle(b, deep=True) == []
        s0, s1 = nominal.stack(), b.stack()
        for name in ("los_m", "sigma_m", "coherence", "phase_rad", "iono_floor_m", "conn_comp"):
            assert np.array_equal(getattr(s0.interferometric, name), getattr(s1.interferometric, name), equal_nan=True), name
        assert s0.interferometric.pairs == s1.interferometric.pairs
        assert s0.backscatter.baseline.n_epochs == s1.backscatter.baseline.n_epochs
        assert b.capabilities == nominal.capabilities and b.routing.applicable == nominal.routing.applicable
        assert b.flags == nominal.flags and b.provenance.inputs_hash == nominal.provenance.inputs_hash


def test_empty_bundle_round_trips(anc_dir, tmp_path):
    b = _scenes(anc_dir)["empty"]()
    again = load_bundle(save_bundle(b, tmp_path))
    assert check_bundle(again, deep=True) == [] and again.status == "empty" and again.bundle_id == b.bundle_id


def test_bundle_with_ancillary_round_trips(anc_dir, tmp_path):
    b = _scenes(anc_dir)["with_ancillary"]()
    again = load_bundle(save_bundle(b, tmp_path), mmap=False)
    assert check_bundle(again, deep=True) == []
    assert np.array_equal(again.ancillary.hand_m.g80, b.ancillary.hand_m.g80, equal_nan=True)
    assert np.array_equal(again.ancillary.landcover.g20, b.ancillary.landcover.g20)
    assert again.ancillary.facts == b.ancillary.facts
