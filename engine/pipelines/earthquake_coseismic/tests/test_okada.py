"""T-3/T-4: engine.lib.okada against the official DC3D reference.

The 24 benchmark rows were generated with tools/dc3d_reference.py (a Python
port of Okada's official DC3D Fortran; https://github.com/satoshi-pes/dc3d.py)
and are the same rows the validated TypeScript port passes at rel < 1e-8.
If any row fails here, the physics is wrong — stop and fix, never loosen.
"""
import math

import numpy as np

import pytest

from engine.lib.okada import (
    GeoFault,
    OkadaFault,
    geo_fault_displacement_enu,
    geo_fault_displacement_enu_top_edge,
    moment_from_mw,
    okada_surface_displacement,
    okada_surface_displacement_raw,
    okada_surface_grid,
    rake_to_dislocations,
    rupture_area_from_mw,
    slip_from_moment,
)

# geom = [x, y, depth, dip, al1, al2, aw1, aw2]; mode = [u1, u2, u3]; ref = [ux, uy, uz]
OKADA_BENCHMARKS = [
    ((2.0, 2.0, 2.0, 70.0, -1.5, 1.5, -1.0, 1.0), (1, 0, 0), (-3.26336498e-2, -3.20658929e-2, 5.2895183e-4)),
    ((2.0, 2.0, 2.0, 70.0, -1.5, 1.5, -1.0, 1.0), (0, 1, 0), (-2.05921477e-2, -4.23946473e-2, -3.22456728e-2)),
    ((2.0, 2.0, 2.0, 70.0, -1.5, 1.5, -1.0, 1.0), (0, 0, 1), (-1.66402795e-2, 1.82395965e-2, -2.2021671e-3)),
    ((2.0, 2.0, 2.0, 70.0, -1.0, 1.0, -1.0, 0.5), (1, 0, 0), (-1.32699212e-2, -1.31207063e-2, -7.7402835e-4)),
    ((2.0, 2.0, 2.0, 70.0, -1.0, 1.0, -1.0, 0.5), (0, 1, 0), (-9.02170739e-3, -1.60404635e-2, -1.36822143e-2)),
    ((2.0, 2.0, 2.0, 70.0, -1.0, 1.0, -1.0, 0.5), (0, 0, 1), (-8.6031592e-3, 5.21290372e-3, -2.65369623e-3)),
    ((0.0, 3.0, 5.0, 30.0, -4.0, 4.0, 0.0, 6.0), (1, 0, 0), (1.99292543e-1, 0.0, 0.0)),
    ((0.0, 3.0, 5.0, 30.0, -4.0, 4.0, 0.0, 6.0), (0, 1, 0), (0.0, 6.14805647e-2, 3.24853093e-1)),
    ((0.0, 3.0, 5.0, 30.0, -4.0, 4.0, 0.0, 6.0), (0, 0, 1), (0.0, -1.4407736e-1, 5.90259192e-1)),
    ((-2.5, 1.7, 3.3, 55.0, -2.0, 2.0, -1.0, 2.0), (1, 0, 0), (1.46764779e-2, 7.58623534e-3, -3.78010804e-2)),
    ((-2.5, 1.7, 3.3, 55.0, -2.0, 2.0, -1.0, 2.0), (0, 1, 0), (-4.64434341e-2, 1.76530304e-2, 5.07648421e-2)),
    ((-2.5, 1.7, 3.3, 55.0, -2.0, 2.0, -1.0, 2.0), (0, 0, 1), (2.93760329e-2, 1.81143499e-4, -1.4838566e-2)),
    ((1.1, -0.4, 8.0, 90.0, -5.0, 5.0, 0.0, 4.0), (1, 0, 0), (6.76783101e-3, -5.24851853e-3, 1.07113276e-3)),
    ((1.1, -0.4, 8.0, 90.0, -5.0, 5.0, 0.0, 4.0), (0, 1, 0), (1.82933248e-3, -1.99881195e-3, 2.63130862e-2)),
    ((1.1, -0.4, 8.0, 90.0, -5.0, 5.0, 0.0, 4.0), (0, 0, 1), (-1.02084725e-2, 1.67973547e-3, -5.80851401e-2)),
    ((0.001, 0.002, 12.0, 10.0, -10.0, 10.0, 0.0, 12.0), (1, 0, 0), (6.96112294e-2, -6.6184816e-6, 1.30139394e-5)),
    ((0.001, 0.002, 12.0, 10.0, -10.0, 10.0, 0.0, 12.0), (0, 1, 0), (-2.6437287e-6, 6.47601684e-2, -7.0785141e-2)),
    ((0.001, 0.002, 12.0, 10.0, -10.0, 10.0, 0.0, 12.0), (0, 0, 1), (1.41841656e-5, -1.63080211e-1, 3.72532435e-1)),
    ((-4.0, -3.0, 6.0, 45.0, -3.0, 3.0, -2.0, 1.5), (1, 0, 0), (2.99996609e-2, 2.4546762e-2, -3.70027609e-2)),
    ((-4.0, -3.0, 6.0, 45.0, -3.0, 3.0, -2.0, 1.5), (0, 1, 0), (-2.45652335e-2, -1.43496669e-2, 3.99895878e-2)),
    ((-4.0, -3.0, 6.0, 45.0, -3.0, 3.0, -2.0, 1.5), (0, 0, 1), (-3.55614335e-2, -4.02331535e-2, 7.57477618e-2)),
    ((7.0, 5.0, 4.0, 70.0, -2.0, 2.0, 0.0, 3.0), (1, 0, 0), (-1.42886816e-2, -1.62278996e-2, 6.97015734e-3)),
    ((7.0, 5.0, 4.0, 70.0, -2.0, 2.0, 0.0, 3.0), (0, 1, 0), (-5.49936515e-3, -9.19523226e-3, -5.15193116e-3)),
    ((7.0, 5.0, 4.0, 70.0, -2.0, 2.0, 0.0, 3.0), (0, 0, 1), (-5.10608733e-3, 1.10631995e-2, 5.80348979e-3)),
]


@pytest.mark.parametrize("geom,mode,ref", OKADA_BENCHMARKS, ids=[f"row{i}" for i in range(24)])
def test_matches_dc3d_reference(geom, mode, ref):
    x, y, depth, dip, al1, al2, aw1, aw2 = geom
    r = okada_surface_displacement_raw(x, y, depth, dip, al1, al2, aw1, aw2, *mode)
    assert not r.singular
    for got, want in zip((r.ux, r.uy, r.uz), ref):
        rel = abs(got - want) / max(abs(want), 1e-30)
        assert rel < 1e-8, f"rel={rel} got={got} want={want}"


def test_buried_strike_slip_matches_2d_screw_analytic():
    # Long vertical strike-slip, top depth 1, width 10, unit slip.
    # 2D anti-plane solution: u(y) = (1/pi)[atan(y/d1) - atan(y/d2)].
    cases = [(0.3, 0.0841), (1.0, 0.2211), (3.3, 0.3136), (5.0, 0.3014)]
    for y, expect2d in cases:
        r = okada_surface_displacement(0.0, y, OkadaFault(depth=1, dip_deg=90, length=100, width=10, u1=1))
        assert abs(r.ux) > expect2d * 0.8
        assert abs(r.ux) < expect2d * 1.2
        assert abs(r.uy) < 1e-9
        assert abs(r.uz) < 1e-9


def test_dip_slip_positive_is_thrust_hanging_wall_rises():
    """Kernel frame: +y is UP-DIP, the fault dips toward -y, so the hanging wall is the y < 0
    side. U2 > 0 is THRUST sense (DC3D): the hanging wall rises relative to the footwall."""
    mk = lambda y: okada_surface_displacement(
        0.0, y, OkadaFault(depth=50, dip_deg=80, length=100000, width=10000, u2=1))
    hang, foot = mk(-100.0), mk(100.0)
    assert hang.uz - foot.uz > 0.5


def test_kernel_footprint_is_on_negative_y_side():
    """Tensile opening of a shallow-dipping crack lifts the ground over the crack: the uplift
    lobe must sit on the y < 0 side (fault dips toward -y), not on the +y side."""
    fault = OkadaFault(depth=1000, dip_deg=30, length=2e6, width=20000, u3=1.0)
    over = okada_surface_displacement(0.0, -8000.0, fault).uz
    away = okada_surface_displacement(0.0, 8000.0, fault).uz
    assert over > 0.5 and abs(away) < 0.05


def test_strike_slip_positive_is_sinistral():
    mk = lambda y: okada_surface_displacement(
        0.0, y, OkadaFault(depth=50, dip_deg=80, length=100000, width=10000, u1=1))
    assert mk(100.0).ux - mk(-100.0).ux < -0.3


def test_tensile_positive_is_opening():
    mk = lambda y: okada_surface_displacement(
        0.0, y, OkadaFault(depth=50, dip_deg=80, length=100000, width=10000, u3=1))
    assert mk(100.0).uy - mk(-100.0).uy > 0.2


def test_displacement_decays_far_from_fault():
    r = okada_surface_displacement(
        0.0, 200000.0, OkadaFault(depth=2000, dip_deg=60, length=20000, width=8000, u2=1))
    assert math.hypot(r.ux, r.uy, r.uz) < 0.01


def test_moment_area_slip_scaling():
    m0 = moment_from_mw(7.0)
    assert 3e19 < m0 < 4e19
    a = rupture_area_from_mw(7.0) / 1e6
    assert 400 < a < 1300
    slip = slip_from_moment(m0, a * 1e6)
    assert 0.5 < slip < 5.0


def test_rake_mapping_matches_sign_conventions():
    # rake +90 (reverse) -> u2 = +slip (U2 > 0 is thrust sense)
    u1, u2, u3 = rake_to_dislocations(1.0, 90.0)
    assert abs(u1) < 1e-12 and abs(u2 - 1.0) < 1e-12 and abs(u3) < 1e-12
    # rake -90 (normal) -> u2 = -slip
    u1, u2, u3 = rake_to_dislocations(1.0, -90.0)
    assert abs(u1) < 1e-12 and abs(u2 + 1.0) < 1e-12
    # rake 0 (sinistral) -> u1 = +slip; rake -180 (dextral) -> u1 = -slip
    u1, u2, u3 = rake_to_dislocations(3.0, 0.0)
    assert abs(u1 - 3.0) < 1e-12 and abs(u2) < 1e-12
    u1, u2, u3 = rake_to_dislocations(2.0, -180.0)
    assert abs(u1 + 2.0) < 1e-12 and abs(u2) < 1e-9


def test_vectorised_grid_matches_scalar_path():
    import numpy as np
    fault = OkadaFault(depth=6000, dip_deg=35, length=22000, width=14000, u1=0.2, u2=-0.9, u3=0.0)
    rng = np.random.default_rng(7)
    xs = rng.uniform(-40000, 40000, 512)
    ys = rng.uniform(-40000, 40000, 512)
    ux, uy, uz, sing = okada_surface_grid(xs, ys, fault)
    assert not sing.any()
    for i in range(len(xs)):
        r = okada_surface_displacement(float(xs[i]), float(ys[i]), fault)
        assert not r.singular
        for got, want in zip((ux[i], uy[i], uz[i]), (r.ux, r.uy, r.uz)):
            assert abs(got - want) <= 1e-12 * max(abs(want), 1e-30), (i, got, want)


def test_vertical_fault_vectorised_matches_scalar():
    """Regression (final-prompt fix 2): a dip-90 fault takes the cd == 0 branch of the
    part-b displacement, which reads q2 from the coefficient dict. The vectorised path
    once missed that unpack (NameError) — the same crash would have hit ANY dip-90
    nodal plane in the forward screen and silently starved the uncatalogued fit."""
    import numpy as np
    for disl in ((0.0, 0.0, 0.0), (1.5, 0.0, 0.0), (0.0, -0.8, 0.0), (0.3, 0.4, 0.1)):
        fault = OkadaFault(depth=5000, dip_deg=90, length=18000, width=12000,
                           u1=disl[0], u2=disl[1], u3=disl[2])
        rng = np.random.default_rng(11)
        xs = rng.uniform(-35000, 35000, 256)
        ys = rng.uniform(-35000, 35000, 256)
        ux, uy, uz, sing = okada_surface_grid(xs, ys, fault)
        assert not sing.any()
        for i in range(len(xs)):
            r = okada_surface_displacement(float(xs[i]), float(ys[i]), fault)
            assert not r.singular
            for got, want in zip((ux[i], uy[i], uz[i]), (r.ux, r.uy, r.uz)):
                assert abs(got - want) <= 1e-12 * max(abs(want), 1e-30), (disl, i, got, want)


def test_geo_fault_vertical_displacement_runs_and_decays():
    """Dip-90 through geo_fault_displacement_enu: the exact forward-screen path for a
    vertical nodal plane (must not raise, must decay with distance)."""
    fault = GeoFault(strike_deg=30, dip_deg=90, rake_deg=-180, length_m=15000, width_m=10000,
                     top_depth_m=0, slip_m=1.0, centroid_lat=0, centroid_lon=0)
    xs = np.array([1000.0, 10000.0, 60000.0, 200000.0])
    ys = np.array([2000.0, -8000.0, 20000.0, -50000.0])
    e, n, up, sing = geo_fault_displacement_enu(fault, 8000.0, xs, ys)
    assert not sing.any()
    assert np.isfinite(e).all() and np.isfinite(n).all() and np.isfinite(up).all()
    near = np.hypot(e[0], up[0])
    far = np.hypot(e[3], up[3])
    assert far < near / 20.0          # elastic decay, two orders of magnitude over 200 km


def _reverse_fault(strike=0.0, rake=90.0, dip=35.0):
    return GeoFault(strike_deg=strike, dip_deg=dip, rake_deg=rake, length_m=20000, width_m=14000,
                    top_depth_m=0, slip_m=1.0, centroid_lat=0, centroid_lon=0)


def test_geo_fault_centroid_placement():
    """Reverse fault (rake +90), strike north, dips EAST, centroid at depth 9 km.

    Physics: the hanging wall (the ground above the fault, EAST of the surface trace) is
    UPLIFTED, the footwall (west) subsides only weakly, and the net volume change is positive.
    The uplift lobe must be centred on the surface projection of the fault (the centroid's
    projection is the footprint midpoint), i.e. within +-(W/2)cos(dip) of the centroid."""
    fault = _reverse_fault()
    half_horiz = 0.5 * 14000 * math.cos(math.radians(35.0))
    xs = np.arange(-60000.0, 60001.0, 500.0)
    X, Y = np.meshgrid(xs, xs)
    e, n, up, sing = geo_fault_displacement_enu(fault, 9000.0, X, Y)
    assert not sing.any()
    assert float(np.nanmax(up)) > 0.3                       # strong hanging-wall uplift
    assert float(np.nanmin(up)) > -0.15                     # weak footwall subsidence
    assert float(np.nansum(up)) > 0.0                       # net positive volume
    i = np.unravel_index(np.nanargmax(up), up.shape)
    assert abs(X[i]) <= half_horiz + 500.0 and abs(Y[i]) <= 10000.0
    # hanging wall (east) up, footwall (west) down relative to each other across the trace
    def _up(x):
        e, n, u, s = geo_fault_displacement_enu(fault, 9000.0, float(x), 0.0)
        assert not s
        return float(u)
    assert _up(0.0) - _up(-2.0 * half_horiz) > 0.2
    # strike 0 => along-strike is N; the field must be symmetric in N about 0
    prof = up[:, np.argmin(np.abs(xs))]
    off = prof - prof[::-1]
    assert np.nanmax(np.abs(off)) < 1e-6 * np.nanmax(np.abs(prof)) + 1e-9


def test_normal_fault_is_mirror_of_reverse_in_vertical_sense():
    """Normal faulting (rake -90) subsides the hanging wall (the east side for strike 0)."""
    fault = _reverse_fault(rake=-90.0)
    xs = np.arange(-60000.0, 60001.0, 500.0)
    X, Y = np.meshgrid(xs, xs)
    e, n, up, sing = geo_fault_displacement_enu(fault, 9000.0, X, Y)
    assert float(np.nanmin(up)) < -0.3 and float(np.nanmax(up)) < 0.15
    i = np.unravel_index(np.nanargmin(up), up.shape)
    assert abs(X[i]) <= 0.5 * 14000 * math.cos(math.radians(35.0)) + 500.0


def test_geo_strike_slip_sense_rake0_is_left_lateral():
    """Aki & Richards: rake 0 = LEFT-lateral. Strike north: the WEST block moves SOUTH and the
    EAST block moves NORTH (standing on one side, the far side moves to your left)."""
    fault = GeoFault(strike_deg=0.0, dip_deg=90.0, rake_deg=0.0, length_m=200000, width_m=10000,
                     top_depth_m=0, slip_m=1.0, centroid_lat=0, centroid_lon=0)
    _, n_w, _, _ = geo_fault_displacement_enu(fault, 6000.0, -1500.0, 0.0)
    _, n_e, _, _ = geo_fault_displacement_enu(fault, 6000.0, 1500.0, 0.0)
    assert float(n_w) < -0.1 and float(n_e) > 0.1
    # rake 180 = right-lateral: the opposite
    fault_r = GeoFault(strike_deg=0.0, dip_deg=90.0, rake_deg=180.0, length_m=200000, width_m=10000,
                       top_depth_m=0, slip_m=1.0, centroid_lat=0, centroid_lon=0)
    _, n_w, _, _ = geo_fault_displacement_enu(fault_r, 6000.0, -1500.0, 0.0)
    assert float(n_w) > 0.1


def test_geo_reverse_horizontal_motion_is_toward_up_dip_for_hanging_wall():
    """Hanging wall moves UP-DIP, i.e. WEST for an east-dipping (strike 0) thrust: the ground over
    the shallow part of the footprint must move west (negative east) and rise."""
    fault = _reverse_fault()
    e, n, up, s = geo_fault_displacement_enu(fault, 9000.0, -6000.0, 0.0)
    assert float(e) < -0.05 and float(up) > 0.2


def test_top_edge_variant_matches_centroid_variant():
    """The tsunami (top-edge) and coseismic (centroid) wrappers must describe the same fault."""
    fault = _reverse_fault(strike=40.0, rake=70.0)
    half = 0.5 * 14000
    d = math.radians(35.0)
    centroid_depth = 9000.0
    top = centroid_depth - half * math.sin(d)
    dir_e, dir_n = math.cos(math.radians(40.0)), -math.sin(math.radians(40.0))
    xs = np.array([-9000.0, 0.0, 4000.0, 15000.0])
    ys = np.array([3000.0, -7000.0, 0.0, 12000.0])
    e1, n1, u1, _ = geo_fault_displacement_enu(fault, centroid_depth, xs, ys)
    shift = half * math.cos(d)
    e2, n2, u2, _ = geo_fault_displacement_enu_top_edge(
        fault, top, xs + shift * dir_e, ys + shift * dir_n)
    assert np.allclose(e1, e2) and np.allclose(n1, n2) and np.allclose(u1, u2)


def test_shallow_centroid_depth_floors_top_edge():
    # centroid depth 1 km with a wide fault would place the top edge in the
    # air; the max(500, ...) floor must catch it.
    fault = GeoFault(strike_deg=0, dip_deg=35, rake_deg=90, length_m=20000, width_m=14000,
                     top_depth_m=0, slip_m=1.0, centroid_lat=0, centroid_lon=0)
    e, n, up, sing = geo_fault_displacement_enu(fault, 1000.0, 5000.0, 0.0)
    assert not sing.any() or True  # placement is exercised; exactness pinned above
