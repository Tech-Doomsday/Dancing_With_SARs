import ast
import pathlib
import re
import tempfile
import unittest
from datetime import datetime, timedelta

import numpy as np

from engine.common import (GranuleRecord, PreprocConfig, SceneBundle, build_scene_bundle, run_preprocessing)
from engine.common import _geo
from engine.common._raster import canonical_grids, resample
from engine.common._types import Grid
from engine.common import p01_granule_resolution as p01
from engine.common.tests import synthetic as syn

PKG = pathlib.Path(__file__).resolve().parents[1]


class GeoTests(unittest.TestCase):
    def test_utm_known_value(self):
        x, y = _geo.lonlat_to_utm(9.0, 45.0, 32, True)       # central meridian of zone 32
        self.assertAlmostEqual(float(x), 500000.0, delta=0.01)
        self.assertAlmostEqual(float(y), 4982950.4, delta=0.5)

    def test_roundtrip_and_cross_zone(self):
        lon, lat = np.array([90.3, 90.45, 89.9]), np.array([23.7, 23.85, 24.0])
        x, y = _geo.lonlat_to_utm(lon, lat, 46, True)
        lo, la = _geo.utm_to_lonlat(x, y, 46, True)
        self.assertLess(float(np.abs(lo - lon).max()), 1e-9)
        self.assertLess(float(np.abs(la - lat).max()), 1e-9)
        x2, y2 = _geo.transform_xy(x, y, "EPSG:32646", "EPSG:32645")
        xb, yb = _geo.transform_xy(x2, y2, "EPSG:32645", "EPSG:32646")
        self.assertLess(float(np.abs(xb - x).max()), 1e-3)
        s, n = _geo.lonlat_to_utm(-60.0, -30.0, 21, False)
        self.assertGreater(float(n), 6e6)                    # southern hemisphere false northing


class ResampleTests(unittest.TestCase):
    def test_identity_window_and_block_mean(self):
        g = Grid("a", "EPSG:32646", 1000.0, 5000.0, 20.0, 20.0, 40, 40)
        a = np.arange(1600, dtype=np.float32).reshape(40, 40)
        self.assertTrue(np.array_equal(resample(a, g, g, "mean"), a))
        coarse = Grid("b", g.crs, 1000.0, 5000.0, 80.0, 80.0, 10, 10)
        m = resample(a, g, coarse, "mean")
        self.assertAlmostEqual(float(m[0, 0]), float(a[:4, :4].mean()), places=3)
        win = Grid("c", g.crs, 1020.0, 4980.0, 20.0, 20.0, 10, 10)
        self.assertTrue(np.array_equal(resample(a, g, win, "bilinear"), a[1:11, 1:11]))
        nan_src = a.copy()
        nan_src[:4, :4] = np.nan
        self.assertTrue(np.isnan(resample(nan_src, g, coarse, "mean")[0, 0]))

    def test_bilinear_reproduces_linear_ramp(self):
        src = Grid("s", "EPSG:32646", 0.0, 8000.0, 80.0, 80.0, 100, 100)
        X, Y = np.meshgrid(src.x_centers(), src.y_centers())
        ramp = (0.01 * X + 0.02 * Y).astype(np.float32)
        dst = Grid("d", src.crs, 333.0, 7777.0, 80.0, 80.0, 80, 80)
        out = resample(ramp, src, dst, "bilinear")
        Xd, Yd = np.meshgrid(dst.x_centers(), dst.y_centers())
        self.assertLess(float(np.nanmax(np.abs(out - (0.01 * Xd + 0.02 * Yd)))), 0.05)

    def test_canonical_grid_is_deterministic_and_aligned(self):
        g1, g2 = canonical_grids(syn.TILE, PreprocConfig()), canonical_grids(syn.TILE, PreprocConfig())
        self.assertEqual(g1, g2)
        self.assertAlmostEqual(g1["G80"].x0 % 80.0, 0.0, places=6)
        self.assertEqual(g1["G20"].shape, (g1["G80"].ny * 4, g1["G80"].nx * 4))


class P01Tests(unittest.TestCase):
    def test_dedupe_baseline_and_target(self):
        cfg = PreprocConfig(max_temporal_baseline_days=36)
        t = datetime(2026, 8, 13)
        c = [syn.gunw_record("a_v1", t, version="0.1.0"), syn.gunw_record("a_v2", t, version="0.2.0"),
             syn.gunw_record("old", t - timedelta(days=12)),
             syn.gunw_record("long", t - timedelta(days=24), baseline_days=48),
             syn.gunw_record("other_track", t - timedelta(days=24), track=9)]
        r = p01.resolve(c, syn.TILE, cfg)
        self.assertEqual(r.current.granule_id, "a_v2")
        self.assertEqual([g.granule_id for g in r.history], ["old"])
        reasons = dict(r.dropped)
        self.assertEqual(reasons["long"], "baseline_too_long")
        self.assertEqual(reasons["a_v1"], "superseded_by_newer_processing_version")
        self.assertNotIn("other_track", [g.granule_id for g in r.history])


class ChainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inp, cls.native = syn.make_scene(n_history=6)
        cls.res = run_preprocessing(cls.inp)
        cls.b = cls.res.bundle

    def test_ok_and_core_layers(self):
        self.assertEqual(self.res.status, "ok", self.res.error)
        for n in ("los_disp_m", "los_sigma_m", "coherence", "conncomp", "valid", "layover_shadow", "stable",
                  "slope_deg", "hand_m", "coh_med", "coh_mad", "incidence_deg", "los_e", "los_n", "los_u"):
            self.assertTrue(self.b.has(n), n)
            self.assertEqual(self.b.layer(n).shape, self.b.grid_of(n).shape)
        self.assertEqual([s.stage for s in self.res.stage_log], [f"P{i:02d}" for i in range(1, 14)])

    def test_read_only(self):
        with self.assertRaises(ValueError):
            self.b.layer("los_disp_m")[0, 0] = 1.0
        with self.assertRaises(TypeError):
            self.b.layers["x"] = np.zeros(1)
        with self.assertRaises(Exception):
            self.b.bundle_id = "x"

    def test_displacement_recovery_and_sign(self):
        g = self.b.grids["G80"]
        truth = syn.truth_disp(g)                             # bowl is symmetric about tile centre
        got = self.b.layer("los_disp_m")
        ok = np.isfinite(got)
        self.assertGreater(float(got[ok].max()), 0.0)         # toward sensor positive
        self.assertLess(float(np.median(np.abs(got[ok] - truth[ok]))), 1.5e-3)
        cc = self.b.layer("conncomp")                         # gating: cc<1 (failed + fill) -> NaN disp
        self.assertTrue(np.isnan(got[cc < 1]).all())
        self.assertGreater(int((cc < 1).sum()), 0)

    def test_coherence_is_not_gated(self):
        coh, cc = self.b.layer("coherence"), self.b.layer("conncomp")
        low = (cc < 1) & np.isfinite(coh)
        self.assertGreater(int(low.sum()), 0)                 # damage-style signal survives
        self.assertLess(float(np.nanmin(coh)), 0.3)

    def test_geometry(self):
        e, n, u = (float(np.nanmedian(self.b.layer(k))) for k in ("los_e", "los_n", "los_u"))
        self.assertAlmostEqual(e, syn.LOS_X, places=3)
        self.assertAlmostEqual(n, syn.LOS_Y, places=3)
        self.assertAlmostEqual(u, float(np.sqrt(1 - syn.LOS_X**2 - syn.LOS_Y**2)), places=3)
        self.assertEqual(self.b.meta["layover_shadow_source"], "dem_derived")
        self.assertEqual(self.b.meta["los_positive"], "toward_sensor")
        self.assertNotIn("INCIDENCE_INCONSISTENT", self.b.flags)

    def test_audit_records_never_applies(self):
        c = self.b.corrections["corrections"]
        self.assertTrue(c["ionosphere"]["available"])
        self.assertFalse(c["ionosphere"]["engine_applied"])
        self.assertFalse(c["wet_troposphere"]["available"])
        self.assertIn("elevation_correlation", self.b.corrections)
        self.assertIsNotNone(self.b.corrections["residual_ramp"])

    def test_stack_and_flags(self):
        self.assertEqual(self.b.stack.n_pairs, 7)
        self.assertFalse(self.b.cold_start)
        self.assertEqual(self.b.stack.coh_stack.shape[0], 7)
        self.assertIn("BETA_DATA_IN_STACK", self.b.flags)     # some refs predate 2026-06-17
        self.assertIn("HAND_IS_PROXY", self.b.flags)
        self.assertNotIn("COLD_START", self.b.flags)
        self.assertTrue(np.isfinite(self.b.layer("coh_med")).any())
        self.assertTrue(self.b.stack.pairs[-1]["is_current"])

    def test_stable_mask(self):
        st, g = self.b.layer("stable"), self.b.grids["G80"]
        coh = self.b.layer("coherence")
        self.assertGreater(int((coh < 0.5).sum()), 0)         # the decorrelated patch survived to G80
        self.assertFalse(st[coh < 0.5].any())                 # ... and is excluded from the stable set
        self.assertGreater(float(st.mean()), 0.2)
        slope, water = self.b.layer("slope_deg"), self.b.layer("water")
        self.assertFalse(st[water].any())
        self.assertFalse((st & (slope > 15)).any())
        self.assertGreater(float((slope > 15).sum()), 0)      # the hill really is steep

    def test_routing_reasons(self):
        d = self.b.routing["decisions"]
        self.assertTrue(d["earthquake_coseismic"]["applicable"])
        self.assertIn("MISSING_BACKSCATTER", d["flood_inundation"]["reasons"])
        self.assertTrue(any(r.startswith("INSUFFICIENT_PAIRS") for r in d["land_subsidence"]["reasons"]))
        self.assertTrue(any(r.startswith("SENSOR_") for r in d["oil_spill"]["reasons"]))
        self.assertEqual(set(d), set(__import__("engine.common.p12_tile_routing", fromlist=["x"]).DEFAULT_REQUIREMENTS))
        self.assertIn("BACKSCATTER_UNAVAILABLE", self.b.flags)
        self.assertEqual(self.res.stage_log[7].status, "skipped")   # P08

    def test_custom_requirements_override(self):
        r = run_preprocessing(self.inp, requirements={"only_one": {"needs": ["displacement"]}})
        self.assertEqual(list(r.bundle.routing["decisions"]), ["only_one"])
        self.assertTrue(r.bundle.routing["decisions"]["only_one"]["applicable"])


class OtherScenarios(unittest.TestCase):
    def test_cold_start_single_pair(self):
        inp, _ = syn.make_scene(n_history=0)
        b = build_scene_bundle(inp)
        self.assertTrue(b.cold_start)
        self.assertIn("COLD_START", b.flags)
        self.assertIn("SINGLE_PAIR_ONLY", b.flags)
        self.assertTrue(np.isnan(b.layer("coh_med")).all())
        self.assertEqual(b.meta["stable_coherence_source"], "current_epoch")

    def test_determinism_and_sensitivity(self):
        a = build_scene_bundle(syn.make_scene(2)[0])
        b = build_scene_bundle(syn.make_scene(2)[0])
        self.assertEqual(a.bundle_id, b.bundle_id)
        self.assertTrue(np.array_equal(a.layer("los_disp_m"), b.layer("los_disp_m"), equal_nan=True))
        c = build_scene_bundle(syn.make_scene(2)[0], PreprocConfig(n_looks=50.0))
        self.assertNotEqual(a.bundle_id, c.bundle_id)
        d = build_scene_bundle(syn.make_scene(3)[0])
        self.assertNotEqual(a.bundle_id, d.bundle_id)

    def test_gcov_backscatter(self):
        inp, _ = syn.make_scene(n_history=3, with_gcov=True, n_gcov_hist=3)
        b = build_scene_bundle(inp)
        self.assertNotIn("BACKSCATTER_UNAVAILABLE", b.flags)
        self.assertEqual(b.layer_grid["gamma0_hv_db"], "G20")
        self.assertAlmostEqual(float(np.nanmedian(b.layer("gamma0_hh_db"))), -10.0, places=2)
        self.assertAlmostEqual(float(np.nanmedian(b.layer("gamma0_hv_db"))), 10 * np.log10(0.02), places=2)
        self.assertAlmostEqual(float(np.nanmedian(b.layer("hv_hh_ratio_db"))), 10 * np.log10(0.2), places=2)
        self.assertTrue(np.isfinite(b.layer("gamma0_hv_med_db")).any())
        self.assertEqual(b.stack.bs_stack["gamma0_hv"].shape[0], 4)
        self.assertTrue(b.routing["decisions"]["flood_inundation"]["applicable"])
        self.assertTrue(b.has("slope_g20_deg") and b.has("hand_g20_m"))

    def test_missing_dem_and_water_are_flagged_not_fatal(self):
        inp, _ = syn.make_scene(n_history=2, with_dem=False, with_water=False)
        b = build_scene_bundle(inp)
        self.assertIn("DEM_MISSING", b.flags)
        self.assertIn("WATER_MASK_MISSING", b.flags)
        self.assertFalse(b.has("slope_deg"))
        self.assertIn("LAYOVER_SHADOW_NOT_AVAILABLE", b.flags)
        self.assertIn("MISSING_DEM", b.routing["decisions"]["volcanic_deformation"]["reasons"])

    def test_failure_is_reported_with_stage(self):
        inp, native = syn.make_scene(n_history=1)
        inp.clip_source._clips["G_cur"] = syn.make_gunw_clip("G_cur", native, drop=("coherence",))
        r = run_preprocessing(inp)
        self.assertEqual((r.status, r.failed_stage), ("failed", "P02"))
        self.assertIn("coherence", r.error)
        with self.assertRaises(Exception):
            build_scene_bundle(inp)

    def test_clip_outside_tile_fails_at_p04(self):
        inp, native = syn.make_scene(n_history=0)
        far = Grid("far", native.crs, native.x0 + 500000.0, native.y0, native.dx, native.dy, *native.shape)
        c = syn.make_gunw_clip("G_cur", far)
        inp.clip_source._clips["G_cur"] = c
        r = run_preprocessing(inp)
        self.assertEqual((r.status, r.failed_stage), ("failed", "P04"))

    def test_bad_history_pair_is_recorded_not_silent(self):
        inp, _ = syn.make_scene(n_history=3)
        del inp.clip_source._clips["G_h1"]
        b = build_scene_bundle(inp)
        self.assertIn("HISTORY_PAIRS_SKIPPED", b.flags)
        bad = [p for p in b.stack.pairs if not p["processed"]]
        self.assertEqual(len(bad), 1)
        self.assertIn("ClipError", bad[0]["skip_reason"])
        self.assertEqual(b.stack.n_pairs, 3)

    def test_degraded_input_flag(self):
        inp, native = syn.make_scene(n_history=1)
        inp.clip_source._clips["G_cur"] = syn.make_gunw_clip("G_cur", native, extra_attrs={"degraded_mode": True})
        b = build_scene_bundle(inp)
        self.assertTrue(b.degraded_mode)
        self.assertIn("DEGRADED_INPUT", b.flags)

    def test_sign_convention_flag(self):
        inp, _ = syn.make_scene(n_history=0)
        a = build_scene_bundle(inp)
        b = build_scene_bundle(inp, PreprocConfig(phase_means_range_increase=False))
        ok = np.isfinite(a.layer("los_disp_m"))
        self.assertTrue(np.allclose(a.layer("los_disp_m")[ok], -b.layer("los_disp_m")[ok], atol=1e-6))

    def test_sensor_to_target_flips_horizontal(self):
        inp, _ = syn.make_scene(n_history=0)
        b = build_scene_bundle(inp, PreprocConfig(los_convention="sensor_to_target"))
        self.assertAlmostEqual(float(np.nanmedian(b.layer("los_e"))), -syn.LOS_X, places=3)
        self.assertGreater(float(np.nanmedian(b.layer("los_u"))), 0.0)

    def test_persistence_roundtrip(self):
        inp, _ = syn.make_scene(n_history=3, with_gcov=True, n_gcov_hist=2)
        b = build_scene_bundle(inp)
        with tempfile.TemporaryDirectory() as td:
            out = b.save(td)
            r = SceneBundle.load(out)
        self.assertEqual(r.bundle_id, b.bundle_id)
        self.assertEqual(set(r.layers), set(b.layers))
        for k in b.layers:
            self.assertTrue(np.array_equal(r.layer(k), b.layer(k), equal_nan=True), k)
        self.assertEqual(r.flags, b.flags)
        self.assertEqual(r.grids["G80"], b.grids["G80"])
        self.assertEqual(r.stack.n_pairs, b.stack.n_pairs)
        self.assertEqual(r.stack.bs_stack["gamma0_hv"].shape, b.stack.bs_stack["gamma0_hv"].shape)
        with self.assertRaises(ValueError):
            r.layer("los_disp_m")[0, 0] = 0

    def test_config_from_mapping_ignores_foreign_keys(self):
        c = PreprocConfig.from_mapping({"stack_min_samples": 6, "some_other_teams_key": 1, "conncomp_fill_values": [1]})
        self.assertEqual(c.stack_min_samples, 6)
        with self.assertRaises(ValueError):
            PreprocConfig(g20_spacing_m=30.0)


class BoundaryTests(unittest.TestCase):
    """The package must stay inside its lane."""

    def test_no_imports_from_other_engine_areas_and_numpy_only(self):
        stdlib = {"__future__", "dataclasses", "datetime", "typing", "types", "json", "math", "re", "time",
                  "hashlib", "platform", "pathlib", "warnings", "numpy"}
        banned = re.compile(r"^engine\.(retrieval|pipelines|lib|verdict|api|registry|validation)")
        for f in PKG.glob("*.py"):
            tree = ast.parse(f.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    if node.level == 0:
                        top = (node.module or "").split(".")[0]
                        self.assertFalse(banned.match(node.module or ""), f"{f.name}: {node.module}")
                        if top == "engine":
                            self.assertTrue((node.module or "").startswith("engine.common"), f"{f.name}")
                        elif not (f.name == "_geo.py" and top == "pyproj"):   # optional, lazy, exotic CRS only
                            self.assertIn(top, stdlib, f"{f.name} imports {node.module}")
                elif isinstance(node, ast.Import):
                    for a in node.names:
                        self.assertIn(a.name.split(".")[0], stdlib, f"{f.name} imports {a.name}")

    def test_pipeline_files_untouched(self):
        # this package ships only inside engine/common
        root = PKG.parent
        self.assertEqual(sorted(p.name for p in root.iterdir() if p.is_dir() and p.name != "__pycache__"), ["common"])


if __name__ == "__main__":
    unittest.main()
