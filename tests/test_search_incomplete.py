"""B4 / A4: a short ASF/CMR listing is described precisely (product, counts, readable text), is remembered briefly so a
reload agrees with itself, and reaches the bundle as a flag the pipelines can act on. Network faked at the asf call only."""
from __future__ import annotations

import pytest

pytest.importorskip("asf_search")

from engine.retrieval.clients import nisar_client as nc  # noqa: E402

RAW = "CMR returned page of incomplete results.Expected 17 results, got 15."          # asf_search's own wording


def test_clean_warning_adds_the_missing_space_and_counts_are_parsed():
    assert nc.clean_warning(RAW) == "CMR returned page of incomplete results. Expected 17 results, got 15."
    assert nc.clean_warning("fine. Already spaced.") == "fine. Already spaced."
    assert nc.parse_shortfalls([nc.clean_warning(RAW), "unrelated"]) == [{"expected": 17, "got": 15}]
    assert nc.parse_shortfalls(None) == []


class _Row:
    def __init__(self, n):
        self.properties = {"fileName": f"G{n}.h5", "fileID": f"G{n}", "url": f"https://x/G{n}.h5", "processingLevel": "GUNW",
                           "startTime": f"2026-07-{n:02d}T00:00:00Z", "stopTime": f"2026-07-{n:02d}T00:00:30Z",
                           "pathNumber": 5, "frameNumber": 1, "flightDirection": "ASCENDING", "bytes": 10}
        self.geometry = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("NISAR_DATA_DIR", str(tmp_path))
    from engine.retrieval.settings import load_settings
    cfg = load_settings()
    c = nc.NisarClient.__new__(nc.NisarClient)
    c.cfg = cfg
    import threading
    from engine.retrieval.clients.cache import SearchCache
    c.searches = SearchCache(cfg.search_dir, 21600)
    c.partial = SearchCache(cfg.search_dir / "partial", 600)
    c.last_search_truncated = c.last_search_incomplete = False
    c.last_search_warnings, c.last_search_shortfalls = [], []
    monkeypatch.setattr(nc, "_ring", lambda r: None)
    monkeypatch.setattr(nc, "_size", lambda p: 10)
    monkeypatch.setitem(cfg.search, "retry_incomplete", 0)
    calls = {"n": 0}

    def fake(asf, kw, timeout_s, warnings=None):
        calls["n"] += 1
        if warnings is not None and calls["n"] == 1:
            warnings.append(RAW)                     # first ask: short page; later asks (fresh) are complete
        return [_Row(i) for i in range(1, 16 if calls["n"] == 1 else 18)]
    monkeypatch.setattr(nc.NisarClient, "_asf_search_bounded", staticmethod(fake))
    monkeypatch.setitem(__import__("sys").modules, "asf_search", type("M", (), {})())
    c._calls = calls
    return c


def test_a_short_listing_is_cleaned_counted_and_remembered_so_a_reload_agrees(client):
    bbox = (0.0, 0.0, 1.0, 1.0)
    first, _ = client.search(bbox, "2026-07-01", "2026-08-01", product="GUNW")
    assert len(first) == 15 and client.last_search_incomplete is True
    assert client.last_search_warnings == ["CMR returned page of incomplete results. Expected 17 results, got 15."]
    assert client.last_search_shortfalls == [{"expected": 17, "got": 15}]
    again, _ = client.search(bbox, "2026-07-01", "2026-08-01", product="GUNW")          # a reload
    assert [r.granule_id for r in again] == [r.granule_id for r in first]                # same list, no new ASF call
    assert client._calls["n"] == 1 and client.last_search_incomplete is True and client.last_search_shortfalls
    fresh, _ = client.search(bbox, "2026-07-01", "2026-08-01", product="GUNW", fresh=True)   # explicit re-ask
    assert len(fresh) == 17 and client.last_search_incomplete is False


def test_p01_flag_names_the_product_the_counts_and_what_is_unknown(tmp_path):
    """A real bundle build over a retriever that reports a short GUNW listing."""
    pytest.importorskip("pyproj")
    from engine.common import DirectoryAncillary, build_scene_bundle
    from tests.common_fixtures import FakeRetriever, default_tile, dt, make_scene, write_ancillary
    tile = default_tile()
    rtv = FakeRetriever(make_scene(tile, n_epochs=3, pairs=((0, 1), (1, 2)), event=False))
    rtv.last_search_incomplete = True
    rtv.last_search_warnings = ["CMR returned page of incomplete results. Expected 17 results, got 15."]
    rtv.last_search_shortfalls = [{"expected": 17, "got": 15}]
    b = build_scene_bundle(tile, rtv, as_of=dt("2026-08-10"), ancillary=DirectoryAncillary(write_ancillary(tmp_path, tile)))
    flags = [f for f in b.flags if f.code == "search_incomplete"]
    assert flags, [f.code for f in b.flags]
    f = flags[0]
    assert dict(f.detail)["expected"] == 34 or dict(f.detail)["expected"] == 17                  # per product the loop sees
    assert dict(f.detail)["product"] in ("GUNW", "GCOV") and dict(f.detail)["got"] is not None
    assert "results.Expected" not in f.message and "not reported by ASF" in f.message
    assert "lower bound" in f.message and "granule(s) missing" in f.message
