"""engine/common building blocks: config, frozen containers, layer catalogue, canonical grids, resampling, terrain maths."""
import pickle
import re

import numpy as np
import pytest

from common_fixtures import default_tile
from engine.common import _terrain as T
from engine.common.config import CommonConfig, load_common_config
from engine.common.frozen import Dual, FrozenDict, freeze, freeze_array
from engine.common.grids import make_tile_grids, mosaic_first_valid, resample
from engine.common.layers import parse_layer_key, request_layers, wanted_products
from engine.common.config import GridsCfg


# ------------------------------------------------------------------------------------ config
def test_config_defaults_yaml_and_env_precedence(tmp_path):
    y = tmp_path / "c.yaml"
    y.write_text("radar:\n  wavelength_m: 0.25\ntemporal:\n  min_baseline_epochs: 5\n")
    cfg = load_common_config(y, env={"NISAR_COMMON_TEMPORAL_MIN_BASELINE_EPOCHS": "7"})
    assert cfg.radar.wavelength_m == 0.25            # yaml beats default
    assert cfg.temporal.min_baseline_epochs == 7     # env beats yaml
    assert cfg.radar.phase_sign == -1                # default kept
    assert load_common_config(tmp_path / "missing.yaml", env={}).grids.g80_m == 80.0


def test_config_rejects_unknown_keys(tmp_path):
    y = tmp_path / "c.yaml"
    y.write_text("radar:\n  wavelenght_m: 0.25\n")
    with pytest.raises(ValueError, match="unknown key"):
        load_common_config(y, env={})
    y.write_text("nonsense: {}\n")
    with pytest.raises(ValueError, match="unknown section"):
        load_common_config(y, env={})


def test_chain_params_exclude_registry_and_shipped_yaml_loads():
    cfg = load_common_config(env={})
    assert "registry" not in cfg.chain_params()
    assert CommonConfig().chain_params_json() == CommonConfig().chain_params_json()


# ------------------------------------------------------------------------------------ frozen
def test_frozen_dict_is_immutable_hashable_free_and_picklable():
    d = FrozenDict({"a": np.arange(3), "b": FrozenDict({"c": 1})})
    with pytest.raises(TypeError):
        d["x"] = 1
    with pytest.raises(AttributeError):
        d.foo = 1
    assert not d["a"].flags.writeable
    d2 = pickle.loads(pickle.dumps(d))
    assert list(d2) == ["a", "b"] and np.array_equal(d2["a"], d["a"])


def test_freeze_array_copies_writeable_but_not_readonly_views(tmp_path):
    a = np.arange(6.0)
    f = freeze_array(a)
    assert not f.flags.writeable and a.flags.writeable and f is not a
    a[0] = 99
    assert f[0] == 0.0                                  # the frozen copy cannot be changed through the original
    np.save(tmp_path / "x.npy", a)
    m = np.load(tmp_path / "x.npy", mmap_mode="r")
    assert freeze_array(m) is m or np.shares_memory(freeze_array(m), m)     # read-only memmap stays on disk
    with pytest.raises(ValueError):
        freeze_array(a).setflags(write=True)
    nested = freeze({"k": [np.zeros(2), (np.ones(2),)]})
    assert isinstance(nested, FrozenDict) and isinstance(nested["k"], tuple) and not nested["k"][0].flags.writeable


def test_dual_lookup():
    d = Dual(np.zeros((2, 2)), None)
    assert d.available and d["G80"] is d.g80 and d["g20"] is None
    assert not Dual().available


# ------------------------------------------------------------------------------------ layers
GUNW = "science__LSAR__GUNW__grids__frequencyA__unwrappedInterferogram__HH__"
GCOV = "science__LSAR__GCOV__grids__frequencyA__"
RG = "science__LSAR__GUNW__metadata__radarGrid__"


@pytest.mark.parametrize("key,role,pol", [
    (GUNW + "unwrappedPhase", "unw_phase", "HH"),
    (GUNW + "coherenceMagnitude", "unw_coherence", "HH"),
    (GUNW + "connectedComponents", "conn_comp", "HH"),
    (GUNW + "ionospherePhaseScreenUncertainty", "iono_sigma", "HH"),
    ("science__LSAR__GUNW__grids__frequencyA__pixelOffsets__HH__slantRangeOffset", "off_range", "HH"),
    (GCOV + "HHHH", "cov", "HH"), (GCOV + "HVHV", "cov", "HV"), (GCOV + "mask", "cov_mask", None),
    (GCOV + "numberOfLooks", "n_looks", None), (GCOV + "rtcGammaToSigmaFactor", "rtc_g2s", None),
    (RG + "incidenceAngle", "incidence", None), (RG + "losUnitVectorX", "los_x", None),
    (RG + "wetTroposphericPhaseScreen", "tropo_wet_phase", None),
])
def test_parse_layer_key_roles(key, role, pol):
    lid = parse_layer_key(key)
    assert (lid.role, lid.pol) == (role, pol)


def test_off_diagonal_covariance_and_unknown_layouts_have_no_role():
    assert parse_layer_key(GCOV + "HHHV").role is None          # off-diagonal terms are complex, not power
    assert parse_layer_key("garbage").role is None
    assert parse_layer_key("science__LSAR__GUNW__identification__trackNumber").role is None


def test_request_regex_selects_exactly_the_wanted_paths():
    rx = re.compile(request_layers("GUNW", {"M1"}))
    path = lambda k: "/" + k.replace("__", "/")                  # noqa: E731
    assert rx.search(path(GUNW + "unwrappedPhase")) and rx.search(path(RG + "losUnitVectorY"))
    assert rx.search(path(RG + "hydrostaticTroposphericPhaseScreen"))
    assert not rx.search(path("science__LSAR__GUNW__grids__frequencyA__pixelOffsets__HH__slantRangeOffset"))   # M3 not asked
    assert not rx.search(path(GUNW.replace("HH", "HV") + "unwrappedPhase"))                                       # cross-pol not asked
    assert not rx.search(path("science__LSAR__GUNW__grids__frequencyB__unwrappedInterferogram__HH__unwrappedPhase"))
    rx2 = re.compile(request_layers("GCOV", {"M2"}))
    assert rx2.search(path(GCOV + "HVHV")) and rx2.search(path(GCOV + "numberOfLooks"))
    assert not rx2.search(path(GCOV + "HHHV"))
    assert request_layers("GCOV", {"M1"}) is None
    assert wanted_products({"M2"}) == ["GCOV"] and wanted_products({"M3"}) == ["GUNW"]
    assert wanted_products(None) == ["GUNW", "GCOV"] and wanted_products({"M4"}) == []


# ------------------------------------------------------------------------------------ grids
def test_tile_grids_geometry():
    t = default_tile()
    g = make_tile_grids(t, GridsCfg())
    assert g.g80.shape == (127, 127) and g.g20.shape == (508, 508) and g.ratio == 4
    assert g.g80.core_slices() == (slice(1, 126), slice(1, 126)) and g.g20.core_slices() == (slice(4, 504), slice(4, 504))
    assert g.g80.bounds == (t.e0 - 80, t.n0 - 80, t.e0 + t.size + 80, t.n0 + t.size + 80)
    assert g.g80.x[0] == t.e0 - 80 + 40 and g.g80.y[0] > g.g80.y[-1]
    # every G20 pixel maps to the G80 pixel that contains it
    idx = g.index_map_20_to_80
    r, c = np.divmod(idx, g.g80.shape[1])
    assert np.all(np.abs(g.g80.x[c] - g.g20.x[None, :]) <= 40) and np.all(np.abs(g.g80.y[r] - g.g20.y[:, None]) <= 40)
    with pytest.raises(ValueError):
        make_tile_grids(t, GridsCfg(g80_m=75.0))


def test_aggregate_20_to_80_matches_block_mean():
    g = make_tile_grids(default_tile(), GridsCfg())
    a = np.random.default_rng(1).random(g.g20.shape).astype(np.float32)
    want = a.reshape(127, 4, 127, 4).mean(axis=(1, 3))
    assert np.allclose(g.aggregate_20_to_80(a, "mean"), want, atol=1e-6)
    b = a > 0.9
    assert np.array_equal(g.aggregate_20_to_80(b, "any"), b.reshape(127, 4, 127, 4).any(axis=(1, 3)))
    assert np.array_equal(g.aggregate_20_to_80(b, "all"), b.reshape(127, 4, 127, 4).all(axis=(1, 3)))
    a[:4, :4] = np.nan
    assert np.isnan(g.aggregate_20_to_80(a, "mean")[0, 0])


def _native(tile, dx, offset=0.0, margin=700.0):
    x0, x1 = tile.e0 - margin + offset, tile.e0 + tile.size + margin + offset
    y1, y0 = tile.n0 + tile.size + margin + offset, tile.n0 - margin + offset
    x = x0 + dx * (np.arange(int((x1 - x0) / dx)) + 0.5)
    y = y1 - dx * (np.arange(int((y1 - y0) / dx)) + 0.5)
    return x, y


def test_resample_aligned_native_grid_is_an_exact_copy():
    t = default_tile()
    g = make_tile_grids(t, GridsCfg())
    x, y = _native(t, 80.0)
    X, Y = np.meshgrid(x, y)
    f = (0.001 * (X - t.e0) + 0.002 * (Y - t.n0)).astype(np.float32)
    out, cov = resample(f, x, y, t.epsg, g.g80, "bilinear")
    ref = (0.001 * (g.g80.x[None, :] - t.e0) + 0.002 * (g.g80.y[:, None] - t.n0)).astype(np.float32)
    assert np.allclose(out, ref, atol=1e-4) and cov.all()


def test_resample_halfpixel_offset_linear_field_and_nan_awareness():
    t = default_tile()
    g = make_tile_grids(t, GridsCfg())
    x, y = _native(t, 80.0, offset=40.0)              # pixel centres half a pixel off the canonical grid
    X, Y = np.meshgrid(x, y)
    f = (0.001 * (X - t.e0) - 0.002 * (Y - t.n0)).astype(np.float32)
    f[100:104, 100:104] = np.nan                       # a hole must stay a hole (no smearing of NaN into neighbours)
    out, _ = resample(f, x, y, t.epsg, g.g80, "bilinear")
    ref = (0.001 * (g.g80.x[None, :] - t.e0) - 0.002 * (g.g80.y[:, None] - t.n0))
    ok = np.isfinite(out)
    err = np.abs(out - ref)
    rr, cc = np.mgrid[:127, :127]
    far = (np.abs(rr - 93.5) > 4) | (np.abs(cc - 93.5) > 4)
    assert np.nanmax(err[far & ok]) < 1e-3                 # exact for a linear field away from the hole
    assert np.nanmax(err[ok]) < 0.05                       # renormalised weights next to it: small, bounded bias
    assert ok.mean() > 0.95 and 4 <= (~ok).sum() <= 40     # the hole stays a hole instead of being smeared everywhere


def test_resample_20_to_80_area_is_block_mean_and_nearest_keeps_integers():
    t = default_tile()
    g = make_tile_grids(t, GridsCfg())
    x, y = _native(t, 20.0)
    f = np.random.default_rng(3).random((len(y), len(x))).astype(np.float32)
    out, _ = resample(f, x, y, t.epsg, g.g80, "area")
    i0 = int(round((g.g80.x[0] - 40 - (x[0] - 10)) / 20))     # canonical origin inside the native grid, in 20 m px
    j0 = int(round(((y[0] + 10) - (g.g80.y[0] + 40)) / 20))
    blk = f[j0:j0 + 4, i0:i0 + 4].mean()
    assert abs(out[0, 0] - blk) < 1e-5
    cc = (np.arange(len(y) * len(x)).reshape(len(y), len(x)) % 7).astype(np.uint16)
    o2, _ = resample(cc, x, y, t.epsg, g.g20, "nearest", fill=0)
    assert o2.dtype == np.uint16 and set(np.unique(o2)) <= set(range(7))


def test_resample_other_utm_zone_lands_on_same_ground():
    from pyproj import Transformer
    t = default_tile()
    g = make_tile_grids(t, GridsCfg())
    tr = Transformer.from_crs(t.epsg, t.epsg + 1, always_xy=True)
    back = Transformer.from_crs(t.epsg + 1, t.epsg, always_xy=True)
    xs, ys = tr.transform([t.e0 - 900, t.e0 + t.size + 900], [t.n0 - 900, t.n0 + t.size + 900])
    x = np.arange(xs[0] - 500, xs[1] + 500, 80.0) + 40
    y = np.arange(max(ys) + 500, min(ys) - 500, -80.0) - 40
    X, Y = np.meshgrid(x, y)
    ex, ny_ = back.transform(X, Y)
    f = (0.001 * (ex - t.e0) + 0.001 * (ny_ - t.n0)).astype(np.float32)           # field defined in tile coordinates
    out, cov = resample(f, x, y, t.epsg + 1, g.g80, "bilinear")
    ref = 0.001 * (g.g80.x[None, :] - t.e0) + 0.001 * (g.g80.y[:, None] - t.n0)
    ok = np.isfinite(out)
    assert ok.mean() > 0.95 and np.allclose(out[ok], ref[ok], atol=0.01)


def test_mosaic_first_valid_counts_and_orders():
    a = np.array([[1.0, np.nan], [np.nan, np.nan]], np.float32)
    b = np.array([[9.0, 2.0], [np.nan, np.nan]], np.float32)
    m = mosaic_first_valid([a, b])
    assert m[0, 0] == 1.0 and m[0, 1] == 2.0 and np.isnan(m[1]).all()
    ci = mosaic_first_valid([np.array([[0, 5]], np.uint16), np.array([[7, 8]], np.uint16)], fill=0)
    assert ci.tolist() == [[7, 5]]


# ------------------------------------------------------------------------------------ terrain maths
def test_slope_aspect_of_planar_and_flat_dem():
    n, dx = 40, 80.0
    x = np.arange(n) * dx
    X, Y = np.meshgrid(x, x[::-1])
    dem = (0.2 * X).astype(np.float64)                      # rises toward the east: slope atan(0.2), faces west
    slope, aspect, gx, gn = T.slope_aspect(dem, dx, dx, 0.1)
    assert np.allclose(slope[5:-5, 5:-5], np.degrees(np.arctan(0.2)), atol=1e-3)
    assert np.allclose(aspect[5:-5, 5:-5], 270.0, atol=1e-3)
    s0, a0, *_ = T.slope_aspect(np.full((n, n), 100.0), dx, dx, 0.1)
    assert np.allclose(s0, 0) and np.isnan(a0).all()        # aspect undefined on flat ground


def test_fill_voids_reports_fraction():
    d = np.arange(100.0).reshape(10, 10)
    d[4:6, 4:6] = np.nan
    f, frac = T.fill_voids(d)
    assert np.isfinite(f).all() and abs(frac - 0.04) < 1e-9


def test_local_geometry_layover_and_shadow_on_a_steep_face():
    n, dx = 30, 80.0
    x = np.arange(n) * dx
    X, _ = np.meshgrid(x, x)
    # sensor to the east (ground->sensor points +E), incidence 40 deg
    inc = np.full((n, n), 40.0)
    los = np.zeros((3, n, n))
    los[0], los[2] = np.sin(np.radians(40)), np.cos(np.radians(40))
    # terrain rising toward the sensor-side (east) faces AWAY from it: shadow; falling toward the sensor faces it: layover
    for slope_deg, expect, local in ((60.0, "shadow", None), (-60.0, "layover", None), (-5.0, "none", 35.0)):
        dem = np.tan(np.radians(slope_deg)) * X
        slope, aspect, gx, gn = T.slope_aspect(dem, dx, dx, 0.1)
        loc, lay, sha = T.local_geometry(gx, gn, los, inc, 0.0, 0.0)
        mid = (slice(5, -5), slice(5, -5))
        assert lay[mid].all() == (expect == "layover") and sha[mid].all() == (expect == "shadow")
        if expect == "none":
            assert not lay[mid].any() and not sha[mid].any() and np.allclose(loc[mid], local, atol=0.5)


def test_hand_is_height_above_the_nearest_drainage():
    n, dx = 60, 80.0
    x = (np.arange(n) - n // 2) * dx
    X, _ = np.meshgrid(x, x)
    row = np.arange(n)[:, None] * np.ones((1, n))
    dem = 0.1 * np.abs(X) + 0.05 * (n - 1 - row) * dx      # a V valley draining toward the last row
    h, _ = T.hand(dem, dx, dx, min_drainage_km2=5.0, extra_drainage=None, max_m=300.0)
    c = n // 2
    assert np.nanmax(h[n - 8:, c]) < 1.0                   # on the stream: ~0
    assert 5.0 < np.nanmax(h[n - 8:, c + 1]) < 20.0        # one pixel (80 m) up the bank: ~8 m
    prof = h[n - 8, c + 1:c + 20]
    assert np.all(np.diff(prof) > 0) and prof[-1] > 100    # rises with distance from it
