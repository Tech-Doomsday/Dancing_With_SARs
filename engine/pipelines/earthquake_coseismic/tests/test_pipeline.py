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
        result = eq_run(bundle).to_dict()
    finally:
        eq_fetch.reset_event_provider()

    assert result["status"] == "SUPPORTED", result["detail"].get("decision")
    assert result["raw_p"] is not None and 0.0 <= result["raw_p"] <= 1.0
    assert {l["name"] for l in result["layers"]} >= {"displacement", "threshold", "detected"}
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
        result = eq_run(bundle).to_dict()
    finally:
        eq_fetch.reset_event_provider()
    assert result["status"] == "NOT_SUPPORTED"
    assert result["detail"]["decision"]["rule"] == "no_detection"
    texts = " ".join(e["claim"] for e in result["evidence"])
    assert "NOT that the ground did not move" in texts


def test_provider_failure_maps_to_insufficient_evidence(tmp_path, tile):
    specs = make_scene(tile, n_epochs=4, pairs=((0, 1), (1, 2), (2, 3)), dlos=0.05)
    bundle = _build_bundle(tmp_path, tile, specs)
    eq_fetch.set_event_provider(make_provider(error="ComCat unreachable: fixture outage"))
    try:
        result = eq_run(bundle).to_dict()
    finally:
        eq_fetch.reset_event_provider()
    assert result["status"] == "INSUFFICIENT_EVIDENCE"
    assert any("fixture outage" in str(e.get("claim", "")) for e in result["evidence"])


def test_no_event_in_window_still_reports_detection_honestly(tmp_path, tile):
    """Detection present, genuinely empty catalog: the fit now runs with REAL residuals
    (the dip-90 q2 crash used to sink it to a silent 1e9 sentinel). The v1 fit family is
    a vertical STRIKE-SLIP source (plan 8.6), which cannot reproduce the single-lobe
    truth bowl — so per plan 8.4 row 4 the honest answer is NOT_SUPPORTED
    (uncatalogued_fit_not_converged). An unconverged fit must never read as
    UNCATALOGUED_CANDIDATE."""
    specs = make_scene(tile, n_epochs=4, pairs=((0, 1), (1, 2), (2, 3)), dlos=0.05)
    bundle = _build_bundle(tmp_path, tile, specs)
    eq_fetch.set_event_provider(make_null_provider())
    try:
        result = eq_run(bundle).to_dict()
    finally:
        eq_fetch.reset_event_provider()
    assert result["status"] == "NOT_SUPPORTED", result["detail"].get("decision")
    assert result["detail"]["decision"]["rule"] == "uncatalogued_fit_not_converged"
    fit = result["detail"]["uncatalogued"]
    assert fit["attempted"] is True and fit["converged"] is False
    assert fit["reason"]                                      # the refusal is explained
    assert fit["rms_m"] < 1e6                                 # a real RMS, not the old crash sentinel (1e9)
    texts = " ".join(e["claim"] for e in result["evidence"]).lower()
    assert "no catalog event" in texts
    assert "did not converge" in texts


def test_catalogued_event_without_mechanism_is_insufficient(tmp_path, tile):
    """The reviewer's repro (final-prompt fix 1): a catalogued Mw 6.4 event 4.7 km from
    the tile with NO published nodal planes must be INSUFFICIENT_EVIDENCE
    (events_unscreenable) — most smaller ComCat events lack a mechanism, so the old
    UNCATALOGUED_CANDIDATE answer would have been the common case, and wrong."""
    specs = make_scene(tile, n_epochs=4, pairs=((0, 1), (1, 2), (2, 3)), dlos=0.05)
    bundle = _build_bundle(tmp_path, tile, specs)
    lat, lon = _event_lonlat(tile)
    event = make_event(event_id="us6000-mw64", magnitude=6.4, lat=lat, lon=lon,
                       time="2026-08-01T00:00:00Z", nodal_planes=())
    eq_fetch.set_event_provider(make_provider([event]))
    try:
        result = eq_run(bundle).to_dict()
    finally:
        eq_fetch.reset_event_provider()
    assert result["status"] == "INSUFFICIENT_EVIDENCE", result["detail"].get("decision")
    assert result["detail"]["decision"]["rule"] == "events_unscreenable"
    assert result["detail"]["decision"]["n_events_in_window"] == 1
    texts = " ".join(e["claim"] for e in result["evidence"])
    assert "no catalog event" not in texts.lower()
    assert any("us6000-mw64" in str(e.get("support") or {}) or "us6000-mw64" in str(e.get("claim", ""))
               for e in result["evidence"])
    # the detection itself is still reported honestly alongside the data gap
    assert any(e["kind"] == "detection" for e in result["evidence"])


def test_low_coherence_map_maps_to_insufficient_evidence(tmp_path, tile):
    specs = make_scene(tile, n_epochs=4, pairs=((0, 1), (1, 2), (2, 3)), dlos=0.05)
    for spec in specs:
        if spec.kind == "GUNW":
            spec.coherence = 0.05          # below the 0.5 gate everywhere
    bundle = _build_bundle(tmp_path, tile, specs)
    eq_fetch.set_event_provider(make_null_provider())
    try:
        result = eq_run(bundle).to_dict()
    finally:
        eq_fetch.reset_event_provider()
    assert result["status"] == "INSUFFICIENT_EVIDENCE"
    assert any("pixels pass the coherence threshold" in str(e.get("claim", "")) for e in result["evidence"])


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
        result = eq_run(bundle).to_dict()
    finally:
        eq_fetch.reset_event_provider()
    # nothing above threshold + genuinely no catalog event in the window: NO_SIGNAL
    # (the docx's sixth status — a quiet outcome, not a tested-and-rejected quake)
    assert result["status"] == "NO_SIGNAL"
    assert result["detail"]["decision"]["rule"] == "no_event_no_cluster"
    selected = next(r for r in result["detail"]["pairs_analysed"] if r.get("selected"))
    assert selected["detected_pixels"] == 0
