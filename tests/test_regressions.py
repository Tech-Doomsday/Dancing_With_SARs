"""Regression tests for bugs found in the step 1-6 review (each one failed before the fix)."""
import asyncio
import gc
import threading
import time
from pathlib import Path

import h5py
import pytest

from conftest import make_client
from synth import lonlat_box

BOX = lonlat_box(300, 250, 640, 700)


# ------------------------------------------------------------------ transient errors while resolving the URL
def test_transient_503_on_the_resolve_probe_is_retried(tmp_path, server, granule):
    """The very first request of every granule is the redirect-resolve probe. One 503/429 there used to fail
    the whole granule (only range reads were retried)."""
    c = make_client(tmp_path)
    try:
        server.fail_next = 2                              # first two /signed hits = resolve probe attempts
        r = c.fetch(BOX, layers="unwrappedPhase", granules=[granule])
        assert not r.errors and r.clips
        assert c.io.metrics.snapshot()["retries"] >= 2
    finally:
        c.close()


def test_resolve_gives_up_after_the_retry_budget_with_a_clear_error(tmp_path, server, granule):
    c = make_client(tmp_path)
    try:
        server.fail_next = 10_000
        r = c.fetch(BOX, layers="unwrappedPhase", granules=[granule])
        assert granule.granule_id in r.errors and "gave up resolving" in r.errors[granule.granule_id]
    finally:
        c.close()


# ------------------------------------------------------------------ deadlock: h5py lock vs. event-loop thread
def test_index_build_survives_h5py_objects_being_finalised_on_the_loop_thread(tmp_path, server, served, granule):
    """h5py holds a global lock for the whole duration of a call; RangeFile.read() runs inside h5py. If that read
    waits on the shared loop thread and the loop thread meanwhile finalises ANY h5py object (cyclic GC runs on
    whichever thread allocates), the loop blocks on h5py's lock while the reader holds it -> permanent hang."""
    path = next(served.glob("*.h5"))
    c = make_client(tmp_path)
    server.latency = 0.01
    stop = threading.Event()

    async def churn():                                    # runs on the loop thread
        while not stop.is_set():
            for _ in range(5):
                f = h5py.File(str(path), "r")
                cell = [f, f["science"]]
                cell.append(cell)                         # cycle: only the cyclic GC can free it
                del f, cell
            gc.collect()
            await asyncio.sleep(0)

    asyncio.run_coroutine_threadsafe(churn(), c.io.lt.loop)
    out = {}

    def work():
        out["r"] = c.fetch(BOX, layers=None, granules=[granule])

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(90)
    stop.set()
    assert not t.is_alive(), "fetch deadlocked (index build vs. loop-thread GC)"
    assert not out["r"].errors and out["r"].clips
    c.close()


def test_close_releases_loops_and_fallback_handles(tmp_path, server, granule):
    c = make_client(tmp_path)
    c.fetch(BOX, granules=[granule])                      # all layers incl. the contiguous one -> h5py fallback handle
    inline = list(c.io._inline_ios)
    assert c._fb and inline
    c.close()
    assert not c._fb and c.io.lt.loop.is_closed() and all(i.lt.loop.is_closed() for i in inline)


# ------------------------------------------------------------------ secrets
def test_credentials_never_appear_in_repr():
    from engine.retrieval.auth.secrets import EarthdataAuth
    a = EarthdataAuth(token="SECRET-TOKEN", username="user", password="SECRET-PW")
    assert "SECRET" not in repr(a) and "SECRET" not in str(a)
    assert a.header()["Authorization"] == "Bearer SECRET-TOKEN"


# ------------------------------------------------------------------ clip store: concurrent writers
def _clip_dir(path: Path, text: str):
    path.mkdir(parents=True)
    (path / "meta.json").write_text("{}")
    (path / "data.bin").write_text(text)


def test_replace_dir_keeps_the_complete_clip_another_writer_finished_first(tmp_path):
    from engine.retrieval.clients.fsutil import replace_dir
    dst, src = tmp_path / "clip", tmp_path / "clip.tmp"
    _clip_dir(dst, "first")
    _clip_dir(src, "second")
    assert replace_dir(src, dst, keep_existing=True)
    assert (dst / "data.bin").read_text() == "first" and not src.exists()


def test_replace_dir_refresh_invalidates_meta_before_deleting(tmp_path, monkeypatch):
    """Refreshing an expired clip must make it look absent (no meta.json) BEFORE its chunk files go, otherwise a
    concurrent reader sees a half-deleted zarr store, which silently reads as fill values (zeros)."""
    import shutil
    from engine.retrieval.clients import fsutil
    dst, src = tmp_path / "clip", tmp_path / "clip.tmp"
    _clip_dir(dst, "old")
    _clip_dir(src, "new")
    seen = []
    real = shutil.rmtree
    monkeypatch.setattr(fsutil.shutil, "rmtree",
                        lambda p, *a, **k: (seen.append((Path(p) == dst, (dst / "meta.json").exists())), real(p, *a, **k))[1])
    assert fsutil.replace_dir(src, dst, keep_existing=False)
    assert (dst / "data.bin").read_text() == "new"
    assert (True, False) in seen                           # when dst was being deleted, meta.json was already gone


# ------------------------------------------------------------------ search truncation is no longer silent
def test_search_that_hits_max_results_is_flagged(tmp_path, monkeypatch, caplog):
    import asf_search
    from engine.retrieval.clients.nisar_client import NisarClient

    class Fake:
        def __init__(self, i):
            self.properties = {"fileName": f"NISAR_L2_PR_GUNW_A_{i:03d}_000_000_000_000_000_000_20260701T000000_20260701T000030_X05010_N_F_J_001.h5",
                               "url": f"https://example.invalid/{i}.h5", "processingLevel": "GUNW"}
            self.geometry = {"coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}

    monkeypatch.setattr(asf_search, "search", lambda **kw: [Fake(i) for i in range(kw["maxResults"])])
    c = make_client(tmp_path)
    try:
        with caplog.at_level("WARNING", logger="nisar.retrieval"):
            refs, _ = c.search((0, 0, 1, 1), "2026-01-01", "2026-02-01", "GUNW", max_results=7, fresh=True)
        assert len(refs) == 7 and c.last_search_truncated and "TRUNCATED" in caplog.text
        monkeypatch.setattr(asf_search, "search", lambda **kw: [Fake(i) for i in range(3)])
        c.search((0, 0, 1, 1), "2026-01-01", "2026-02-01", "GUNW", max_results=7, fresh=True)
        assert not c.last_search_truncated
    finally:
        c.close()


# ------------------------------------------------------------------ hermetic tests
def test_unit_tests_ignore_a_shipped_catalog(tmp_path):
    c = make_client(tmp_path)
    try:
        assert not any(str(d).startswith(str(Path(__file__).resolve().parents[1] / "config")) for d in c.cfg.catalog_dirs)
    finally:
        c.close()
