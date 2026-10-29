"""§5.1 item 5 contract test: every detail.layers[*] of every IMPLEMENTED pipeline's scenario is
a valid canonical LayerPayload (validate_layer_payload -> empty problem list). Also pins the
detections geometry equality (§5.4) and the D15 NO_SIGNAL tier."""
from __future__ import annotations

import numpy as np
import pytest

from engine.lib.layer_payload import validate_layer_payload
from tests.test_api import client, data_dir  # noqa: F401  (pytest fixtures)


def _run_to_ok(c, bundle_id, body):
    import time

    r = c.post("/api/runs", json={"bundle_id": bundle_id, **body})
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]
    state = {}
    for _ in range(300):
        state = c.get(f"/api/runs/{run_id}").json()
        if state["status"] in ("ok", "error"):
            break
        time.sleep(0.1)
    assert state["status"] == "ok", state.get("error")
    return run_id, state


def test_every_implemented_pipeline_emits_valid_layer_payloads(client):
    """earthquake_coseismic (normal lane) + tsunami_ocean_evidence (heavy lane, the D1 offender):
    every layer they emit passes the canonical contract."""
    c, _ = client
    bundles = c.get("/api/bundles").json()["bundles"]
    run_id, state = _run_to_ok(c, bundles[0]["bundle_id"],
                               {"pipelines": ["earthquake_coseismic", "tsunami_ocean_evidence"],
                                "params": {}})
    checked = 0
    for result in state["results"]:
        for name, payload in ((result.get("detail") or {}).get("layers") or {}).items():
            problems = validate_layer_payload(payload)
            assert not problems, f"{result['pipeline']}/{name}: {problems}"
            checked += 1
    assert checked >= 2, "both implemented pipelines must have emitted layers"

    # the API edge refuses an invalid payload with 422 + the problem list, never a 500
    from engine.api.state import get_runner
    from tests.test_cloud_cache import FakePostgrest  # noqa: F401  (import proves no network need)

    # inject a malformed layer into a finished run state and try to fetch it
    runner = get_runner()
    state2 = runner.get_state(run_id)
    broken = dict(next(r for r in state2["results"] if r["pipeline"] == "earthquake_coseismic"))
    detail = dict(broken.get("detail") or {})
    layers = dict(detail.get("layers") or {})
    broken_payload = dict(layers["displacement"])
    broken_payload["data_b64"] = "not-base64!!"
    broken_payload["bounds_lonlat"] = [[1.0, 2.0], [3.0, 4.0]]      # the legacy nested form
    layers["displacement"] = broken_payload
    detail["layers"] = layers
    broken["detail"] = detail
    runner._states[run_id]["results"] = [
        broken if r["pipeline"] == "earthquake_coseismic" else r
        for r in runner._states[run_id]["results"]]
    r = c.get(f"/api/runs/{run_id}/layers/displacement")
    assert r.status_code == 422, r.text
    detail_body = r.json()["detail"]
    assert detail_body["ok"] is False and detail_body["problems"]


def test_detected_clusters_match_detected_cluster_stats_exactly():
    """§5.4: the geometry function uses the SAME 4-neighbour rule and min_pixels — counts and
    areas must equal the existing function's output on random grids."""
    from engine.lib.detect import detected_cluster_stats, detected_clusters

    rng = np.random.default_rng(7)
    for trial in range(20):
        detected = (rng.random((37, 41)) > 0.82).astype(np.uint8)
        disp = rng.standard_normal((37, 41))
        disp[detected == 0] = np.nan
        stats = detected_cluster_stats(detected, pixel_area_km2=0.0064, min_pixels=4)
        clusters = detected_clusters(detected, disp, pixel_area_km2=0.0064, min_pixels=4)
        assert len(clusters) == stats["count"], f"trial {trial}"
        sizes = sorted((c["area_km2"] for c in clusters), reverse=True)
        assert sizes == stats["sizes_km2"], f"trial {trial}"


def test_no_signal_has_the_quiet_tier_d15():
    """D15: NO_SIGNAL is a quiet tile, not an error — in TIER_OF with its own label."""
    from engine.verdict.calibrate import TIER_LABEL, TIER_OF, calibrate, tier_of

    assert TIER_OF["NO_SIGNAL"] == "quiet"
    assert tier_of("NO_SIGNAL") == "quiet"
    assert TIER_LABEL["quiet"] == "no signal — quiet tile"
    row = calibrate({"status": "NO_SIGNAL", "pipeline": "earthquake_coseismic"})
    assert row["tier"] == "quiet" and row["tier_label"] == "no signal — quiet tile"


def test_tsunami_layers_row0_is_north_and_mercator_rows_noted():
    """D1 + DEC10: the tsunami canonical payload has flat bounds, and a >1 deg domain says it
    resampled rows to be linear in Web-Mercator Y."""
    from engine.lib.layer_payload import resample_mercator_rows
    from pyproj import Transformer

    # a 10-deg domain: resampling must re-map rows (mercator stretches high latitudes) and note it
    values = np.arange(50, dtype=np.float32)
    arr = values.reshape(50, 1) * np.ones((1, 3), np.float32)
    out, note = resample_mercator_rows(arr, lat_top=38.0, lat_bottom=28.0)
    assert out.shape == (50, 3) and "Web-Mercator Y" in note
    # nearest-neighbour never invents values: every output row is one of the source rows,
    # and the row ORDER (north -> south) is preserved (values here increase southward)
    assert set(out[:, 0].tolist()) <= set(values.tolist())
    assert list(out[:, 0]) == sorted(out[:, 0])
    # analytic position check: the source row whose latitude is KNOWN (row 25 = lat 33.1 deg)
    # must land on the output row whose own latitude is nearest to it
    tr = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    tr_inv = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
    _, y_top = tr.transform(0.0, 38.0)
    _, y_bot = tr.transform(0.0, 28.0)
    dst_y = y_top + (y_bot - y_top) * ((np.arange(50) + 0.5) / 50.0)
    dst_lat = tr_inv.transform(np.zeros(50), dst_y)[1]           # each output row's latitude
    src_lat = 38.0 + (28.0 - 38.0) * ((np.arange(50) + 0.5) / 50.0)
    known = 24                                                    # src row 24 = lat 33.1 deg
    assert round(float(src_lat[known]), 1) == 33.1
    k = int(np.abs(dst_lat - src_lat[known]).argmin())            # output row nearest that latitude
    assert out[k, 0] == values[known]
    # a <1 deg span is a no-op with an empty note
    out2, note2 = resample_mercator_rows(np.zeros((10, 2), np.float32), lat_top=34.4, lat_bottom=34.0)
    assert note2 == "" and out2.shape == (10, 2)
