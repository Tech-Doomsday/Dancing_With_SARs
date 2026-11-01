"""Item A: overlay corners describe the DRAWN (decimated) image, not the full-resolution array.

Independent check: the far edge of the last drawn pixel is built from that pixel's SOURCE centre
(x[(m-1)*k]) plus half a drawn pixel (k*step/2) and projected with the engine's own UTM helper;
nothing here calls _strided_edges."""
from types import SimpleNamespace

import numpy as np

from engine.common import grids
from engine.common._geo import transform_xy

EPSG = 32643                                  # UTM 43N
X0, Y0, STEP = 500_000.0, 3_300_000.0, 30.0   # first-pixel centre; 30 m pixels; rows run north -> south


def _axes(nx=103, ny=87):
    return X0 + STEP * np.arange(nx), Y0 - STEP * np.arange(ny)


def _lonlat(xe, ye):
    lon, lat = transform_xy(np.array([xe]), np.array([ye]), f"EPSG:{EPSG}", "EPSG:4326")
    return [float(lon[0]), float(lat[0])]


def _expected_corners(x, y, k):
    """TL, TR, BR, BL from the last drawn pixel's own centre (independent of the engine's edge maths)."""
    xs, ys = x[::k], y[::k]
    west = xs[0] - k * STEP / 2
    east = xs[-1] + k * STEP / 2
    north = ys[0] + k * STEP / 2
    south = ys[-1] - k * STEP / 2
    return [_lonlat(west, north), _lonlat(east, north), _lonlat(east, south), _lonlat(west, south)]


def test_stride_1_is_the_full_resolution_result_unchanged():
    x, y = _axes()
    corners, note = grids.edge_corners_lonlat(x, y, EPSG)
    assert note is None
    ex, ey = grids.axis_edges(x), grids.axis_edges(y)
    old = grids.quad_corners_lonlat(min(ex[0], ex[-1]), max(ex[0], ex[-1]),
                                    min(ey[0], ey[-1]), max(ey[0], ey[-1]), EPSG)[0]
    assert corners == old                                  # bit-for-bit, not approximately
    assert grids.edge_corners_lonlat(x, y, EPSG, stride=1)[0] == old


def test_last_drawn_pixel_outer_edge_matches_independent_computation():
    x, y = _axes()
    for k in (1, 2, 5):
        got, note = grids.edge_corners_lonlat(x, y, EPSG, stride=k)
        assert note is None, (k, note)
        want = _expected_corners(x, y, k)
        for g, w in zip(got, want):
            assert abs(g[0] - w[0]) < 1e-9 and abs(g[1] - w[1]) < 1e-9, (k, g, w)


def test_drawn_pixel_count_matches_the_decimated_axes():
    from engine.api import background_img as bimg
    x, y = _axes()
    arr = np.random.default_rng(0).random((y.size, x.size)).astype(np.float32)
    shown, k = bimg.decimate(arr, 20)
    assert k > 1 and shown.shape == (y[::k].size, x[::k].size)


def test_far_edge_moves_with_stride_and_is_never_a_whole_stride_short():
    x, y = _axes(nx=101, ny=101)                  # 101 = 1 + 4*25 -> stride 5 lands exactly on the last pixel
    c1 = grids.edge_corners_lonlat(x, y, EPSG, stride=1)[0]
    c5 = grids.edge_corners_lonlat(x, y, EPSG, stride=5)[0]
    # old behaviour kept the stride-1 quad for every stride; the drawn quad is wider by 2*(5-1)/2 px per axis
    assert c5[1][0] > c1[1][0] and c5[0][0] < c1[0][0]


def test_extent_form_matches_axes_form_for_every_stride():
    x, y = _axes()
    extent = (x[0] - STEP / 2, x[-1] + STEP / 2, y[-1] - STEP / 2, y[0] + STEP / 2)
    for k in (1, 2, 5):
        a = grids.edge_corners_lonlat(x, y, EPSG, stride=k)[0]
        b, note = grids.extent_corners_lonlat(extent, EPSG, stride=k, shape=(y.size, x.size))
        assert note is None
        for p, q in zip(a, b):
            assert abs(p[0] - q[0]) < 1e-9 and abs(p[1] - q[1]) < 1e-9, (k, p, q)


def test_extent_stride_without_shape_is_refused_not_guessed():
    x, y = _axes()
    extent = (x[0] - STEP / 2, x[-1] + STEP / 2, y[-1] - STEP / 2, y[0] + STEP / 2)
    corners, note = grids.extent_corners_lonlat(extent, EPSG, stride=3)
    assert corners is None and note.startswith("no corners:")
    assert grids.extent_corners_lonlat(extent, EPSG)[0] is not None      # stride 1 needs no shape


def test_trace_png_uses_the_product_of_both_decimations():
    from engine.api import background as bg
    x, y = _axes(nx=240, ny=200)
    full = np.random.default_rng(1).random((y.size, x.size)).astype(np.float32) + 1.0
    st = 3
    rec = SimpleNamespace(thumb=full[::st, ::st], stride=st, shape=full.shape, dtype="float32", kind="float",
                          extent=(x[0] - STEP / 2, x[-1] + STEP / 2, y[-1] - STEP / 2, y[0] + STEP / 2),
                          epsg=EPSG, label="t", path="clips/t")
    _png, meta = bg.trace_array_png(rec, "viridis", None, None, 16)
    k2 = meta["shown_stride"]
    assert k2 > 1
    want = grids.edge_corners_lonlat(x, y, EPSG, stride=st * k2)[0]
    for p, q in zip(meta["corners_lonlat"], want):
        assert abs(p[0] - q[0]) < 1e-9 and abs(p[1] - q[1]) < 1e-9
