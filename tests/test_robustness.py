import multiprocessing as mp
import os
import threading
import time
from pathlib import Path

import h5py
import numpy as np
import pytest

from conftest import make_client
from engine.retrieval import checks
from engine.retrieval.clients import fsutil
from engine.retrieval.clients.cache import ClipStore, DiskChunkCache
from engine.retrieval.clients.types import GridClip
from synth import lonlat_box

BOX = lonlat_box(300, 250, 640, 700)


def test_replace_file_survives_transient_permission_errors(tmp_path, monkeypatch):
    real, calls = os.replace, {"n": 0}

    def flaky(a, b):
        calls["n"] += 1
        if calls["n"] <= 3:
            raise PermissionError(13, "in use by another process")
        return real(a, b)

    monkeypatch.setattr(os, "replace", flaky)
    src, dst = tmp_path / "a.tmp", tmp_path / "a"
    src.write_text("x")
    assert fsutil.replace_file(src, dst) and dst.read_text() == "x" and calls["n"] == 4


def test_replace_file_keep_existing_never_raises_when_destination_locked(tmp_path, monkeypatch):
    dst = tmp_path / "chunk"; dst.write_text("first")
    src = tmp_path / "chunk.tmp"; src.write_text("first")
    monkeypatch.setattr(os, "replace", lambda a, b: (_ for _ in ()).throw(PermissionError(32, "locked")))
    assert fsutil.replace_file(src, dst, keep_existing=True, delay=0) is True
    assert dst.read_text() == "first" and not src.exists()


def test_disk_chunk_cache_concurrent_identical_writes(tmp_path):
    c = DiskChunkCache(tmp_path / "c", 1 << 30)
    data = os.urandom(50_000)
    ts = [threading.Thread(target=lambda: [c.put("k", data) for _ in range(20)]) for _ in range(8)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert c.get("k") == data and not list((tmp_path / "c").rglob("*.tmp"))


def test_clip_store_long_names_roundtrip_and_short_paths(tmp_path):
    long_layer = "science__LSAR__GUNW__grids__frequencyA__unwrappedInterferogram__HH__" + "ionospherePhaseScreenUncertainty" * 3
    long_grid = "science__LSAR__GUNW__grids__frequencyA__" + "veryLongGridGroupName" * 3
    g = GridClip(long_grid, 32611, np.arange(20.0), np.arange(10.0), {"heightAboveEllipsoid": np.arange(3.0)},
                 {long_layer: np.random.rand(10, 20).astype("f4"), long_layer + "_2": np.random.rand(3, 10, 20).astype("f4")})
    cs = ClipStore(tmp_path / "clips", None)
    k = cs.key("G", (1, 2, 3, 4), None)
    cs.put(k, {long_grid: g}, {"granule": "G"})
    longest = max(len(str(p.relative_to(tmp_path))) for p in (tmp_path / "clips").rglob("*"))
    assert longest < 120, longest                                   # leaves >140 chars for the user's own folder depth
    from engine.retrieval.clients.nisar_client import NisarClient
    root, _, _ = cs.get(k)
    clip = NisarClient._from_zarr(None, (1, 2, 3, 4), root, "clip-cache")
    got = clip.grids[long_grid]
    assert set(got.layers) == set(g.layers) and set(got.extras) == {"heightAboveEllipsoid"}
    assert all(np.array_equal(got.layers[k_], g.layers[k_]) for k_ in g.layers)


def test_concurrent_processes_share_one_data_dir_without_corruption(tmp_path, server, served, granule):
    """Spawned processes (the Windows default) race to write the same chunks/clips into one data dir."""
    data = tmp_path / "shared"
    seed = make_client(tmp_path / "seed")
    seed.fetch(BOX, layers="unwrappedPhase|coherenceMagnitude", granules=[granule])      # builds index + catalog once
    import shutil
    for sub in ("layout", "catalog"):
        shutil.copytree(seed.cfg.data_dir / "cache" / sub, data / "cache" / sub)
    truth_hash = checks.hash_clip(seed.fetch(BOX, layers="unwrappedPhase|coherenceMagnitude", granules=[granule]))
    seed.close()
    args = {"data_dir": str(data), "ref": granule.to_dict(), "bbox": list(BOX), "layers": "unwrappedPhase|coherenceMagnitude",
            "selftest": True}
    server.latency = 0.02
    with mp.get_context("spawn").Pool(4) as pool:
        outs = pool.map_async(checks.fetch_worker, [args] * 4).get(timeout=180)
    assert all(not o["errors"] and o["clips"] == 1 for o in outs), outs
    assert {o["hash"] for o in outs} == {truth_hash}
    assert not list(data.rglob("*.tmp*"))                                           # no leftover temp files
    c = make_client(tmp_path / "x"); c.cfg.data_dir = data
    assert c.counters["chunks_net"] == 0
    c.close()


def test_verify_clip_works_when_result_came_from_a_cache(tmp_path, server, served, granule):
    """The crash seen on a re-run: second run is served from the clip cache, so the client never opened the index."""
    c1 = make_client(tmp_path)
    c1.fetch(BOX, layers="unwrappedPhase|coherenceMagnitude", granules=[granule])
    c1.close()
    c2 = make_client(tmp_path)                                   # same data dir, brand-new process-like client
    res = c2.fetch(BOX, layers="unwrappedPhase|coherenceMagnitude", granules=[granule])
    assert res.clips[0].source == "clip-cache" and granule.granule_id not in c2._indexes
    ver = checks.verify_clip(c2, granule, res)
    assert ver and all(same for _, same, *_ in ver)


# ------------------------------------------------------------------ close() semantics (this used to hang forever)
def _within(seconds, fn):
    """Run fn in a thread; fail (instead of hanging the whole suite) if it does not finish."""
    box = {}

    def target():
        try:
            box["r"] = fn()
        except BaseException as ex:
            box["e"] = ex
    t = threading.Thread(target=target, daemon=True)
    t.start(); t.join(seconds)
    assert not t.is_alive(), f"call did not return within {seconds}s (hung)"
    if "e" in box:
        raise box["e"]
    return box.get("r")


def test_close_is_synchronous_releases_everything_and_use_after_close_fails_fast(tmp_path, server, granule):
    c = make_client(tmp_path)
    c.fetch(BOX, layers="unwrappedPhase", granules=[granule])
    c._fallback_file(granule)                                       # make sure an h5py handle is open
    assert c._fb
    c.close()
    assert c.io.lt.loop.is_closed() and not c.io.lt._t.is_alive()   # loop closed, thread gone, on return
    assert not c._fb and not c._indexes                             # fallback handles released
    with pytest.raises(RuntimeError):
        _within(10, lambda: c.fetch(BOX, layers="unwrappedPhase", granules=[granule]))
    with pytest.raises(RuntimeError):
        _within(10, lambda: c.io.lt.run(c.io.resolve(granule.url)))
    c.close()                                                       # idempotent


def test_use_after_close_never_hangs_even_when_racing_the_shutdown(tmp_path, server, granule):
    for _ in range(25):
        c = make_client(tmp_path)
        t = threading.Thread(target=c.close)
        t.start()
        try:
            _within(10, lambda: c.fetch(BOX, layers="unwrappedPhase", granules=[granule]))
        except RuntimeError:
            pass                                                    # failing fast is the contract; hanging is the bug
        t.join(10)
        assert not t.is_alive()


def test_closing_while_a_fetch_is_in_flight_releases_the_waiter(tmp_path, server, granule):
    server.latency = 1.0
    c = make_client(tmp_path)
    err = {}

    def work():
        try:
            c.fetch(BOX, layers="unwrappedPhase", granules=[granule])
        except BaseException as ex:
            err["e"] = ex
    t = threading.Thread(target=work, daemon=True)
    t.start()
    time.sleep(0.4)
    c.close()
    t.join(15)
    assert not t.is_alive(), "the in-flight fetch hung after close()"
