"""Regression tests for "the SAR overlay is empty and says 0 % no-data" (pixelOffsets/HH/alongTrackOffset, Nov 2026).

Two independent defects produced it, and neither was covered:

1. engine/api/routes_background.py reused `lo`, `hi` as loop variables, overwriting the route's colour-range
   query parameters with the last latitude bound. Every preview was therefore drawn on a forced -90 .. 90 range
   (range_fixed=True), whatever the data and whatever the caller asked for.
2. The retrieval client pre-filled never-written chunks with h5py's default fill (0.0) instead of the dataset's
   declared no-data (NaN), so empty swath showed as real zero values and the legend said "no-data 0 %".
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import h5py
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.test_background_api import (  # noqa: E402,F401  (fixtures + helpers reused as-is)
    GCOV_A, NORTH, SOUTH, EAST, WEST, _auth, client, data_dir)
from engine.api import background_img as bimg  # noqa: E402
from engine.retrieval.clients.h5_index import declared_fill  # noqa: E402

LAYER = "science__LSAR__GCOV__grids__frequencyA__HHHH"
BASE = (f"/api/background/preview?id={GCOV_A}&layer={LAYER}"
        f"&west={WEST}&south={SOUTH}&east={EAST}&north={NORTH}&max_px=32&scale=linear")


def _meta(c, extra: str = "") -> dict:
    r = c.get(BASE + extra, headers=_auth())
    assert r.status_code == 200, r.text
    return json.loads(r.headers["X-Img-Meta"])


# --------------------------------------------------------------------------- 1. the forced -90..90 range
def test_default_range_is_stretched_from_the_data_not_the_latitude_bounds(client):
    c, _t, _b = client
    m = _meta(c)
    assert m["range_fixed"] is False
    assert (m["lo"], m["hi"]) != (-90.0, 90.0)
    assert m["lo"] >= m["stats"]["min"] - 1e-9 and m["hi"] <= m["stats"]["max"] + 1e-9   # 2..98 percentile of the data


def test_explicit_range_reaches_the_renderer(client):
    c, _t, _b = client
    m = _meta(c, "&lo=0.2&hi=0.8")
    assert (m["lo"], m["hi"], m["range_fixed"]) == (0.2, 0.8, True)


@pytest.mark.parametrize("extra", ["&lo=0.2", "&hi=0.8", "&lo=0.9&hi=0.1", "&lo=0.5&hi=0.5", "&lo=nan&hi=1"])
def test_bad_ranges_are_refused_not_silently_replaced(client, extra):
    c, _t, _b = client
    r = c.get(BASE + extra, headers=_auth())
    assert r.status_code == 400, (extra, r.status_code, r.text[:200])


def test_a_different_range_is_a_different_cached_image(client):
    c, _t, _b = client
    a, b = _meta(c, "&lo=0.0&hi=1.0"), _meta(c, "&lo=0.05&hi=0.3")
    assert (a["lo"], a["hi"]) != (b["lo"], b["hi"])


def test_trace_array_route_validates_the_range_like_preview(client):
    c, _t, _b = client
    r = c.get("/api/background/runs/nope/arrays/nope?lo=3&hi=1", headers=_auth())
    assert r.status_code == 400


# --------------------------------------------------------------------------- 2. no-data is honest
def test_integer_fill_is_no_data_not_a_class():
    a = np.array([[1, 2], [255, 3]], np.uint8)
    v = bimg.finite_view(a, mask_int_fill=True)
    assert np.isnan(v[1, 0]) and v[0, 0] == 1
    s = bimg.stats(a, v)
    assert s["nan_fraction"] == 0.25 and s["max"] == 3.0
    # trace thumbnails keep their old behaviour (no fill convention applied)
    assert not np.isnan(bimg.finite_view(a)).any()


def test_signed_integer_fill_is_dtype_min():
    a = np.array([[-32768, 5]], np.int16)
    assert np.isnan(bimg.finite_view(a, mask_int_fill=True)[0, 0])


def test_modal_value_tells_a_flat_image_from_a_real_one():
    v = np.concatenate([np.zeros(70), np.arange(30, dtype=float) + 1])
    assert bimg.modal(v) == (0.0, 0.7)
    assert bimg.modal(np.array([])) == (None, 0.0)


def test_notes_say_why_an_image_looks_empty():
    from engine.api import background as bg
    data = np.linspace(0.0, 1.0, 1000, dtype=np.float32)
    miss = bg._render_notes(data, data, "viridis", -90.0, 90.0, True)
    assert any("flat" in n for n in miss), miss
    ok = bg._render_notes(data, data, "viridis", 0.0, 1.0, True)
    assert not ok, ok
    far = bg._render_notes(data, data, "viridis", 10.0, 20.0, False)
    assert any("does not overlap" in n for n in far), far
    dom = bg._render_notes(np.zeros(100, np.float32), np.zeros(100, np.float32), "viridis", -1.0, 1.0, False)
    assert any("share one value" in n for n in dom), dom


def test_preview_meta_carries_the_new_diagnostics(client):
    c, _t, _b = client
    m = _meta(c)
    assert "notes" in m and isinstance(m["notes"], list) and m["nodata_rule"]
    assert "modal_fraction" in m["stats"]


# --------------------------------------------------------------------------- 3. the declared fill value
@pytest.mark.parametrize("make,expect", [
    (dict(dtype="f4"), "nan"),                                   # nothing declared -> NISAR float convention
    (dict(dtype="f4", fillvalue=-9999.0), -9999.0),              # explicit creation-property fill
    (dict(dtype="f4", attr=-123.0), -123.0),                     # _FillValue attribute wins
    (dict(dtype="f4", fillvalue=-9999.0, attr=float("nan")), "nan"),   # ...even over the creation fill
    (dict(dtype="u1", attr=255), 255.0),
    (dict(dtype="u2", attr=65535), 65535.0),
    (dict(dtype="u1"), None),                                    # integers with no declaration: caller keeps 0
])
def test_declared_fill(tmp_path, make, expect):
    kw = dict(make)
    attr = kw.pop("attr", None)
    with h5py.File(tmp_path / "t.h5", "w") as f:
        d = f.create_dataset("d", shape=(4, 4), chunks=(2, 2), **kw)
        if attr is not None:
            d.attrs["_FillValue"] = np.asarray(attr, dtype=kw["dtype"])
        got = declared_fill(d)
    if expect == "nan":
        assert got is not None and np.isnan(got)
    else:
        assert got == expect


def test_never_written_chunks_come_back_as_nan_through_the_real_client(tmp_path):
    """End to end: HDF5 file on a range server -> NisarClient.fetch -> clip. Unwritten float chunks must be NaN."""
    from conftest import make_client
    from synth import EPSG, GRID, RangeServer, X0, Y0, DX, lonlat_box
    from engine.retrieval.clients.types import GranuleRef
    name = "NISAR_L2_PR_GUNW_030_091_D_072_031_2000_SH_20260912T123206_20260912T123240_20260924T123206_20260924T123240_P05023_N_F_J_001.h5"
    ny = nx = 512
    with h5py.File(tmp_path / name, "w") as f:
        top = f.create_group(f"{GRID}/pixelOffsets"); g = top.create_group("HH")
        top["xCoordinates"] = X0 + DX * np.arange(nx); top["yCoordinates"] = Y0 - DX * np.arange(ny)
        p = top.create_dataset("projection", data=np.uint32(EPSG)); p.attrs["epsg_code"] = EPSG
        d = g.create_dataset("alongTrackOffset", shape=(ny, nx), dtype="f4", chunks=(128, 128), compression="gzip")
        d.attrs["_FillValue"] = np.float32("nan")
        d[0:128, 0:128] = 7.0                                            # one chunk written, 15 never allocated
        m = g.create_dataset("mask", shape=(ny, nx), dtype="u1", chunks=(128, 128), compression="gzip")
        m.attrs["_FillValue"] = np.uint8(255)
        m[0:128, 0:128] = 1
    srv = RangeServer(tmp_path)
    cli = make_client(tmp_path)
    ref = GranuleRef(name[:-3], srv.url(name), "GUNW")
    res = cli.fetch(lonlat_box(0, 0, nx, ny), granules=[ref], layers=r"pixelOffsets/HH/(alongTrackOffset|mask)$")
    assert not res.errors and len(res.clips) == 1
    gc = next(iter(res.clips[0].grids.values()))
    off = gc.layers["science__LSAR__GUNW__grids__frequencyA__pixelOffsets__HH__alongTrackOffset"]
    msk = gc.layers["science__LSAR__GUNW__grids__frequencyA__pixelOffsets__HH__mask"]
    assert np.isnan(off).mean() > 0.9 and not (off == 0).any()          # was: 0.0 everywhere nothing was written
    assert float((off == 7.0).mean()) > 0.05
    assert (msk == 255).mean() > 0.9                                      # declared integer fill, not 0


# --------------------------------------------------------------------------- 4. units come from the file, not a guess
from engine.retrieval.clients.h5_index import declared_units  # noqa: E402


@pytest.mark.parametrize("raw,expect", [
    ("meters", "meters"), (b"radians", "radians"), (np.array([b"1"]), "1"), ("  ", None), (None, None)])
def test_declared_units(tmp_path, raw, expect):
    with h5py.File(tmp_path / "u.h5", "w") as f:
        d = f.create_dataset("d", data=np.zeros(3, "f4"))
        if raw is not None:
            d.attrs["units"] = raw
        assert declared_units(d) == expect


def test_units_survive_the_clip_cache_and_reach_the_preview_meta(tmp_path):
    from conftest import make_client
    from synth import EPSG, GRID, RangeServer, X0, Y0, DX, lonlat_box
    from engine.retrieval.clients.types import GranuleRef
    from engine.api import background as bg
    name = "NISAR_L2_PR_GUNW_030_091_D_072_031_2000_SH_20260912T123206_20260912T123240_20260924T123206_20260924T123240_P05023_N_F_J_001"
    ny = nx = 256
    with h5py.File(tmp_path / f"{name}.h5", "w") as f:
        top = f.create_group(f"{GRID}/pixelOffsets"); g = top.create_group("HH")
        top["xCoordinates"] = X0 + DX * np.arange(nx); top["yCoordinates"] = Y0 - DX * np.arange(ny)
        p = top.create_dataset("projection", data=np.uint32(EPSG)); p.attrs["epsg_code"] = EPSG
        d = g.create_dataset("alongTrackOffset", data=np.random.default_rng(0).normal(0, 3, (ny, nx)).astype("f4"),
                             chunks=(64, 64), compression="gzip")
        d.attrs["units"] = "meters"
        s = g.create_dataset("slantRangeOffset", data=np.ones((ny, nx), "f4"), chunks=(64, 64))   # no units stated
    srv = RangeServer(tmp_path)
    ref = GranuleRef(name, srv.url(f"{name}.h5"), "GUNW")
    bbox = lonlat_box(0, 0, nx, ny)
    key = "science__LSAR__GUNW__grids__frequencyA__pixelOffsets__HH__alongTrackOffset"
    key2 = key.replace("alongTrackOffset", "slantRangeOffset")

    cli = make_client(tmp_path)
    r1 = cli.fetch(bbox, granules=[ref], layers=r"pixelOffsets/HH/(alongTrackOffset|slantRangeOffset)$")
    g1 = next(iter(r1.clips[0].grids.values()))
    assert g1.units == {key: "meters"}                      # stated -> carried; unstated -> absent, never invented
    cli.close()

    cli2 = make_client(tmp_path)                            # fresh client: memory is empty, the on-disk clip cache is used
    r2 = cli2.fetch(bbox, granules=[ref], layers=r"pixelOffsets/HH/(alongTrackOffset|slantRangeOffset)$")
    assert r2.clips[0].source != "network", r2.clips[0].source
    g2 = next(iter(r2.clips[0].grids.values()))
    assert g2.units == {key: "meters"}

    class Src:
        client = cli2
        def fetch(self, bbox, t0, t1, product, layers, granules):
            return cli2.fetch(bbox, t0, t1, product, layers, granules=granules)
        def find_by_id(self, gid): return ref
        def search(self, *a, **k): return [ref], False
        last_search_truncated = False
    m = bg.preview(Src(), gid=name, layer=key, bbox=bbox, max_px=64, use_cache=False)["meta"]
    assert m["unit"] == "meters"
    assert bg.preview(Src(), gid=name, layer=key2, bbox=bbox, max_px=64, use_cache=False)["meta"]["unit"] is None
    cli2.close()


# --------------------------------------------------------------------------- 5. coherence validation (fade, not hide)
def _decode_alpha(png_bytes: bytes) -> np.ndarray:
    import io
    from PIL import Image
    return np.asarray(Image.open(io.BytesIO(png_bytes)).convert("RGBA"))[..., 3]


@pytest.fixture()
def offsets_over_coherence(tmp_path):
    """A GUNW whose pixelOffsets grid is 4x coarser than the interferogram grid. Left half: good coherence and
    a gentle real signal; right half: coherence 0.05 and offsets saturated at the +-90 search-window limit."""
    from conftest import make_client
    from synth import EPSG, GRID, RangeServer, X0, Y0, DX, lonlat_box
    from engine.retrieval.clients.types import GranuleRef
    name = "NISAR_L2_PR_GUNW_030_091_D_072_031_2000_SH_20260912T123206_20260912T123240_20260924T123206_20260924T123240_P05023_N_F_J_001"
    n_f, n_c = 256, 64
    rng = np.random.default_rng(5)
    coh = np.where(np.arange(n_f)[None, :] < n_f // 2, 0.9, 0.05) * np.ones((n_f, 1))
    off = np.where(np.arange(n_c)[None, :] < n_c // 2, rng.normal(0, 1.0, (n_c, n_c)),
                   rng.choice([-90.0, 90.0], (n_c, n_c)))
    with h5py.File(tmp_path / f"{name}.h5", "w") as f:
        def grid(grp, n, step, shift):
            g = f.create_group(f"{GRID}/{grp}")
            g["xCoordinates"] = X0 + DX * (step * np.arange(n) + shift)
            g["yCoordinates"] = Y0 - DX * (step * np.arange(n) + shift)
            p = g.create_dataset("projection", data=np.uint32(EPSG)); p.attrs["epsg_code"] = EPSG
            return g
        gu = grid("unwrappedInterferogram", n_f, 1, 0.0)
        gu.create_group("HH").create_dataset("coherenceMagnitude", data=coh.astype("f4"), chunks=(64, 64))
        go = grid("pixelOffsets", n_c, 4, 1.5)            # coarse pixel k covers fine pixels 4k..4k+3
        go.create_group("HH").create_dataset("alongTrackOffset", data=off.astype("f4"), chunks=(32, 32))
    srv = RangeServer(tmp_path)
    ref = GranuleRef(name, srv.url(f"{name}.h5"), "GUNW")
    cli = make_client(tmp_path)

    class Src:
        client = cli
        def fetch(self, bbox, t0, t1, product, layers, granules):
            return cli.fetch(bbox, t0, t1, product, layers, granules=granules)
        def find_by_id(self, gid): return ref
        def search(self, *a, **k): return [ref], False
        last_search_truncated = False
    yield Src(), name, lonlat_box(0, 0, n_f, n_f)
    cli.close()


KEY_OFF = "science__LSAR__GUNW__grids__frequencyA__pixelOffsets__HH__alongTrackOffset"


def test_coherence_validation_fades_low_coherence_pixels_on_another_grid(offsets_over_coherence):
    from engine.api import background as bg
    src, name, bbox = offsets_over_coherence
    off = bg.preview(src, gid=name, layer=KEY_OFF, bbox=bbox, max_px=64, use_cache=False)
    on = bg.preview(src, gid=name, layer=KEY_OFF, bbox=bbox, max_px=64, use_cache=False, quality="coherence")

    # off: every valid pixel opaque, and the junk dictates the colour stretch (the original complaint)
    a_off = _decode_alpha(off["png"])
    assert (a_off == 255).all()
    assert off["meta"]["quality"] == {"mode": "off", "applied": False}
    assert off["meta"]["hi"] > 50

    # on: coherence found on the OTHER grid, resampled, and used for opacity
    q = on["meta"]["quality"]
    assert q["applied"] is True and q["same_grid"] is False
    assert q["layer"].endswith("unwrappedInterferogram__HH__coherenceMagnitude")
    assert abs(q["hidden_fraction"] - 0.5) < 0.05 and q["unvalidated_fraction"] == 0.0
    a = _decode_alpha(on["png"])
    left, right = a[:, : a.shape[1] // 2 - 2], a[:, a.shape[1] // 2 + 2:]       # skip the boundary columns
    assert (left == 255).all()                                                    # coherence 0.9 >= hi: opaque
    assert (right == round(0.05 * 255)).all()                                     # coherence 0.05 <= lo: min_alpha
    # the colour stretch now comes from the trustworthy half: a few metres, not +-90
    assert on["meta"]["hi"] < 10 and on["meta"]["lo"] > -10
    assert on["meta"]["range_fixed"] is False and "above the fade midpoint" in q["range_from"]
    # the pixel VALUES are untouched: same data statistics, only opacity differs
    assert on["meta"]["stats"] == off["meta"]["stats"]


def test_explicit_range_still_wins_with_validation_on(offsets_over_coherence):
    from engine.api import background as bg
    src, name, bbox = offsets_over_coherence
    m = bg.preview(src, gid=name, layer=KEY_OFF, bbox=bbox, max_px=64, lo=-5, hi=5, use_cache=False,
                   quality="coherence")["meta"]
    assert (m["lo"], m["hi"], m["range_fixed"]) == (-5.0, 5.0, True) and m["quality"]["applied"]


def test_quality_on_and_off_are_cached_separately(offsets_over_coherence):
    from engine.api import background as bg
    src, name, bbox = offsets_over_coherence
    a = bg.preview(src, gid=name, layer=KEY_OFF, bbox=bbox, max_px=64, quality="coherence")
    b = bg.preview(src, gid=name, layer=KEY_OFF, bbox=bbox, max_px=64)
    c = bg.preview(src, gid=name, layer=KEY_OFF, bbox=bbox, max_px=64, quality="coherence")
    assert c["meta"].get("cache_hit") is True and c["meta"]["quality"]["applied"] is True
    assert b["meta"]["quality"]["mode"] == "off" and a["png"] != b["png"]


def test_validation_that_cannot_be_applied_says_why_and_draws_unfaded(offsets_over_coherence):
    from engine.api import background as bg
    src, name, bbox = offsets_over_coherence
    bad = KEY_OFF.replace("HH", "VV")                      # no such polarization in this file -> clip has no layer
    # a layer with no coherence counterpart (GCOV-style key): no candidates, explicit reason
    assert bg.coherence_candidates("science__LSAR__GCOV__grids__frequencyA__HHHH") == []
    assert bg.coherence_candidates("science__LSAR__GUNW__metadata__radarGrid__incidenceAngle") == []
    c = bg.coherence_candidates(KEY_OFF)
    assert c[0].endswith("unwrappedInterferogram__HH__coherenceMagnitude") and len(c) == 2
    c = bg.coherence_candidates(KEY_OFF.replace("pixelOffsets__HH__alongTrackOffset", "wrappedInterferogram__HH__x"))
    assert c[0].endswith("wrappedInterferogram__HH__coherenceMagnitude")           # own group first
    assert len(bg.coherence_candidates("science__LSAR__GUNW__grids__frequencyA__pixelOffsets__mask")) == 12


def test_quality_param_is_validated(client):
    c, _t, _b = client
    assert c.get(BASE + "&quality=bogus", headers=_auth()).status_code == 400
    m = _meta(c, "&quality=coherence")                      # GCOV fixture: nothing to validate against
    assert m["quality"]["applied"] is False and "GUNW" in m["quality"]["reason"]
    assert any("not applied" in n for n in m["notes"])
    assert m["nan_fraction"] == _meta(c)["nan_fraction"]    # drawn unfaded: the image is the same as with it off


def test_quality_alpha_and_config_validation(tmp_path):
    a = bimg.quality_alpha(np.array([0.0, 0.2, 0.4, 0.6, 1.0, np.nan]), 0.2, 0.6, 0.05)
    assert np.allclose(a, [0.05, 0.05, 0.525, 1.0, 1.0, 0.05])
    from engine.api.preview_cfg import PreviewConfigError, load_preview_config
    base = (ROOT / "config" / "preview.yaml").read_text()
    assert load_preview_config().quality["coherence"] == {"lo": 0.2, "hi": 0.6, "min_alpha": 0.05}
    for bad in ("lo: 0.7", "hi: 1.5", "min_alpha: 2", "lo: high"):
        key = bad.split(":")[0]
        text = "\n".join(f"    {bad}" if l.strip().startswith(key + ":") else l for l in base.splitlines())
        f = tmp_path / "preview.yaml"; f.write_text(text)
        with pytest.raises(PreviewConfigError):
            load_preview_config(env={"NISAR_PREVIEW_CONFIG": str(f)})
    legacy = tmp_path / "legacy.yaml"                       # a config written before the option existed still loads
    legacy.write_text(base.split("# \"validate with coherence\"")[0])
    assert load_preview_config(env={"NISAR_PREVIEW_CONFIG": str(legacy)}).quality["coherence"]["lo"] == 0.2


def test_resample_mean_handles_north_up_grids_and_gaps():
    sx = 10.0 + np.arange(8.0); sy = 80.0 - np.arange(8.0)                    # y descending, like NISAR
    src = np.arange(64.0).reshape(8, 8); src[0, 0] = np.nan
    tx = 10.5 + 2.0 * np.arange(6)                                            # 10.5 .. 20.5, runs past the source
    out = bimg.resample_mean(src, sx, sy, tx, np.array([79.5, 77.5]))
    assert out.shape == (2, 6)
    assert out[0, 0] == pytest.approx(np.nanmean(src[0:2, 0:2]))              # NaN ignored, not poisoning the mean
    assert out[1, 1] == pytest.approx(src[2:4, 2:4].mean())
    assert out[1, 3] == pytest.approx(src[2:4, 6:8].mean())                   # last pixel still inside the source
    assert np.isnan(out[:, 4:]).all()                                         # outside the source extent
    with pytest.raises(ValueError, match="regularly spaced"):
        bimg.resample_mean(src, sx, sy, np.array([10.5, 12.5, 99.0]), np.array([79.5, 77.5]))
