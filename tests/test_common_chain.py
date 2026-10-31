"""P01..P13 end to end on synthetic NISAR-shaped inputs with known ground truth (FakeRetriever, no network)."""
import math
from datetime import timedelta

import numpy as np
import pytest

from common_fixtures import (FakeRetriever, GeomSpec, GranuleSpec, default_tile, dt, event_patch, gunw_id, los_truth,
                             make_scene, ref_for, write_ancillary)
from engine.common import build_scene_bundle
from engine.common.ancillary import DirectoryAncillary
from engine.common.contract import assert_bundle_contract

AS_OF = "2026-09-20T00:00:00Z"
LAMBDA = 0.2384
TILE = default_tile()
PAIRS = ((0, 1), (1, 2), (4, 5))            # the last pair spans the event epoch (index 5)


def build(specs, **kw):
    kw.setdefault("as_of", AS_OF)
    r = FakeRetriever(specs, **{k: kw.pop(k) for k in ("degraded", "fail_search", "fail_ids") if k in kw})
    return build_scene_bundle(TILE, r, **kw), r


def codes(b, severity=None):
    return {f.code for f in b.flags if severity is None or f.severity == severity}


@pytest.fixture(scope="module")
def nominal():
    return build(make_scene(TILE, pairs=PAIRS))[0]


@pytest.fixture(scope="module")
def anc_dir(tmp_path_factory):
    return write_ancillary(tmp_path_factory.mktemp("anc"), TILE, water_frac_north=0.05)


@pytest.fixture(scope="module")
def with_anc(anc_dir):
    return build(make_scene(TILE, pairs=PAIRS), ancillary=DirectoryAncillary(anc_dir),
                 triggers={"storm": False})[0]


# ------------------------------------------------------------------------------------ the nominal scene
def test_nominal_bundle_is_valid_and_complete(nominal):
    b = nominal
    assert b.status == "ok" and list(b.stacks) == ["A005"]
    assert {"M1", "M2", "M3", "geometry", "baseline", "corrections", "stable_mask"} <= b.capabilities
    assert_bundle_contract(b, deep=True)
    assert [s.code for s in b.provenance.steps] == [f"P{i:02d}" for i in range(1, 14)]
    assert all(s.status == "ok" for s in b.provenance.steps)
    assert b.epoch == "2026-08-30T00:00:00Z"
    assert not codes(b, "error")


def test_los_displacement_recovers_the_planted_bowl_with_sign_and_units(nominal):
    s = nominal.stack()
    i = s.interferometric
    g = nominal.grids.g80
    X, Y = np.meshgrid(g.x, g.y)
    truth = los_truth(TILE, X, Y, -0.05)                       # metres, + toward the sensor, later - earlier
    est = i.los_m[-1]
    ok = np.isfinite(est)
    assert ok.mean() > 0.97
    assert np.sqrt(np.mean((est - truth)[ok] ** 2)) < 0.002   # 2 mm
    assert abs(np.nanmin(est) + 0.05) < 0.003                  # the peak keeps its sign (subsidence = negative = away)
    assert np.nanmax(np.abs(i.los_m[0])) < 0.003 and np.nanmax(np.abs(i.los_m[1])) < 0.003
    p = i.pairs[-1]
    assert p.ref_time == "2026-08-18T00:00:00Z" and p.sec_time == "2026-08-30T00:00:00Z" and p.dt_days == 12.0 and not p.flipped


def test_los_m_is_exactly_phase_times_constant_never_corrected(nominal):
    i = nominal.stack().interferometric
    ok = np.isfinite(i.los_m)
    k = -LAMBDA / (4 * math.pi)
    assert np.allclose(i.los_m[ok], k * i.phase_rad[ok], atol=1e-7)
    for item in i.corrections.items.values():
        assert item.applied_by_engine is False
    assert i.corrections.residual_components                    # diagnostics exist, but live in a separate layer
    assert not np.array_equal(i.corrections.residual_los_m, i.los_m, equal_nan=True)


def test_connected_component_gate_removes_garbage_and_sigma_matches_cramer_rao(nominal):
    i = nominal.stack().interferometric
    g = nominal.grids.g80
    X, Y = np.meshgrid(g.x, g.y)
    bad = (X > TILE.e0 + 1000) & (X < TILE.e0 + 2000) & (Y > TILE.n0 + 7000) & (Y < TILE.n0 + 8500)
    assert (i.conn_comp[:, bad] == 0).all() and np.isnan(i.los_m[:, bad]).all() and np.isnan(i.phase_rad[:, bad]).all()
    coh = 0.8
    sp = math.sqrt((1 - coh ** 2) / (2 * 100 * coh ** 2))
    assert abs(np.nanmedian(i.sigma_m) - sp * LAMBDA / (4 * math.pi)) < 1e-4
    assert np.isnan(i.sigma_m[np.isnan(i.los_m)]).all()
    assert np.nanmedian(i.iono_floor_m) == pytest.approx(0.05 * LAMBDA / (4 * math.pi), rel=0.05)


def test_pair_network_and_depth(nominal):
    n = nominal.stack().interferometric.network
    assert (n.n_pairs, n.n_dates, n.n_components, n.largest_component_pairs, n.connected) == (3, 5, 2, 2, False)
    assert nominal.depth.n_gunw_pairs == 3 and nominal.depth.n_gunw_pairs_connected == 2 and nominal.depth.n_gcov_epochs == 6
    assert "network_disconnected" in codes(nominal)
    assert nominal.depth.metric("pairs_connected") == 2


def test_geometry_from_radargrid_matches_the_sensor_setup(nominal):
    g = nominal.stack().geometry
    assert g.source == "radarGrid:GUNW" and g.vectors_available
    assert g.heading_deg == pytest.approx(350.0, abs=0.01)
    inc = np.radians(40.0)
    az = np.radians((-10 + 90) % 360)                           # left-looking: ground->sensor azimuth = heading + 90
    e, n, u = (g.los_enu.g80[k].mean() for k in range(3))
    assert (e, n, u) == pytest.approx((math.sin(inc) * math.sin(az), math.sin(inc) * math.cos(az), math.cos(inc)), abs=2e-3)
    assert g.consistency_deg < 0.1 and g.incidence_deg.g80.mean() == pytest.approx(40.0, abs=0.01)
    assert g.los_enu.g20.shape == (3,) + nominal.grids.g20.shape
    assert g.height_source == "fallback" and not g.layover.available                 # no DEM -> no local geometry, said so
    assert "no_dem" in codes(nominal)


def test_backscatter_db_baseline_and_change_detection(nominal):
    bs = nominal.stack().backscatter
    g20 = nominal.grids.g20
    X, Y = np.meshgrid(g20.x, g20.y)
    ev = event_patch(TILE, X, Y)
    forest = X < TILE.e0 + 5000
    assert set(bs.gamma0_db) == {"HH", "HV"} and len(bs.epochs) == 6 and bs.current_index == 5
    hv0 = bs.gamma0_db["HV"][0]
    assert abs(np.nanmedian(hv0[forest & ~ev]) - (-15.0)) < 0.5      # speckle mean in dB sits slightly below the nominal
    assert abs(np.nanmedian(bs.hv_hh_db[0][forest]) - (-6.0)) < 0.7
    assert np.nanmean(bs.sigma0_db["HH"][0] - bs.gamma0_db["HH"][0]) == pytest.approx(10 * math.log10(0.7), abs=1e-3)
    b = bs.baseline
    assert (b.n_epochs, b.cold_start, b.seasonal_ok, b.epoch_indices) == (5, False, False, (0, 1, 2, 3, 4))
    assert b.history_days == pytest.approx(60.0)          # first prior epoch -> newest epoch
    z = (bs.gamma0_db["HV"][5] - b.median_db["HV"]) / b.sigma_db["HV"]
    assert np.nanmean(z[ev]) < -4.0                                   # the planted -6 dB drop stands out
    assert abs(np.nanmean(z[~ev])) < 0.1 and np.nanstd(z[~ev]) < 1.3  # and nothing else does
    assert np.nanmin(b.sigma_db["HV"]) >= b.speckle_floor_db - 1e-5 and b.speckle_floor_db == pytest.approx(4.3429 / 5.0, rel=1e-3)


def test_stable_mask_excludes_the_changed_patch(nominal):
    st = nominal.stack().stable
    assert st.criteria == ("coherence", "no_change") and set(st.missing) == {"slope", "not_water"}
    g20 = nominal.grids.g20
    X, Y = np.meshgrid(g20.x, g20.y)
    ev = event_patch(TILE, X, Y)
    assert st.mask.g20[ev].mean() < 0.05 and st.mask.g20[~ev].mean() > 0.8
    assert "stable_mask_partial" in codes(nominal)


def test_pairs_spanning_helper(nominal):
    hits = nominal.pairs_spanning("2026-08-20T00:00:00Z", "2026-08-28T00:00:00Z")
    assert [(k, i) for k, i, _ in hits] == [("A005", 2)]
    assert nominal.pairs_spanning("2026-09-01T00:00:00Z", "2026-09-02T00:00:00Z") == []


# ------------------------------------------------------------------------------------ pair conventions / frames / CRS
def test_pair_stored_secondary_first_is_flipped_and_sign_normalised():
    t = [dt("2026-07-01"), dt("2026-07-13")]
    base = make_scene(TILE, n_epochs=2, pairs=(), event=False)
    sp = GranuleSpec(ref_for(gunw_id(t[1], t[0]), "GUNW", t[1]), "GUNW", ref_time=t[1], sec_time=t[0], dlos=-0.05, seed=3,
                     sign_flip=True)
    b, _ = build(base + [sp])
    i = b.stack().interferometric
    p = i.pairs[0]
    assert p.flipped and p.ref_time < p.sec_time and "pairs_flipped" in codes(b)
    g = b.grids.g80
    X, Y = np.meshgrid(g.x, g.y)
    ok = np.isfinite(i.los_m[0])
    assert np.sqrt(np.mean((i.los_m[0] - los_truth(TILE, X, Y, -0.05))[ok] ** 2)) < 0.002


def test_two_frames_of_one_pass_become_one_epoch_without_gaps():
    b, r = build(make_scene(TILE, pairs=(), split_frames=True, event=False))
    bs = b.stack().backscatter
    assert len(bs.epochs) == 6 and all(len(e.granule_ids) == 2 and len(e.frames) == 1 for e in bs.epochs)
    core = (slice(*b.grids.g20.core_slices()[0].indices(508)), slice(*b.grids.g20.core_slices()[1].indices(508)))
    assert bs.valid[:, core[0], core[1]].mean() > 0.999                # the two halves overlap and cover the whole tile
    assert "frame_overlap" in codes(b)
    assert len(r.fetches) == 1 and len(r.fetches[0]["granules"]) == 12   # one request per product, not per granule


def test_stack_in_a_neighbouring_utm_zone_lands_on_the_tile_grid():
    nominal_b, _ = build(make_scene(TILE, pairs=PAIRS))
    b, _ = build(make_scene(TILE, pairs=PAIRS, crs="neighbor"))
    i, j = nominal_b.stack(), b.stack()
    ok = np.isfinite(j.interferometric.los_m[-1])
    assert ok.mean() > 0.9
    g = b.grids.g80
    X, Y = np.meshgrid(g.x, g.y)
    assert np.sqrt(np.mean((j.interferometric.los_m[-1] - los_truth(TILE, X, Y, -0.05))[ok] ** 2)) < 0.004
    g20 = b.grids.g20
    X, _ = np.meshgrid(g20.x, g20.y)
    a, c = i.backscatter.gamma0_db["HV"][0], j.backscatter.gamma0_db["HV"][0]
    for region in (X < TILE.e0 + 4800, X > TILE.e0 + 5200):          # per land-cover half: a 50/50 median is unstable by nature
        both = region & np.isfinite(a) & np.isfinite(c)
        assert abs(np.nanmedian(a[both]) - np.nanmedian(c[both])) < 0.3


def test_ascending_and_descending_tracks_become_separate_stacks():
    asc = make_scene(TILE, n_epochs=4, pairs=((0, 1),), event=False)
    desc = make_scene(TILE, n_epochs=4, pairs=((0, 1),), event=False, track=12, direction="D", start="2026-07-03",
                      geom=GeomSpec(heading_deg=190.0, look="left", incidence_deg=35.0))
    b, _ = build(asc + desc)
    assert set(b.stacks) == {"A005", "D012"}
    ga, gd = b.stacks["A005"].geometry, b.stacks["D012"].geometry
    assert gd.incidence_deg.g80.mean() == pytest.approx(35.0, abs=0.01)
    assert np.sign(ga.los_enu.g80[0].mean()) != np.sign(gd.los_enu.g80[0].mean())  # east-looking vs west-looking geometry
    assert b.primary_key in b.stacks and b.depth.per_stack.keys() == {"A005", "D012"}


# ------------------------------------------------------------------------------------ failure handling
def test_no_granules_gives_an_empty_bundle_not_an_exception():
    b, _ = build([])
    assert b.status == "empty" and not b.stacks and b.capabilities == frozenset()
    assert "no_granules" in codes(b)
    assert_bundle_contract(b, deep=True)
    assert b.routing is not None and all(d.applies in ("yes", "no", "unknown") for d in b.routing.decisions.values())


def test_unreachable_search_is_reported_and_the_other_product_still_works():
    b, _ = build(make_scene(TILE, pairs=PAIRS), fail_search="GUNW")
    assert b.status == "partial" and "search_error" in codes(b, "error")
    s = b.stack()
    assert s.interferometric is None and s.backscatter is not None and "M2" in b.capabilities and "M1" not in b.capabilities


def test_failed_granule_download_is_flagged_and_skipped():
    specs = make_scene(TILE, pairs=PAIRS)
    bad = next(s.ref.granule_id for s in specs if s.kind == "GUNW" and s.dlos != 0.0)
    b, _ = build(specs, fail_ids=(bad,))
    assert b.status == "partial"
    f = [f for f in b.flags if f.code == "fetch_error"]
    assert len(f) == 1 and bad in f[0].message
    assert len(b.stack().interferometric.pairs) == 2


def test_stale_listing_marks_the_bundle_partial():
    b, _ = build(make_scene(TILE, pairs=PAIRS), degraded=True)
    assert b.status == "partial" and {"search_degraded", "fetch_degraded"} <= codes(b)


def test_older_processing_version_is_ignored():
    specs = make_scene(TILE, pairs=PAIRS)
    t = [dt("2026-08-18"), dt("2026-08-30")]
    old = GranuleSpec(ref_for(gunw_id(t[0], t[1], version=5009), "GUNW", t[0]), "GUNW", ref_time=t[0], sec_time=t[1], dlos=0.5, seed=99)
    b, _ = build(specs + [old])
    assert "older_versions_dropped" in codes(b) and len(b.stack().interferometric.pairs) == 3
    assert np.nanmax(np.abs(b.stack().interferometric.los_m)) < 0.2     # the 0.5 m decoy never reached the stack


def test_request_only_asks_for_the_active_codes():
    b, r = build(make_scene(TILE, pairs=PAIRS), active_codes={"M2"})
    assert [f["product"] for f in r.fetches] == ["GCOV"] and "M1" not in b.capabilities and "M2" in b.capabilities
    b2, r2 = build(make_scene(TILE, pairs=PAIRS), active_codes={"M4"})
    assert r2.fetches == [] and b2.status == "empty" and "no_nisar_measurement" in codes(b2)
    rx = r.fetches[0]["layers"]
    assert "HHHH" in rx and "unwrappedPhase" not in rx


def test_a_step_that_raises_is_contained(monkeypatch):
    from engine.common import p10_temporal_stack as p10

    def boom(ctx):
        raise RuntimeError("synthetic failure")
    monkeypatch.setattr(p10, "run", boom)
    import engine.common.chain as chain
    monkeypatch.setattr(chain, "STEPS", tuple(s if s.code != "P10" else type(s)(s.code, s.name, boom, s.needs) for s in chain.STEPS))
    b, _ = build(make_scene(TILE, pairs=PAIRS))
    assert b.status == "partial" and "step_failed" in codes(b, "error")
    rec = {s.code: s for s in b.provenance.steps}
    assert rec["P10"].status == "error" and rec["P11"].status == "ok" and rec["P13"].status == "ok"
    assert b.stack().backscatter.baseline.cold_start                       # P10's absence is visible, not papered over
    assert_bundle_contract(b)


def test_bad_tile_size_is_a_chain_error_not_a_crash():
    from engine.common.context import ChainError
    from engine.tiling.mgrs import Tile
    with pytest.raises(ChainError):
        build_scene_bundle(Tile(11, "S", 400000, 3700000, 1000), FakeRetriever([]), as_of=AS_OF,
                           cfg=__import__("dataclasses").replace(__import__("engine.common.config", fromlist=["x"]).load_common_config(env={}),
                                                                 grids=__import__("engine.common.config", fromlist=["x"]).GridsCfg(g80_m=300.0)))


# ------------------------------------------------------------------------------------ history / events
def test_short_history_is_a_cold_start_and_says_so():
    b, _ = build(make_scene(TILE, n_epochs=3, pairs=((0, 1),), event=False))
    bl = b.stack().backscatter.baseline
    assert bl.cold_start and bl.n_epochs == 2 and "cold_start" in codes(b)
    assert b.depth.cold_start and "baseline" not in b.capabilities
    assert "no_change" in b.stack().stable.missing


def test_event_window_keeps_event_epochs_out_of_the_baseline():
    b, _ = build(make_scene(TILE, pairs=PAIRS), event_window=("2026-07-14T00:00:00Z", "2026-07-15T00:00:00Z"))
    bl = b.stack().backscatter.baseline
    assert bl.epoch_indices == (0, 1) and bl.cold_start and "baseline_excludes_event" in codes(b)
    assert b.request.event_window == ("2026-07-14T00:00:00Z", "2026-07-15T00:00:00Z")
    r = b.routing.decisions["earthquake_coseismic"]
    assert r.applies == "yes" and "span the event window" in " ".join(r.reasons)     # Jul 13 -> Jul 25 spans Jul 14-15
    b2, _ = build(make_scene(TILE, pairs=PAIRS), event_window=("2026-08-05T00:00:00Z", "2026-08-07T00:00:00Z"))
    assert b2.routing.decisions["earthquake_coseismic"].applies == "no"            # no pair spans Aug 5-7


def test_future_acquisitions_are_ignored():
    b, _ = build(make_scene(TILE, pairs=PAIRS), as_of="2026-08-10T00:00:00Z")
    assert b.epoch == "2026-08-06T00:00:00Z" and len(b.stack().backscatter.epochs) == 4


# ------------------------------------------------------------------------------------ ancillary + routing
def test_ancillary_layers_are_aligned_and_reported(with_anc):
    b = with_anc
    a = b.ancillary
    assert_bundle_contract(b, deep=True)
    assert {"terrain", "hand", "landcover", "water_occurrence"} <= b.capabilities
    g = b.grids
    X, Y = np.meshgrid(g.g20.x, g.g20.y)
    east = X > TILE.e0 + 5100
    assert np.nanmedian(a.slope_deg.g20[east]) == pytest.approx(25.0, abs=0.5) and np.nanmedian(a.slope_deg.g20[X < TILE.e0 + 4900]) < 0.5
    assert set(np.unique(a.landcover.g20)) == {10, 30, 80} and a.landcover.g20.dtype == np.uint8
    assert a.status["dem"].available and a.status["dem"].source == "synthetic-dem" and a.status["landcover"].version == "t1"
    assert a.facts["volcano_distance_km"] == 30.0 and a.hand_valid_fraction > 0.9
    assert a.hand_m.g80[np.isfinite(a.hand_m.g80)].min() >= 0
    s = b.stack()
    assert s.geometry.height_source == "dem" and s.geometry.layover.available and s.geometry.local_incidence_deg.g80 is not None
    assert "no_dem" not in codes(b) and "slope" not in s.stable.missing and "not_water" not in s.stable.missing
    assert not s.stable.mask.g20[np.isclose(X, X) & (Y > TILE.n0 + 9600)].any()           # water (north 5 %) is not stable
    assert not s.stable.mask.g20[east].any()                                              # 25 deg slope is not stable


def test_routing_follows_the_manifest_rules(with_anc):
    d = with_anc.routing.decisions
    yes = {n for n, x in d.items() if x.applies == "yes"}
    assert {"earthquake_coseismic", "land_subsidence", "volcanic_deformation", "landslide", "flood_inundation", "deforestation",
            "wildfire_burn_scar", "river_erosion"} <= yes
    no = {n for n, x in d.items() if x.applies == "no"}
    assert {"glacier_ice_flow", "permafrost_thaw", "urban_damage", "tropical_cyclone", "tsunami_inundation", "oil_spill",
            "iceberg_calving_drift"} <= no
    assert d["volcanic_deformation"].reasons[0].startswith("nearest S7 volcano 30 km")
    assert with_anc.routing.quality.water_source == "landcover" and with_anc.routing.quality.water_fraction == pytest.approx(0.05, abs=0.01)
    assert with_anc.routing.landcover_hist["10"] == pytest.approx(0.475, abs=0.02)
    assert with_anc.routing.applicable == tuple(sorted(yes))


def test_routing_without_ancillary_says_unknown_not_no(nominal):
    d = nominal.routing.decisions
    for n in ("landslide", "deforestation", "glacier_ice_flow", "urban_damage", "river_erosion", "permafrost_thaw"):
        assert d[n].applies == "unknown" and d[n].needs, n
    assert d["flood_inundation"].proxy and nominal.routing.quality.water_source == "backscatter_proxy"
    assert "water_proxy" in codes(nominal)


def test_trigger_driven_rules():
    anc = None
    ctx = dict(ancillary=anc)
    b, _ = build(make_scene(TILE, pairs=PAIRS), triggers={"quake": {"mw": 7.1}, "wind_ms": 5.0})
    assert b.request.triggers["quake"]["mw"] == 7.1
    assert b.routing.decisions["tsunami_inundation"].applies in ("unknown", "no")        # needs shoreline + DEM: never a silent yes
    assert b.routing.decisions["tsunami_inundation"].applies != "yes"


def test_broken_ancillary_file_is_flagged_and_everything_else_survives(tmp_path):
    root = write_ancillary(tmp_path, TILE)
    (root / "landcover" / "lc.npz").write_bytes(b"not an npz")
    b, _ = build(make_scene(TILE, pairs=PAIRS), ancillary=DirectoryAncillary(root))
    assert "ancillary_error" in codes(b, "error") and b.status == "partial"
    assert not b.ancillary.landcover.available and b.ancillary.elevation_m.available and not b.ancillary.status["landcover"].available


def test_provider_that_raises_does_not_break_the_chain():
    class Bad:
        def raster(self, *a, **k):
            raise OSError("disk gone")

        def facts(self, tile):
            raise OSError("disk gone")
    b, _ = build(make_scene(TILE, pairs=PAIRS), ancillary=Bad())
    assert "ancillary_error" in codes(b, "error") and b.stack().interferometric is not None


# ------------------------------------------------------------------------------------ provenance
def test_bundle_id_is_deterministic_and_input_sensitive():
    specs = make_scene(TILE, pairs=PAIRS)
    a, _ = build(specs)
    b, _ = build(specs)
    assert a.bundle_id == b.bundle_id and a.provenance.inputs_hash == b.provenance.inputs_hash and a.bundle_id.startswith(TILE.id + "-")
    c, _ = build(make_scene(TILE, pairs=PAIRS, dlos=-0.06))
    assert c.bundle_id != a.bundle_id and c.provenance.inputs_hash != a.provenance.inputs_hash
    d, _ = build(specs, as_of="2026-09-21T00:00:00Z")
    assert d.bundle_id != a.bundle_id
    e, _ = build(specs, triggers={"storm": True})
    assert e.bundle_id != a.bundle_id and e.provenance.inputs_hash == a.provenance.inputs_hash
    import dataclasses
    from engine.common.config import RadarCfg, load_common_config
    cfg = dataclasses.replace(load_common_config(env={}), radar=RadarCfg(wavelength_m=0.24))
    f, _ = build(specs, cfg=cfg)
    assert f.bundle_id != a.bundle_id and f.provenance.params_hash != a.provenance.params_hash


def test_provenance_lists_inputs_and_unverified_conventions(nominal):
    p = nominal.provenance
    assert len(p.inputs) == 9 and {r.product for r in p.inputs} == {"GUNW", "GCOV"}      # 3 pairs + 6 epochs
    assert all(r.sha and r.layers for r in p.inputs) and p.code_version and p.params_hash
    text = " ".join(p.assumptions)
    for needle in ("phase sign", "wavelength", "LOS unit vector", "effective looks", "NOT already applied"):
        assert needle in text, needle
    assert p.contract_version == nominal.contract_version == "1.0"
