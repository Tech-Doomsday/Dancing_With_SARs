"""engine/common against analytic truth, end to end through the chain (FakeRetriever, no network).

Complements test_common_chain.py: those tests plant a signal and recover it on a flat, same-zone scene; these push
the same chain through the situations where a sign, an axis or a resampling step can quietly go wrong - terrain facing
toward / away from the sensor, drainage, a neighbouring UTM zone, integer fill conventions, missing layers - and compare
with numbers worked out by hand.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from common_fixtures import (FakeRetriever, GeomSpec, GranuleSpec, default_tile, dt, event_patch, gunw_id, los_truth,
                             make_scene, ref_for)
from engine.common import build_scene_bundle
from engine.common.ancillary import DirectoryAncillary, write_raster_npz
from engine.common.contract import check_bundle

AS_OF = "2026-09-20T00:00:00Z"
TILE = default_tile()
LAMBDA = 0.2384
PAIRS = ((0, 1), (1, 2), (4, 5))


def build(specs, **kw):
    kw.setdefault("as_of", AS_OF)
    r = FakeRetriever(specs)
    b = build_scene_bundle(TILE, r, **kw)
    assert check_bundle(b, deep=True) == []
    return b


def codes(b):
    return {f.code for f in b.flags}


def write_dem(root, fn):
    """DEM raster (30 m, +-6 km around the tile) with z = fn(east - e0, north - n0)."""
    n = int((TILE.size + 12000) / 30)
    x = TILE.e0 - 6000 + 30.0 * (np.arange(n) + 0.5)
    y = TILE.n0 + TILE.size + 6000 - 30.0 * (np.arange(n) + 0.5)
    X, Y = np.meshgrid(x, y)
    write_raster_npz(root / "dem" / "dem.npz", fn(X - TILE.e0, Y - TILE.n0).astype(np.float32), x, y, TILE.epsg,
                     source="analytic-dem", version="t")
    return X, Y


GEOM = GeomSpec()                      # 40 deg incidence, heading -10, left looking => ground->sensor azimuth 80 deg
H = (math.sin(math.radians(80.0)), math.cos(math.radians(80.0)))     # horizontal unit vector toward the sensor (E, N)


def toward_sensor_slope(deg):
    """DEM gradient (dz/dE, dz/dN) of a plane that rises/falls by `deg` along the direction toward the sensor.
    Negative deg = descends toward the sensor = a slope FACING the sensor."""
    t = math.tan(math.radians(deg))
    return t * H[0], t * H[1]


# ------------------------------------------------------------------------------------ layover / shadow / local incidence
@pytest.mark.parametrize("face_deg, layover, shadow", [
    (-20.0, False, False),       # faces the sensor, gentler than the incidence angle: foreshortened only
    (-55.0, True, False),        # faces the sensor, steeper than 40 deg: layover
    (+20.0, False, False),       # faces away
    (+55.0, False, True),        # faces away, steeper than 90 - 40 = 50 deg: radar shadow
])
def test_layover_and_shadow_follow_the_side_the_slope_faces(tmp_path, face_deg, layover, shadow):
    gx, gy = toward_sensor_slope(face_deg)
    write_dem(tmp_path, lambda e, n: 1500.0 + gx * e + gy * n)
    b = build(make_scene(TILE, pairs=PAIRS), ancillary=DirectoryAncillary(tmp_path))
    g = b.stack().geometry
    rs, cs = b.grids.g80.core_slices()
    inner = (slice(rs.start + 3, rs.stop - 3), slice(cs.start + 3, cs.stop - 3))
    assert g.height_source == "dem"
    for name in ("g80", "g20"):
        lay, sha = getattr(g.layover, name), getattr(g.shadow, name)
        sl = inner if name == "g80" else (slice(inner[0].start * 4, inner[0].stop * 4), slice(inner[1].start * 4, inner[1].stop * 4))
        assert lay[sl].mean() == (1.0 if layover else 0.0), (name, face_deg)
        assert sha[sl].mean() == (1.0 if shadow else 0.0), (name, face_deg)
    # local incidence: the angle between the surface normal and the sensor direction
    n = np.array([-gx, -gy, 1.0])
    n /= np.linalg.norm(n)
    los = g.los_enu.g80[:, inner[0], inner[1]].reshape(3, -1).mean(axis=1)
    expect = math.degrees(math.acos(float(np.clip(n @ los, -1, 1))))
    assert np.nanmedian(g.local_incidence_deg.g80[inner]) == pytest.approx(expect, abs=0.3)
    # the static backscatter masks are the same layers, and the tile quality counts them
    m = b.stack().backscatter.masks
    assert (m.layover[: , :].mean() > 0.5) == layover and (m.shadow.mean() > 0.5) == shadow
    assert (b.stack().quality.layover_shadow_fraction > 0.5) == (layover or shadow)


def test_flat_terrain_has_local_incidence_equal_to_the_product_incidence(tmp_path):
    write_dem(tmp_path, lambda e, n: np.full_like(e, 700.0))
    b = build(make_scene(TILE, pairs=PAIRS), ancillary=DirectoryAncillary(tmp_path))
    g = b.stack().geometry
    assert np.nanmax(np.abs(g.local_incidence_deg.g80 - g.incidence_deg.g80)) < 0.05
    assert not g.layover.g80.any() and not g.shadow.g80.any()
    assert np.nanmax(b.ancillary.slope_deg.g80) < 1e-3 and np.isnan(b.ancillary.aspect_deg.g80).all()


def test_slope_and_aspect_of_a_tilted_plane(tmp_path):
    t = math.tan(math.radians(20.0))
    write_dem(tmp_path, lambda e, n: 100.0 + t * e)                   # rises toward the east => faces WEST (aspect 270)
    a = build(make_scene(TILE, pairs=PAIRS), ancillary=DirectoryAncillary(tmp_path)).ancillary
    assert np.nanmedian(a.slope_deg.g80) == pytest.approx(20.0, abs=0.05)
    assert np.nanmedian(a.aspect_deg.g80) == pytest.approx(270.0, abs=0.5)
    assert np.nanmedian(a.slope_deg.g20) == pytest.approx(20.0, abs=0.05)
    assert np.nanmedian(a.elevation_m.g80) == pytest.approx(100.0 + t * 5000.0, abs=15.0)


# ------------------------------------------------------------------------------------ HAND
def test_hand_in_parallel_v_valleys_is_the_height_above_the_nearest_channel(tmp_path):
    """Valleys 3 km apart (ridges between), 10 deg flanks, flat channel floors. Every channel collects > hand_min_drainage_km2
    (a single V without ridges would, correctly, turn its own upper flank into 'stream' too, so ridges are part of the truth)."""
    theta = math.radians(10.0)
    period, xs = 3000.0, 2500.0

    def dist_to_axis(e):
        return np.abs(((e - xs + period / 2) % period) - period / 2)
    write_dem(tmp_path, lambda e, n: 300.0 + dist_to_axis(e) * math.tan(theta))
    b = build(make_scene(TILE, pairs=PAIRS), ancillary=DirectoryAncillary(tmp_path))
    g = b.grids.g80
    X80, _ = np.meshgrid(g.x, g.y)
    d = dist_to_axis(X80 - TILE.e0)
    h = b.ancillary.hand_m.g80
    sel = (d > 200) & (d < 1300)
    expect = d * math.tan(theta)
    ok = sel & np.isfinite(h)
    assert ok.sum() > 0.95 * sel.sum()
    # HAND is measured to the channel PIXEL, whose centre lies up to half a pixel from the true axis: <= 40 m * tan(10 deg) = 7 m
    assert np.median(np.abs(h[ok] - expect[ok])) < 0.5 * 80.0 * math.tan(theta)
    assert np.abs(h[ok] - expect[ok]).max() < 80.0 * math.tan(theta) * 2 + 1.0
    assert np.nanmax(h[d < 45]) < 8.0                                  # the channel floor is at zero
    assert h[ok][expect[ok] > 150].min() > 100.0                       # high ground is high, not 'on the stream'
    assert b.ancillary.hand_valid_fraction > 0.9


def test_permanent_water_defines_drainage_even_when_the_catchment_is_small(tmp_path):
    """One narrow V (walls capped by a plateau that drains elsewhere is not needed: use the water layer as the channel)."""
    theta = math.radians(10.0)
    xs = 2500.0
    write_dem(tmp_path, lambda e, n: 300.0 + np.minimum(np.abs(e - xs), 600.0) * math.tan(theta) + 0.0 * n)
    x = TILE.e0 - 6000 + 30.0 * (np.arange(int((TILE.size + 12000) / 30)) + 0.5)
    y = TILE.n0 + TILE.size + 6000 - 30.0 * (np.arange(int((TILE.size + 12000) / 30)) + 0.5)
    X, _ = np.meshgrid(x, y)
    write_raster_npz(tmp_path / "water_occurrence" / "wo.npz",
                     np.where(np.abs(X - TILE.e0 - xs) < 20, 100.0, 0.0).astype(np.float32), x, y, TILE.epsg)
    b = build(make_scene(TILE, pairs=PAIRS), ancillary=DirectoryAncillary(tmp_path))
    g = b.grids.g80
    X80, _ = np.meshgrid(g.x, g.y)
    d = np.abs(X80 - TILE.e0 - xs)
    h = b.ancillary.hand_m.g80
    near = (d > 100) & (d < 500)
    assert np.nanmedian(np.abs(h[near] - d[near] * math.tan(theta))) < 0.5 * 80.0 * math.tan(theta)     # half-pixel channel offset
    assert np.nanmax(h[d < 45]) < 2.0


def test_hand_is_unknown_not_zero_where_nothing_drains_to_a_stream(tmp_path):
    write_dem(tmp_path, lambda e, n: 100.0 + 0.02 * n)                # a planar ramp: no stream inside the window
    b = build(make_scene(TILE, pairs=PAIRS), ancillary=DirectoryAncillary(tmp_path))
    h = b.ancillary.hand_m.g80
    assert h is not None and (np.isnan(h).mean() > 0.0 or np.nanmax(h) > 0.0)       # never a blanket 0 "on the stream"
    assert not (np.isfinite(h) & (h == 0)).all()


# ------------------------------------------------------------------------------------ a neighbouring UTM zone
def test_stack_in_a_neighbouring_zone_recovers_the_truth_and_keeps_the_gate_exact():
    b = build(make_scene(TILE, pairs=PAIRS, crs="neighbor"))
    i = b.stack().interferometric
    g = b.grids.g80
    X, Y = np.meshgrid(g.x, g.y)
    truth = los_truth(TILE, X, Y, -0.05)
    est = i.los_m[-1]
    rs, cs = g.core_slices()
    ok = np.isfinite(est)
    sub = np.zeros_like(ok)
    sub[rs, cs] = True
    ok &= sub
    assert ok.sum() > 0.9 * sub.sum()
    assert np.sqrt(np.mean((est - truth)[ok] ** 2)) < 0.004
    assert not np.any((i.conn_comp == 0) & np.isfinite(i.phase_rad))          # the gate survives resampling
    assert not np.any((i.conn_comp == 0) & np.isfinite(i.los_m))


# ------------------------------------------------------------------------------------ integer fill conventions
def _cc_with_fill(dtype, fill):
    def f(cc):
        out = cc.astype(dtype)
        out[cc == 0] = fill
        return out
    return f


@pytest.mark.parametrize("dtype, fill", [(np.uint8, 255), (np.uint16, 65535), (np.uint32, 2 ** 32 - 1)])
def test_connected_component_fill_value_is_never_a_usable_component(dtype, fill):
    t = [dt("2026-07-01"), dt("2026-07-13")]
    spec = GranuleSpec(ref_for(gunw_id(t[0], t[1]), "GUNW", t[0]), "GUNW", ref_time=t[0], sec_time=t[1], dlos=-0.05, seed=3,
                       fills={"HH/connectedComponents": _cc_with_fill(dtype, fill)})
    gc = [s for s in make_scene(TILE, n_epochs=2, pairs=(), event=False)]
    b = build(gc + [spec])
    i = b.stack().interferometric
    g = b.grids.g80
    X, Y = np.meshgrid(g.x, g.y)
    bad = (X > TILE.e0 + 1000) & (X < TILE.e0 + 2000) & (Y > TILE.n0 + 7000) & (Y < TILE.n0 + 8500)
    assert (i.conn_comp[0][bad] == 0).all() and np.isnan(i.los_m[0][bad]).all()
    assert i.conn_comp.dtype == np.uint16


def test_nan_and_inf_phase_never_reach_los_or_sigma():
    def poison(a):
        a = a.copy()
        a[10:14, 10:14] = np.nan
        a[20:22, 20:22] = np.inf
        a[30, 30] = -np.inf
        return a
    t = [dt("2026-07-01"), dt("2026-07-13")]
    spec = GranuleSpec(ref_for(gunw_id(t[0], t[1]), "GUNW", t[0]), "GUNW", ref_time=t[0], sec_time=t[1], dlos=-0.05, seed=3,
                       fills={"HH/unwrappedPhase": poison})
    b = build(make_scene(TILE, n_epochs=2, pairs=(), event=False) + [spec])
    i = b.stack().interferometric
    assert np.isfinite(i.los_m).sum() > 0 and not np.isinf(i.los_m).any() and not np.isinf(i.sigma_m).any()
    assert np.isnan(i.sigma_m[np.isnan(i.los_m)]).all()


def test_coherence_outside_0_1_is_corrupt_and_becomes_unknown_not_clipped_into_trust():
    def corrupt(c):
        c = c.copy()
        c[40:60, 40:60] = 3.0                                          # 400 native pixels
        return c
    t = [dt("2026-07-01"), dt("2026-07-13")]

    def mk(fills):
        spec = GranuleSpec(ref_for(gunw_id(t[0], t[1]), "GUNW", t[0]), "GUNW", ref_time=t[0], sec_time=t[1], dlos=-0.05, seed=3,
                           fills=fills)
        return build(make_scene(TILE, n_epochs=2, pairs=(), event=False) + [spec]).stack().interferometric
    clean, bad = mk({}), mk({"HH/coherenceMagnitude": corrupt})
    assert np.nanmax(bad.coherence) <= 1.0                             # nothing was clipped to 1 and trusted
    extra = int(np.isnan(bad.coherence).sum() - np.isnan(clean.coherence).sum())
    assert 300 <= extra <= 420, extra                                  # the 400 corrupt pixels became unknown ...
    corrupted = np.isnan(bad.coherence) & ~np.isnan(clean.coherence)
    assert np.isnan(bad.sigma_m[corrupted]).all()                      # ... and carry no sigma


# ------------------------------------------------------------------------------------ geometry without every layer
def test_incidence_is_derived_from_the_vector_when_the_product_layer_is_missing():
    specs = make_scene(TILE, pairs=PAIRS)
    for s in specs:
        s.drop_layers = ("incidenceAngle",)
    g = build(specs).stack().geometry
    assert g.vectors_available and g.consistency_deg is None
    assert np.nanmedian(g.incidence_deg.g80) == pytest.approx(40.0, abs=0.05)


def test_scalar_only_geometry_is_unavailable_not_guessed():
    specs = make_scene(TILE, pairs=PAIRS)
    for s in specs:
        s.drop_layers = ("losUnitVectorX", "losUnitVectorY")
    b = build(specs)
    g = b.stack().geometry
    assert not g.vectors_available and g.los_enu.g80 is None and "geometry" not in b.capabilities
    assert "no_los_vectors" in codes(b)
    assert np.nanmedian(g.incidence_deg.g80) == pytest.approx(40.0, abs=0.05)


def test_incidence_gradient_across_range_is_preserved_on_the_canonical_grid():
    geom = GeomSpec(incidence_gradient_deg_per_km=0.5)               # 5 deg across the 10 km tile, increasing east
    b = build(make_scene(TILE, pairs=PAIRS, geom=geom))
    inc = b.stack().geometry.incidence_deg.g80
    g = b.grids.g80
    X, _ = np.meshgrid(g.x, g.y)
    expect = 40.0 + 0.5 * (X - TILE.e0 - 5000) / 1000.0
    rs, cs = g.core_slices()
    assert np.nanmax(np.abs(inc - expect)[rs, cs]) < 0.2
    e = b.stack().geometry.los_enu.g80
    assert np.nanmax(np.abs(np.degrees(np.arccos(e[2])) - inc)[rs, cs]) < 0.2         # vector and incidence agree everywhere


# ------------------------------------------------------------------------------------ stack-level invariants
def test_building_twice_gives_bit_identical_arrays_and_the_same_id():
    specs = make_scene(TILE, pairs=PAIRS)
    a, b = build(specs), build(specs)
    assert a.bundle_id == b.bundle_id
    for name in ("los_m", "sigma_m", "coherence", "phase_rad", "conn_comp"):
        assert np.array_equal(getattr(a.stack().interferometric, name), getattr(b.stack().interferometric, name), equal_nan=True)
    for pol in a.stack().backscatter.gamma0_db:
        assert np.array_equal(a.stack().backscatter.gamma0_db[pol], b.stack().backscatter.gamma0_db[pol], equal_nan=True)


def test_order_of_granules_in_the_listing_does_not_change_the_bundle():
    specs = make_scene(TILE, pairs=PAIRS)
    a = build(specs)
    b = build(list(reversed(specs)))
    assert a.bundle_id == b.bundle_id
    assert np.array_equal(a.stack().interferometric.los_m, b.stack().interferometric.los_m, equal_nan=True)


def test_sigma_is_monotone_in_coherence_and_matches_the_formula_pixelwise():
    from engine.common.p06_phase_to_los import cramer_rao_sigma_m
    coh = np.array([0.1, 0.3, 0.5, 0.7, 0.9, 0.99, np.nan, 1.0], np.float32)
    s = cramer_rao_sigma_m(coh, 100.0, LAMBDA, 3.0, 0.3)
    assert np.isnan(s[0]) and np.isnan(s[6])                          # below min_coherence / unknown
    v = s[[1, 2, 3, 4, 5]]
    assert np.all(np.diff(v) < 0)
    c = np.float64(0.7)
    assert s[3] == pytest.approx(math.sqrt((1 - c * c) / (2 * 100 * c * c)) * LAMBDA / (4 * math.pi), rel=1e-5)
    assert np.isfinite(s[7]) and s[7] >= 0


def test_collapse_height_interpolates_linearly_and_clamps():
    from engine.common.p05_geometry import collapse_height
    h = np.array([-500.0, 1000.0, 3000.0])
    cube = np.stack([np.full((2, 2), 30.0), np.full((2, 2), 40.0), np.full((2, 2), 45.0)]).astype(np.float32)
    at = np.array([[-500.0, 250.0], [3000.0, 9000.0]])
    out = collapse_height(cube, h, at)
    assert out == pytest.approx(np.array([[30.0, 35.0], [45.0, 45.0]]), abs=1e-4)
    out = collapse_height(cube, h, np.array([[-9000.0, 2000.0], [1000.0, np.nan]]))
    assert out == pytest.approx(np.array([[30.0, 42.5], [40.0, 30.0]]), abs=1e-4)           # NaN height -> bottom of the axis


def test_grid_convergence_is_zero_on_the_central_meridian_and_grows_east():
    from engine.common.p05_geometry import convergence_deg
    from engine.tiling.mgrs import tile_at
    on = tile_at(-117.0, 34.0)                                        # zone 11 central meridian is -117
    east = tile_at(-115.0, 34.0)
    west = tile_at(-119.0, 34.0)
    assert abs(convergence_deg(on)) < 0.06
    assert convergence_deg(east) > 0.9 > 0 > convergence_deg(west) < -0.9


# ------------------------------------------------------------------------------------ P07 diagnostics (ramp, elevation)
def test_planar_ramp_recovers_a_known_plane_and_ignores_nan():
    from engine.common.p07_correction_audit import planar_ramp
    ny = nx = 60
    r, c = np.mgrid[0:ny, 0:nx]
    east_km, north_km = c * 0.08, -r * 0.08                        # 80 m pixels, rows run north -> south
    a = (0.002 * east_km - 0.003 * north_km + 0.01).astype(np.float32)   # 2 mm/km east, -3 mm/km north ... in metres
    a[5:9, 5:9] = np.nan
    amp, gx, gy = planar_ramp(a, 80.0, 80.0)
    assert gx == pytest.approx(2.0, abs=1e-3) and gy == pytest.approx(-3.0, abs=1e-3)
    assert amp == pytest.approx((2.0 + 3.0) * 60 * 0.08, rel=1e-3)
    assert planar_ramp(np.full((5, 5), np.nan), 80.0, 80.0) is None


def test_elevation_correlation_is_plus_or_minus_one_for_a_linear_relation():
    from engine.common.p07_correction_audit import elevation_correlation
    z = np.random.default_rng(0).uniform(0, 1000, (40, 40))
    assert elevation_correlation(0.001 * z, z) == pytest.approx(1.0)
    assert elevation_correlation(-0.001 * z + 3.0, z) == pytest.approx(-1.0)
    assert np.isnan(elevation_correlation(np.ones((40, 40)), z))
    assert np.isnan(elevation_correlation(z[:5, :5], z[:5, :5]))


def test_ramp_in_the_phase_shows_up_in_the_chain_diagnostics_and_is_never_removed():
    a_rad_per_px = 0.05

    def add_ramp(phase):
        return (phase + a_rad_per_px * np.arange(phase.shape[1])[None, :]).astype(np.float32)
    specs = make_scene(TILE, pairs=((0, 1),), event=False)
    for s in specs:
        if s.kind == "GUNW":
            s.fills = {"HH/unwrappedPhase": add_ramp}
    b = build(specs)
    i = b.stack().interferometric
    d = i.corrections.diagnostics
    expect = -LAMBDA / (4 * math.pi) * a_rad_per_px / 0.08 * 1e3                    # mm per km, toward sensor
    assert len(d["planar_ramp_amplitude_mm"]) == len(i.pairs) == 1
    assert d["ramp_gradient_x_mm_per_km"][0] == pytest.approx(expect, rel=0.05)
    assert abs(d["ramp_gradient_y_mm_per_km"][0]) < 0.05 * abs(expect)
    assert d["planar_ramp_amplitude_mm"][0] == pytest.approx(abs(expect) * 10.0, rel=0.1)
    assert "elevation_correlation_r" not in d                                       # no DEM -> not invented
    assert all(it.applied_by_engine is False for it in i.corrections.items.values())
    k = -LAMBDA / (4 * math.pi)
    ok = np.isfinite(i.los_m)
    assert np.allclose(i.los_m[ok], k * i.phase_rad[ok], atol=1e-7)                 # los_m still carries the ramp


def test_elevation_correlated_phase_is_reported_with_a_dem(tmp_path):
    write_dem(tmp_path, lambda e, n: 200.0 + 0.1 * e)                               # 0 .. 1.2 km ramp rising east
    specs = make_scene(TILE, pairs=((0, 1),), event=False)
    for s in specs:
        if s.kind == "GUNW":
            s.fills = {"HH/unwrappedPhase": lambda p: (p + 0.05 * np.arange(p.shape[1])[None, :]).astype(np.float32)}
    d = build(specs, ancillary=DirectoryAncillary(tmp_path)).stack().interferometric.corrections.diagnostics
    assert d["elevation_correlation_r"][0] < -0.9          # phase ramp east, DEM rises east, los = -lambda/4pi * phase
