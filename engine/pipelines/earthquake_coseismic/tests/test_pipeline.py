"""T-8/T-9: earthquake_coseismic end-to-end on the REAL engine chain.

Builds SceneBundles through P01..P13 with tests/common_fixtures.py synthetic
truth (FakeRetriever, no network, no HDF5), asserts the bundle contract, then
runs the pipeline through the registry — the exact production path minus the
process pool. Event sources are injected via the fetch provider seam
(READINESS-mandated); the live R1 adapter is exercised by the @live tests.

NOTE: this module requires the engine repo around it (engine.common chain,
tests/common_fixtures). Kernel-level tests in this folder run standalone.
"""
from __future__ import annotations

from datetime import timedelta

import numpy as np
import pytest

pytest.importorskip("pyproj")

from engine.common import (  # noqa: E402
    DirectoryAncillary,
    assert_bundle_contract,
    build_scene_bundle,
)
from engine.pipelines.earthquake_coseismic import fetch as eq_fetch  # noqa: E402
from engine.pipelines.earthquake_coseismic.types import EqParams  # noqa: E402
from engine.pipelines.earthquake_coseismic.verdict import run as eq_run  # noqa: E402
from tests.common_fixtures import (  # noqa: E402
    FakeRetriever,
    default_tile,
    dt,
    make_scene,
    write_ancillary,
)

from .fixtures_eq import make_event, make_null_provider, make_provider  # noqa: E402

AS_OF = dt("2026-08-10")
START = dt("2026-07-01")
DAYS = [START + timedelta(days=12 * i) for i in range(4)]
PAIR_WINDOW = ("2026-07-25T00:00:00Z", "2026-08-06T00:00:00Z")   # pair (2, 3)


def _iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def _event_lonlat(tile):
    """Fixture-event epicentre: 3 km west / 3 km south of the truth-bowl
    centre (calibrated so the Okada forward prediction matches the bowl)."""
    from pyproj import Transformer
    tr = Transformer.from_crs(tile.epsg, 4326, always_xy=True)
    lon, lat = tr.transform(tile.e0 + 4000.0 - 3000.0, tile.n0 + 5500.0 - 3000.0)
    return float(lat), float(lon)


def _build_bundle(tmp_path, tile, specs, event_window=PAIR_WINDOW):
    anc_root = write_ancillary(tmp_path / "ancillary", tile)
    return build_scene_bundle(
        tile, FakeRetriever(specs), as_of=AS_OF,
        ancillary=DirectoryAncillary(anc_root),
        event_window=event_window,
    )


@pytest.fixture(scope="module")
def tile():
    return default_tile()


def test_supported_on_synthetic_eq_scene(tmp_path, tile):
    """Truth bowl (+50 mm toward) + a matching catalog event => SUPPORTED."""
    specs = make_scene(tile, n_epochs=4, pairs=((0, 1), (1, 2), (2, 3)), dlos=0.05)
    bundle = _build_bundle(tmp_path, tile, specs)
    assert_bundle_contract(bundle, deep=True)
    assert bundle.request.event_window == PAIR_WINDOW

    lat, lon = _event_lonlat(tile)
    event = make_event(lat=lat, lon=lon, time="2026-08-01T00:00:00Z")
    eq_fetch.set_event_provider(make_provider([event]))
    try:
        result = eq_run(bundle)
    finally:
        eq_fetch.reset_event_provider()

    assert result["status"] == "SUPPORTED", result["detail"].get("decision")
    assert result["raw_p"] is not None and 0.0 <= result["raw_p"] <= 1.0
    assert set(result["layers"]) >= {"displacement", "threshold", "detected"}
    detail = result["detail"]
    selected = next(r for r in detail["pairs_analysed"] if r.get("selected"))
    assert selected["detected_pixels"] > 0
    assert selected["affected_area_km2"] > 0.5
    screens = [s for s in detail["forward_screens"] if not s["skipped"]]
    assert screens, "forward model should run for an event with a nodal plane"
    best = max(screens, key=lambda s: (s["pattern_correlation"], -abs(math_log(s["amplitude_ratio"]))))
    assert 1.0 / 3.0 < best["amplitude_ratio"] < 3.0, best
    assert best["pattern_correlation"] > 0.25, best
    assert any(e["kind"] == "detection" for e in result["evidence"])
    assert any("R1 ComCat" in d for d in result["data_used"])
    # residual layer exists and is smaller than the raw signal where the model agrees
    assert "residual" in detail["layers"]


def math_log(v):
    import math
    return math.log(max(abs(v), 1e-9))


def test_null_scene_is_not_supported_with_null_hypothesis_language(tmp_path, tile):
    specs = make_scene(tile, n_epochs=4, pairs=((0, 1), (1, 2), (2, 3)), dlos=0.0)
    bundle = _build_bundle(tmp_path, tile, specs)
    lat, lon = _event_lonlat(tile)
    event = make_event(lat=lat, lon=lon, time="2026-08-01T00:00:00Z")
    eq_fetch.set_event_provider(make_provider([event]))
    try:
        result = eq_run(bundle)
    finally:
        eq_fetch.reset_event_provider()
    assert result["status"] == "NOT_SUPPORTED"
    assert result["detail"]["decision"]["rule"] == "no_detection"
    texts = " ".join(e["text"] for e in result["evidence"])
    assert "NOT that the ground did not move" in texts


def test_provider_failure_maps_to_insufficient_evidence(tmp_path, tile):
    specs = make_scene(tile, n_epochs=4, pairs=((0, 1), (1, 2), (2, 3)), dlos=0.05)
    bundle = _build_bundle(tmp_path, tile, specs)
    eq_fetch.set_event_provider(make_provider(error="ComCat unreachable: fixture outage"))
    try:
        result = eq_run(bundle)
    finally:
        eq_fetch.reset_event_provider()
    assert result["status"] == "INSUFFICIENT_EVIDENCE"
    assert any("fixture outage" in str(e.get("text", "")) for e in result["evidence"])


def test_no_event_in_window_still_reports_detection_honestly(tmp_path, tile):
    """Detection present, genuinely empty catalog: the signal is reported as-is."""
    specs = make_scene(tile, n_epochs=4, pairs=((0, 1), (1, 2), (2, 3)), dlos=0.05)
    bundle = _build_bundle(tmp_path, tile, specs)
    eq_fetch.set_event_provider(make_null_provider())
    try:
        result = eq_run(bundle)
    finally:
        eq_fetch.reset_event_provider()
    # truth is an isotropic uplift bowl: not representable by the v1 vertical strike-slip fit, so the
    # honest outcome is "no candidate claimed" with the fit's own reason on record
    assert result["status"] in ("UNCATALOGUED_CANDIDATE", "NOT_SUPPORTED")
    assert result["detail"]["decision"]["rule"] in (
        "uncatalogued_fit_converged", "uncatalogued_fit_not_converged", "sub_credible_no_catalog_event")
    if result["detail"]["decision"]["rule"] != "sub_credible_no_catalog_event":
        assert result["detail"]["uncatalogued"]["attempted"] is True
    assert "_prediction" not in (result["detail"]["uncatalogued"] or {})


def test_low_coherence_map_maps_to_insufficient_evidence(tmp_path, tile):
    specs = make_scene(tile, n_epochs=4, pairs=((0, 1), (1, 2), (2, 3)), dlos=0.05)
    for spec in specs:
        if spec.kind == "GUNW":
            spec.coherence = 0.05          # below the 0.5 gate everywhere
    bundle = _build_bundle(tmp_path, tile, specs)
    eq_fetch.set_event_provider(make_null_provider())
    try:
        result = eq_run(bundle)
    finally:
        eq_fetch.reset_event_provider()
    assert result["status"] == "INSUFFICIENT_EVIDENCE"
    assert any("pixels pass the coherence threshold" in str(e.get("text", "")) for e in result["evidence"])


def test_registry_run_tile_end_to_end(tmp_path, tile):
    """The production entry: Registry.run_tile routes, gates and runs 3.01 (inline)."""
    from engine.registry import Registry

    specs = make_scene(tile, n_epochs=4, pairs=((0, 1), (1, 2), (2, 3)), dlos=0.05)
    anc_root = write_ancillary(tmp_path / "ancillary", tile)
    lat, lon = _event_lonlat(tile)
    event = make_event(lat=lat, lon=lon, time="2026-08-01T00:00:00Z")
    eq_fetch.set_event_provider(make_provider([event]))
    try:
        reg = Registry(cfg=None)  # default config; pipelines dir = repo engine/pipelines
        run = reg.run_tile(
            tile, FakeRetriever(specs), as_of=AS_OF,
            ancillary=DirectoryAncillary(anc_root),
            event_window=PAIR_WINDOW,
            names=["earthquake_coseismic", "landslide"],
            workers=0,
        )
    finally:
        eq_fetch.reset_event_provider()

    eq = run.results["earthquake_coseismic"]
    assert eq.status == "SUPPORTED"
    assert eq.raw_p is not None and 0.0 <= eq.raw_p <= 1.0
    assert eq.tile == tile.id
    assert eq.bundle_id == bundle_id_of(run)
    assert any("M1" in d or "R1" in d for d in eq.data_used)
    assert eq.detail["decision"]["rule"] == "screen_pass"

    # the unimplemented sibling reports honestly (NOT_MODELLED), never crashes
    ls = run.results["landslide"]
    assert ls.status in ("NOT_MODELLED", "NOT_RUN", "NOT_APPLICABLE", "INSUFFICIENT_EVIDENCE")


def bundle_id_of(run) -> str:
    return run.bundle.bundle_id


def test_eq_params_override_via_triggers(tmp_path, tile):
    """UI/API parameter overrides ride the bundle's triggers (registry contract)."""
    specs = make_scene(tile, n_epochs=4, pairs=((0, 1), (1, 2), (2, 3)), dlos=0.05)
    anc_root = write_ancillary(tmp_path / "ancillary", tile)
    bundle = build_scene_bundle(
        tile, FakeRetriever(specs), as_of=AS_OF,
        ancillary=DirectoryAncillary(anc_root),
        event_window=PAIR_WINDOW,
        triggers={"eq_params": {"detection_floor_m": 0.2, "significance_k": 5.0}},
    )
    params = EqParams.from_triggers(bundle.request.triggers)
    assert params.detection_floor_m == 0.2
    assert params.significance_k == 5.0
    # floor 200 mm > the 50 mm truth peak => no detections possible
    eq_fetch.set_event_provider(make_null_provider())
    try:
        result = eq_run(bundle)
    finally:
        eq_fetch.reset_event_provider()
    # empty catalog + nothing above the raised floor => NO_SIGNAL (with the floor in the headline)
    assert result["status"] == "NO_SIGNAL"
    assert "200 mm" in result["detail"]["narrative"]["headline"]
    selected = next(r for r in result["detail"]["pairs_analysed"] if r.get("selected"))
    assert selected["detected_pixels"] == 0


def test_uncatalogued_candidate_on_synthetic_source_with_empty_catalog(tmp_path, tile, monkeypatch):
    """T-8 UNCATALOGUED path on the real chain.

    Truth = a vertical strike-slip Okada source (fixtures_eq.TRUE_SOURCE) injected through the
    engine fixture's truth hook, empty catalog => UNCATALOGUED_CANDIDATE with a converged fit that
    recovers the source, a residual layer, and the v1 simplifications on record.
    """
    import tests.common_fixtures as cf
    from .fixtures_eq import TRUE_SOURCE, fixture_los_unit, okada_los_field

    # the fixture's LOS vector projects a 0.15 m strike-slip weakly (peak ~14 mm < the 10 mm floor
    # after noise), so the end-to-end source is stronger: peak ~50 mm, a clearly detectable field
    E2E_SOURCE = dict(TRUE_SOURCE, slip=0.5)
    los = fixture_los_unit()
    original = cf.los_truth

    def source_truth(t, x, y, peak):
        if peak == 0.0:                                      # pairs that do not span the event
            return original(t, x, y, 0.0)
        return okada_los_field(x, y, t.e0, t.n0, los, E2E_SOURCE).astype(np.float32)

    monkeypatch.setattr(cf, "los_truth", source_truth)
    specs = make_scene(tile, n_epochs=4, pairs=((0, 1), (1, 2), (2, 3)), dlos=0.05)
    bundle = _build_bundle(tmp_path, tile, specs)
    assert_bundle_contract(bundle, deep=True)

    eq_fetch.set_event_provider(make_null_provider())
    try:
        result = eq_run(bundle)
    finally:
        eq_fetch.reset_event_provider()

    decision = result["detail"]["decision"]
    assert result["status"] == "UNCATALOGUED_CANDIDATE", (decision, result["detail"]["uncatalogued"])
    assert decision["rule"] == "uncatalogued_fit_converged"
    assert result["raw_p"] is not None and 0.0 < result["raw_p"] <= 1.0

    fit = result["detail"]["uncatalogued"]
    assert fit["converged"] and "_prediction" not in fit            # JSON-able: no raw array leaks out
    p = fit["parameters"]
    assert np.hypot(p["east_m"] - (tile.e0 + TRUE_SOURCE["east"]),
                    p["north_m"] - (tile.n0 + TRUE_SOURCE["north"])) < 600.0
    assert abs((p["strike_deg"] - TRUE_SOURCE["strike"] + 90.0) % 180.0 - 90.0) < 10.0

    sel = next(r for r in result["detail"]["pairs_analysed"] if r.get("selected"))
    assert sel["ref_sigma_m"] == 0.0 and sel["ref_scatter_m"] > 0.0       # tileMedian: scatter reported, not used
    assert "residual" in result["layers"] and "residual" in result["detail"]["layers"]
    kinds = {e["kind"] for e in result["evidence"]}
    assert {"uncatalogued", "uncatalogued_simplifications"} <= kinds
    unc_text = next(e["text"] for e in result["evidence"] if e["kind"] == "uncatalogued")
    assert "candidate" in unc_text and "not a detection" in unc_text
    import json
    json.dumps(result["detail"])                                      # whole detail must serialise


def _run_with(bundle, provider):
    eq_fetch.set_event_provider(provider)
    try:
        return eq_run(bundle)
    finally:
        eq_fetch.reset_event_provider()


def test_no_event_and_no_cluster_is_no_signal(tmp_path, tile):
    specs = make_scene(tile, n_epochs=4, pairs=((0, 1), (1, 2), (2, 3)), dlos=0.0)
    result = _run_with(_build_bundle(tmp_path, tile, specs), make_null_provider())
    assert result["status"] == "NO_SIGNAL", result["detail"]["decision"]
    assert result["raw_p"] is None
    assert result["detail"]["decision"]["rule"] == "no_event_no_cluster"
    assert any(e["kind"] == "no_signal" for e in result["evidence"])
    assert "NOT 'the ground did not move'" in result["detail"]["narrative"]["headline"]


def test_event_without_mechanism_is_not_modelled(tmp_path, tile):
    """A qualifying event that cannot be modelled must not become SUPPORTED/UNCATALOGUED."""
    specs = make_scene(tile, n_epochs=4, pairs=((0, 1), (1, 2), (2, 3)), dlos=0.05)
    lat, lon = _event_lonlat(tile)
    event = make_event(lat=lat, lon=lon, time="2026-08-01T00:00:00Z", nodal_planes=())
    result = _run_with(_build_bundle(tmp_path, tile, specs), make_provider([event]))
    assert result["status"] == "NOT_MODELLED", result["detail"]["decision"]
    assert result["raw_p"] is None
    assert result["detail"]["decision"]["rule"] == "event_not_modellable"
    kinds = {e["kind"] for e in result["evidence"]}
    assert {"not_modellable", "forward_model_skip"} <= kinds


def test_flipping_the_los_sign_of_the_truth_changes_the_verdict(tmp_path, tile):
    """Metamorphic sign gate: same event, truth bowl moved AWAY instead of toward the sensor.

    A convention flip anywhere between the bundle and the forward model would make both scenes
    agree with the event (or both disagree); the verdict must differ, and the flipped scene must
    anti-correlate with the model.
    """
    lat, lon = _event_lonlat(tile)
    event = make_event(lat=lat, lon=lon, time="2026-08-01T00:00:00Z")
    results = {}
    for name, dlos in (("toward", 0.05), ("away", -0.05)):
        specs = make_scene(tile, n_epochs=4, pairs=((0, 1), (1, 2), (2, 3)), dlos=dlos)
        bundle = _build_bundle(tmp_path / name, tile, specs)
        results[name] = _run_with(bundle, make_provider([event]))
    assert results["toward"]["status"] == "SUPPORTED"
    assert results["away"]["status"] != "SUPPORTED"
    away_screens = [s for s in results["away"]["detail"]["forward_screens"] if not s["skipped"]]
    assert away_screens and max(s["pattern_correlation"] for s in away_screens) < 0.25
