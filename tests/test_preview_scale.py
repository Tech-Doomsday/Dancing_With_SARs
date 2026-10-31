"""Preview scale / colour map / corner geometry (engine-computed; the UI only displays).
Helper-level tests need no FastAPI; the bg.preview() ones use the same FakeSource as test_background_api."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from engine.api import background_img as bimg
from engine.api.preview_cfg import PreviewConfigError, load_preview_config
from engine.common.grids import axis_edges, edge_corners_lonlat

ROOT = Path(__file__).resolve().parents[1]


# ------------------------------------------------------------------ corners_lonlat
def _utm_axes(n=40, dx=20.0, x0=500_000.0 + 150_000.0, y0=3_770_000.0):
    x = x0 + dx * np.arange(n)                # ascending eastings (pixel centres)
    y = y0 - dx * np.arange(n)                # descending northings: row 0 is north
    return x, y


def test_corners_order_half_pixel_and_not_axis_aligned():
    x, y = _utm_axes()
    corners, note = edge_corners_lonlat(x, y, 32611)
    assert note is None and len(corners) == 4
    (tl, tr, br, bl) = corners
    assert tl[1] > bl[1] and tr[1] > br[1]            # row 0 is north: top corners are the northern ones
    assert tl[0] < tr[0] and bl[0] < br[0]            # west before east
    assert tl[0] != bl[0]                             # UTM is rotated against the graticule: not axis-aligned
    # half a pixel beyond the outer centres, exactly: project the corners back to UTM
    from engine.common._geo import transform_xy
    e, n = transform_xy(np.array([c[0] for c in corners]), np.array([c[1] for c in corners]), "EPSG:4326", "EPSG:32611")
    assert np.allclose(e, [x[0] - 10, x[-1] + 10, x[-1] + 10, x[0] - 10], atol=0.01)
    assert np.allclose(n, [y[0] + 10, y[0] + 10, y[-1] - 10, y[-1] - 10], atol=0.01)


def test_axis_edges_half_pixel_both_directions():
    assert list(axis_edges(np.array([10.0, 20.0, 30.0]))) == [5.0, 15.0, 25.0, 35.0]
    assert list(axis_edges(np.array([30.0, 20.0, 10.0]))) == [35.0, 25.0, 15.0, 5.0]


def test_corners_refuse_instead_of_guessing():
    c, note = edge_corners_lonlat(np.array([500000.0]), np.array([3.0e6]), 32611)
    assert c is None and "no corners" in note                       # one pixel: size unknown
    c, note = edge_corners_lonlat(*_utm_axes(), 99999)              # unsupported CRS
    assert c is None and "no corners" in note


# ------------------------------------------------------------------ scale
def test_to_db_is_10log10_and_nonpositive_or_nonfinite_become_nan():
    a = np.array([[1.0, 10.0, 0.1], [0.0, -2.0, np.nan], [np.inf, 100.0, 1e-3]])
    out = bimg.to_db(a)
    assert np.allclose(out[0], [0.0, 10.0, -10.0]) and out[2, 1] == pytest.approx(20.0) and out[2, 2] == pytest.approx(-30.0)
    assert np.isnan(out[1]).all() and np.isnan(out[2, 0])
    png = bimg.render(out, "gray", -30.0, 20.0)                     # NaN -> alpha 0
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


# ------------------------------------------------------------------ config
def test_cmap_and_scale_auto_resolve_by_kind():
    cfg = load_preview_config()
    assert cfg.resolve_scale("auto", "power") == "db" and cfg.resolve_scale("auto", "phase") == "linear"
    assert cfg.resolve_scale("linear", "power") == "linear" and cfg.resolve_scale("db", "phase") == "db"
    assert cfg.resolve_cmap("auto", "power") == "gray"
    assert cfg.resolve_cmap("auto", "phase") == "diverging"
    assert cfg.resolve_cmap("auto", "coherence") == "viridis"
    assert cfg.resolve_cmap("auto", "cc") == "categorical"
    assert cfg.resolve_cmap("auto", None) == cfg.default_cmap       # unknown role: default, never a guess
    assert cfg.resolve_cmap("magma", "power") == "magma"            # an explicit choice wins
    assert cfg.unit("db") == "dB" and cfg.unit("linear") is None    # unknown unit stays null


def test_config_dir_env_and_clear_errors(tmp_path):
    (tmp_path / "preview.yaml").write_text("render_version: 7\nscale_by_kind: {power: db}\n")
    assert load_preview_config(env={"NISAR_CONFIG_DIR": str(tmp_path)}).render_version == 7
    with pytest.raises(PreviewConfigError, match="not found"):
        load_preview_config(env={"NISAR_CONFIG_DIR": str(tmp_path / "nope")})
    bad = tmp_path / "bad.yaml"
    bad.write_text("render_version: [1\n")
    with pytest.raises(PreviewConfigError, match="could not be read"):
        load_preview_config(env={"NISAR_PREVIEW_CONFIG": str(bad)})
    bad.write_text("render_version: x\n")
    with pytest.raises(PreviewConfigError, match="invalid"):
        load_preview_config(env={"NISAR_PREVIEW_CONFIG": str(bad)})
    bad.write_text("render_version: 1\nscale_by_kind: {power: cube}\n")
    with pytest.raises(PreviewConfigError, match="db\\|linear"):
        load_preview_config(env={"NISAR_PREVIEW_CONFIG": str(bad)})


# ------------------------------------------------------------------ bg.preview() end to end (fake source)
LAYER = "science__LSAR__GCOV__grids__frequencyA__HHHH"


@pytest.fixture()
def fake(tmp_path, monkeypatch):
    """FakeSource.search (ids/refs) + a stub fetch(): a 40x40 UTM clip with positive power values, a zero
    and a NaN — so the dB rules are exercised without the tiling stack (pyproj)."""
    monkeypatch.setenv("NISAR_DATA_DIR", str(tmp_path / "data"))
    from types import SimpleNamespace
    from tests.test_background_api import FakeSource, default_specs, GCOV_A, WEST, SOUTH, EAST, NORTH

    class StubSource(FakeSource):
        def fetch(self, bbox, t0, t1, product, layers, granules):
            x, y = _utm_axes()
            arr = np.linspace(0.01, 1.0, 1600).reshape(40, 40)
            arr[0, 0], arr[0, 1] = 0.0, np.nan
            grid = SimpleNamespace(epsg=32611, x=x, y=y, layers={LAYER: arr})
            return SimpleNamespace(errors={}, skipped={}, stats={}, clips=[SimpleNamespace(grids={"g": grid}, source="stub")])

    return StubSource(default_specs()), GCOV_A, (WEST, SOUTH, EAST, NORTH)


def _preview(fake, **kw):
    from engine.api import background as bg
    src, gid, bbox = fake
    return bg.preview(src, gid=gid, layer=LAYER, bbox=bbox, max_px=64, **kw)


def test_auto_on_gcov_power_is_db_gray_with_unit_and_corners(fake):
    m = _preview(fake, use_cache=False)["meta"]
    assert (m["scale"], m["unit"], m["cmap"], m["cmap_requested"], m["scale_requested"]) == ("db", "dB", "gray", "auto", "auto")
    assert m["lo"] < m["hi"] and m["lo"] < 10                       # dB-sized numbers, not raw power
    assert len(m["corners_lonlat"]) == 4 and m["corners_note"] is None
    assert m["nan_fraction"] == pytest.approx(2 / 1600)             # the zero (no dB value) and the NaN
    assert -20 < m["lo"] < -10 and -0.5 < m["hi"] <= 0.0          # the 2nd/98th percentile of 10*log10(0.01..1)
    assert len(json.dumps(m)) < 4096                                # rides in the X-Img-Meta header


def test_scale_linear_overrides_auto(fake):
    m = _preview(fake, scale="linear", use_cache=False)["meta"]
    assert m["scale"] == "linear" and m["unit"] is None


def test_invalid_scale_or_cmap_is_400_with_allowed_values(fake):
    from engine.api import background as bg
    for kw in ({"scale": "log"}, {"cmap": "jet"}):
        with pytest.raises(bg.BackgroundError) as ei:
            _preview(fake, use_cache=False, **kw)
        assert ei.value.status == 400 and "auto" in str(ei.value)


def test_two_scales_two_cache_files_and_render_version_invalidates(fake):
    from engine.api import background as bg
    from engine.api.preview_cfg import load_preview_config
    base = load_preview_config()
    _preview(fake, scale="db"); _preview(fake, scale="linear")
    src, gid, bbox = fake
    p_db, _ = bg._cache_paths(gid, LAYER, bbox, 64, "auto", "db", base.render_version)
    p_lin, _ = bg._cache_paths(gid, LAYER, bbox, 64, "auto", "linear", base.render_version)
    assert p_db != p_lin and p_db.exists() and p_lin.exists()
    assert _preview(fake, scale="db")["meta"]["cache_hit"] is True
    bumped = type(base)(base.render_version + 1, base.scale_by_kind, base.cmap_by_kind, base.default_cmap, base.unit_by_scale)
    m = _preview(fake, scale="db", cfg=bumped)["meta"]
    assert m["cache_hit"] is False and m["render_version"] == base.render_version + 1
    p_new, _ = bg._cache_paths(gid, LAYER, bbox, 64, "auto", "db", bumped.render_version)
    assert p_new != p_db and p_new.exists()
