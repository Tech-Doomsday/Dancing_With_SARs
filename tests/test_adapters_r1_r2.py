"""R1 ComCat + R2 ShakeMap adapter tests — parsing, cache, failure drill, live marker.

Covers the final-prompt defects:
  * `fetch_event_detail` assigned the `(payload, sha256)` tuple to `payload` and then
    called `.get` on it — the R2 ShakeMap lookup silently never worked. Pinned here
    with a recorded payload (regression).
  * R2 results carried no `event_id`, so the pipeline rendered "R2 ShakeMap extent
    for None". Every R2 result is now normalised (event_id always present) with an
    optional tile-coverage check.

Unit tests are NETWORK-GUARDED: any socket connect raises, so a test can never
silently reach the internet. Recorded payloads stand in at the `_http_get_json`
seam (the same injection point the adapters expose for tests).

Live behaviour is opt-in:
    EQ_LIVE=1 python -m pytest -m live -q tests/test_adapters_r1_r2.py
"""
from __future__ import annotations

import socket

import pytest

from engine.retrieval.clients.adapters import r1_comcat, r2_shakemap

# --------------------------------------------------------------------------- #
# network guard (unit tests only; the live-marked tests bypass it)
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _no_network(request, monkeypatch):
    if request.node.get_closest_marker("live"):
        yield
        return

    def _blocked(*a, **k):
        raise AssertionError("unit tests must not touch the network — inject a recorded payload instead")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    yield


@pytest.fixture(autouse=True)
def _cache_dir(tmp_path, monkeypatch):
    """Redirect the adapter disk cache into the test tmp dir (no repo pollution,
    no cross-test cache hits)."""
    d = tmp_path / "cache" / "r1_comcat"
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(r1_comcat, "_cache_dir", lambda: d)
    return d


# --------------------------------------------------------------------------- #
# recorded payloads (real ComCat shapes, trimmed)
# --------------------------------------------------------------------------- #
FEATURE = {
    "id": "us6000abcd",
    "properties": {
        "mag": 6.4, "place": "42 km SW of Testville", "time": 1754000000000,
        "url": "https://earthquake.usgs.gov/earthquakes/eventpage/us6000abcd",
        "products": {"focal-mechanism": [{"properties": {
            "nodal-plane-1-strike": "30", "nodal-plane-1-dip": "70", "nodal-plane-1-rake": "-10",
            "nodal-plane-2-strike": "120", "nodal-plane-2-dip": "85", "nodal-plane-2-rake": "-175",
        }}]},
    },
    "geometry": {"coordinates": [-118.31, 34.05, 8.7]},
}

FEATURE_NO_MECHANISM = {
    "id": "us6000nm",
    "properties": {"mag": 6.4, "place": "near the tile", "time": 1754000000000},
    "geometry": {"coordinates": [-118.30, 34.06, 5.0]},
}

DETAIL = {
    "id": "us6000abcd",
    "properties": {
        "mag": 6.4, "place": "42 km SW of Testville", "time": 1754000000000,
        "products": {"shakemap": [{"contents": {
            "download/extent.json": {"url": "https://example.com/extent.json"}}}]},
    },
    "geometry": {"coordinates": [-118.31, 34.05, 8.7]},
}


# --------------------------------------------------------------------------- #
# R1 parsing
# --------------------------------------------------------------------------- #
def test_parse_event_feature_full():
    ev = r1_comcat.parse_event_feature(FEATURE)
    assert ev is not None
    assert ev["id"] == "us6000abcd" and ev["magnitude"] == 6.4
    assert ev["lat"] == pytest.approx(34.05) and ev["lon"] == pytest.approx(-118.31)
    assert ev["depth_km"] == pytest.approx(8.7)
    assert ev["time"] == "2025-07-31T22:13:20Z"          # 1754000000000 ms, UTC
    assert len(ev["nodal_planes"]) == 2                   # both focal-mechanism planes
    assert (30.0, 70.0, -10.0) in ev["nodal_planes"]


def test_parse_event_feature_without_location_is_dropped():
    bad = {**FEATURE_NO_MECHANISM, "geometry": {"coordinates": [None, None, None]}}
    assert r1_comcat.parse_event_feature(bad) is None
    assert r1_comcat.parse_event_feature({}) is None


# --------------------------------------------------------------------------- #
# R1 fetch: cache, provenance, failure drill
# --------------------------------------------------------------------------- #
def test_fetch_events_parses_records_and_carries_provenance(monkeypatch):
    sha = "a" * 64
    monkeypatch.setattr(r1_comcat, "_http_get_json",
                        lambda url, t: ({"features": [FEATURE, FEATURE_NO_MECHANISM]}, sha))
    out = r1_comcat.fetch_events((-118.4, 33.9, -118.2, 34.2), "2025-08-01T00:00:00Z",
                                 "2025-08-02T00:00:00Z")
    assert out["error"] is None and out["degraded"] is False
    assert [e["id"] for e in out["events"]] == ["us6000abcd", "us6000nm"]
    assert out["provenance"]["sha256"] == sha
    assert out["provenance"]["adapter"] == "r1_comcat"


def test_fetch_events_second_call_is_served_from_cache(monkeypatch):
    calls = []

    def _rec(url, t):
        calls.append(url)
        return ({"features": [FEATURE]}, "b" * 64)

    monkeypatch.setattr(r1_comcat, "_http_get_json", _rec)
    first = r1_comcat.fetch_events((-118.4, 33.9, -118.2, 34.2), "2025-08-01T00:00:00Z",
                                   "2025-08-02T00:00:00Z")
    assert len(calls) == 1
    second = r1_comcat.fetch_events((-118.4, 33.9, -118.2, 34.2), "2025-08-01T00:00:00Z",
                                    "2025-08-02T00:00:00Z")
    assert len(calls) == 1                                # the network seam was NOT touched again
    assert second["source"].endswith("(cache)")
    assert [e["id"] for e in second["events"]] == [first["events"][0]["id"]]


def test_fetch_events_failure_is_a_result_not_an_exception(monkeypatch):
    import urllib.error

    def _down(url, t):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(r1_comcat, "_http_get_json", _down)
    out = r1_comcat.fetch_events((-118.4, 33.9, -118.2, 34.2), "2025-08-03T00:00:00Z",
                                 "2025-08-04T00:00:00Z")
    assert out["degraded"] is True and out["events"] == []
    assert "ComCat unreachable" in out["error"]


# --------------------------------------------------------------------------- #
# R1 detail — the tuple-as-dict regression (final-prompt fix 4)
# --------------------------------------------------------------------------- #
def test_fetch_event_detail_unpacks_payload_and_sha(monkeypatch):
    """`_http_get_json` returns (payload, sha256). The old code assigned the whole tuple
    to `payload` and called .get on it -> AttributeError; callers that swallow exceptions
    (the R2 lookup) silently degraded forever."""
    monkeypatch.setattr(r1_comcat, "_http_get_json", lambda url, t: (DETAIL, "c" * 64))
    out = r1_comcat.fetch_event_detail("us6000abcd")
    assert out["error"] is None and out["degraded"] is False
    assert out["event"]["id"] == "us6000abcd"
    assert "shakemap" in out["event"]["products"]


def test_fetch_event_detail_failure_is_a_result(monkeypatch):
    import urllib.error

    def _down(url, t):
        raise urllib.error.HTTPError(url, 503, "busy", {}, None)

    monkeypatch.setattr(r1_comcat, "_http_get_json", _down)
    out = r1_comcat.fetch_event_detail("us6000abcd")
    assert out["degraded"] is True and out["event"] is None
    assert "503" in out["error"]


# --------------------------------------------------------------------------- #
# R2 — normalised result + tile-coverage check (final-prompt fix 4)
# --------------------------------------------------------------------------- #
DETAIL_PARSED_EVENT = {
    "id": "us6000abcd", "magnitude": 6.4, "place": "42 km SW of Testville",
    "time": "2025-07-31T22:13:20Z", "lat": 34.05, "lon": -118.31, "depth_km": 8.7,
    "url": None, "nodal_planes": ((30.0, 70.0, -10.0),),
    # the PARSED event dict shape fetch_event_detail returns (products hoisted)
    "products": {"shakemap": [{"contents": {
        "download/extent.json": {"url": "https://example.com/extent.json"}}}]},
}


def _with_shakemap_detail(monkeypatch, event):
    monkeypatch.setattr(r1_comcat, "fetch_event_detail",
                        lambda ev, timeout_s=None: {"source": "usgs-comcat", "event": event,
                                                    "degraded": False, "error": None})
    monkeypatch.setattr(r2_shakemap, "_http_get_json",
                        lambda url, t: {"bbox": [-118.6, 33.8, -117.9, 34.4]})


def test_fetch_extent_normalised_with_coverage(monkeypatch):
    _with_shakemap_detail(monkeypatch, DETAIL_PARSED_EVENT)
    tile = [-118.4, 33.9, -118.2, 34.2]                   # fully inside the extent
    out = r2_shakemap.fetch_extent("us6000abcd", tile_bbox=tile)
    assert out["event_id"] == "us6000abcd"                # the "for None" bug is dead
    assert out["available"] is True and out["error"] is None
    assert out["bbox"] == [-118.6, 33.8, -117.9, 34.4]
    assert out["coverage"]["covers_tile"] is True and out["coverage"]["overlaps_tile"] is True


def test_fetch_extent_partial_overlap_and_outside(monkeypatch):
    _with_shakemap_detail(monkeypatch, DETAIL_PARSED_EVENT)
    overlapping = [-119.0, 34.3, -118.5, 34.6]            # clips the NE corner
    out = r2_shakemap.fetch_extent("us6000abcd", tile_bbox=overlapping)
    assert out["coverage"]["covers_tile"] is False and out["coverage"]["overlaps_tile"] is True
    outside = [0.0, 0.0, 1.0, 1.0]
    out = r2_shakemap.fetch_extent("us6000abcd", tile_bbox=outside)
    assert out["coverage"]["covers_tile"] is False and out["coverage"]["overlaps_tile"] is False
    # without a tile_bbox there is no coverage block at all
    out = r2_shakemap.fetch_extent("us6000abcd")
    assert out["coverage"] is None and out["event_id"] == "us6000abcd"


def test_fetch_extent_without_shakemap_is_a_note_with_event_id(monkeypatch):
    event = {**DETAIL_PARSED_EVENT, "products": {}}
    monkeypatch.setattr(r1_comcat, "fetch_event_detail",
                        lambda ev, timeout_s=None: {"event": event, "degraded": False, "error": None})
    out = r2_shakemap.fetch_extent("us6000abcd")
    assert out["event_id"] == "us6000abcd"
    assert out["available"] is False and out["degraded"] is False
    assert "no ShakeMap product" in out["note"]


def test_fetch_extent_detail_error_is_degraded_with_event_id(monkeypatch):
    monkeypatch.setattr(r1_comcat, "fetch_event_detail",
                        lambda ev, timeout_s=None: {"event": None, "degraded": True,
                                                    "error": "ComCat unreachable: x"})
    out = r2_shakemap.fetch_extent("us6000abcd")
    assert out["event_id"] == "us6000abcd" and out["available"] is False
    assert out["degraded"] is True and "ComCat" in out["error"]


# --------------------------------------------------------------------------- #
# live (opt-in): EQ_LIVE=1 python -m pytest -m live -q tests/test_adapters_r1_r2.py
# --------------------------------------------------------------------------- #
@pytest.mark.live
@pytest.mark.skipif(__import__("os").environ.get("EQ_LIVE") != "1",
                    reason="live USGS check — run with EQ_LIVE=1 (M3 marker)")
def test_live_comcat_query_detail_and_shakemap():
    out = r1_comcat.fetch_events((-118.4, 33.9, -118.2, 34.2), "2025-08-01T00:00:00Z",
                                 "2025-08-02T00:00:00Z", min_magnitude=2.0, limit=5)
    assert out["error"] is None, out["error"]
    assert isinstance(out["events"], list)
    detail = r1_comcat.fetch_event_detail("ci31445856")   # a stable historic id
    assert detail["error"] is None or "unreachable" in (detail["error"] or "")
    if detail.get("event"):
        ext = r2_shakemap.fetch_extent(detail["event"]["id"], tile_bbox=[-118.5, 33.8, -118.1, 34.2])
        assert {"event_id", "available", "bbox", "note", "degraded", "error"} <= set(ext)
