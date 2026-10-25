import shutil
import threading
import time

import h5py
import numpy as np
import pytest

from conftest import make_client, rm
from engine.retrieval.clients.decode import UnsupportedFilter, decode_chunk, unshuffle
from engine.retrieval.clients.nisar_client import drop_older_versions
from engine.retrieval.clients.range_io import coalesce
from engine.retrieval.clients.types import GranuleRef
from synth import DX, GRID, X0, Y0, lonlat_box

BOX = lonlat_box(300, 250, 640, 700)          # spans several chunks of every layer
H = f"{GRID}/unwrappedInterferogram/HH"
GK = "science__LSAR__GUNW__grids__frequencyA"
RK = "science__LSAR__GUNW__metadata__radarGrid"
K = "science__LSAR__GUNW__grids__frequencyA__unwrappedInterferogram__HH__"


# ----------------------------------------------------------------- pure units
def test_coalesce_merges_close_and_respects_caps():
    r = [(0, 10), (12, 10), (1000, 10), (1015, 5)]
    assert [(s, e, i) for s, e, i in coalesce(r, gap=5, max_req=10_000)] == [(0, 22, [0, 1]), (1000, 1020, [2, 3])]
    assert len(coalesce(r, gap=5, max_req=15)) == 4        # merging either pair would exceed max_req
    assert len(coalesce([(0, 10), (10, 10)], gap=0, max_req=15)) == 2
    assert len(coalesce([(0, 10), (10, 10)], gap=0, max_req=100)) == 1


def test_drop_older_versions():
    mk = lambda gid: GranuleRef(gid, "u", "GUNW")
    a = mk("NISAR_X_001_20260701T000000_20260701T000030_X05010_N_F_J_001")
    b = mk("NISAR_X_001_20260701T000000_20260701T000030_P05023_N_F_J_001")
    c = mk("NISAR_X_002_20260702T000000_20260702T000030_P05023_N_F_J_001")
    kept, dropped = drop_older_versions([a, b, c])
    assert dropped == 1 and kept == [b, c]


def test_unshuffle_roundtrip():
    a = np.random.rand(1000).astype("f4")
    shuffled = np.frombuffer(a.tobytes(), np.uint8).reshape(-1, 4).T.reshape(-1).tobytes()
    assert np.array_equal(np.frombuffer(unshuffle(shuffled, 4), "f4"), a)
    with pytest.raises(UnsupportedFilter):
        decode_chunk(b"x", [[32004, []]], 0, np.dtype("f4"), (1,))


# ----------------------------------------------------------------- end to end vs h5py
def _expected(served, gid_file, layer_path, clipgrid):
    ix0 = int(round((clipgrid.x[0] - X0) / DX)); iy0 = int(round((Y0 - clipgrid.y[0]) / DX))
    return ix0, iy0


def test_clip_matches_h5py_for_every_layer_kind(client, server, served, granule):
    res = client.fetch(BOX, granules=[granule])
    assert not res.errors and len(res.clips) == 1
    gc = res.clips[0].grids[GK]
    ix0, iy0 = _expected(None, None, None, gc)
    ny, nx = len(gc.y), len(gc.x)
    with h5py.File(next(served.glob("*.h5")), "r") as f:
        for name in ("unwrappedPhase", "coherenceMagnitude", "connectedComponents", "sparse", "rawplain",
                     "complexLayer", "contig"):
            got = gc.layers[K + name]
            want = f[f"{H}/{name}"][iy0:iy0 + ny, ix0:ix0 + nx]
            assert got.dtype == want.dtype and np.array_equal(got, want), name
    sp = gc.layers[K + "sparse"]
    assert (sp == -9999.0).any()                              # never-written chunks come back as the fill value
    assert client.counters["fallback_layers"] == 1            # only the contiguous layer needed h5py


def test_cube_grid_and_axis(client, granule, served):
    res = client.fetch(BOX, granules=[granule])
    g = res.clips[0].grids[RK]
    cube = g.layers["science__LSAR__GUNW__metadata__radarGrid__incidenceAngle"]
    assert cube.shape[0] == 3 and "heightAboveEllipsoid" in g.extras
    ix0 = int(round((g.x[0] - X0) / (4 * DX))); iy0 = int(round((Y0 - g.y[0]) / (4 * DX)))
    with h5py.File(next(served.glob("*.h5")), "r") as f:
        want = f["science/LSAR/GUNW/metadata/radarGrid/incidenceAngle"][:, iy0:iy0 + cube.shape[1], ix0:ix0 + cube.shape[2]]
    assert np.array_equal(cube, want)


def test_layer_filter_limits_what_is_read(client, granule):
    res = client.fetch(BOX, layers=r"unwrappedPhase", granules=[granule])
    keys = set().union(*(g.layers.keys() for g in res.clips[0].grids.values()))
    assert keys == {K + "unwrappedPhase"}


# ----------------------------------------------------------------- caches
def test_repeat_fetch_is_served_from_clip_cache_without_network(client, server, granule):
    a = client.fetch(BOX, granules=[granule])
    n = server.requests
    b = client.fetch(BOX, granules=[granule])
    assert server.requests == n and b.clips[0].source == "clip-cache"
    k = K + "unwrappedPhase"
    assert np.array_equal(a.clips[0].grids[GK].layers[k], b.clips[0].grids[GK].layers[k])


def test_new_bbox_reuses_chunks_and_index(tmp_path, server, granule):
    c1 = make_client(tmp_path)
    c1.fetch(BOX, layers="unwrappedPhase", granules=[granule])
    c1.close()
    # new process-like client: cold RAM, same disk. Different but overlapping box.
    c2 = make_client(tmp_path)
    server.reset()
    shifted = lonlat_box(320, 260, 620, 690)
    r = c2.fetch(shifted, layers="unwrappedPhase", granules=[granule])
    assert not r.errors
    assert c2.counters["index_disk"] == 1 and c2.counters["index_built"] == 0
    assert c2.counters["chunks_net"] == 0 and c2.counters["chunks_disk"] > 0
    assert server.requests <= 1                      # only the URL resolve probe, if anything
    c2.close()


def test_ram_tier_serves_repeat_reads(client, server, granule):
    client.fetch(BOX, layers="unwrappedPhase", granules=[granule])
    rm(client.clips.root)       # force re-assembly from chunk tiers
    server.reset()
    client.fetch(BOX, layers="unwrappedPhase", granules=[granule])
    assert client.counters["chunks_ram"] > 0 and server.requests == 0


def _hammer(c, granule, n=4):
    ts = [threading.Thread(target=lambda: c.fetch(BOX, layers="unwrappedPhase", granules=[granule])) for _ in range(n)]
    [t.start() for t in ts]; [t.join() for t in ts]


def test_singleflight_shares_identical_concurrent_requests(tmp_path, server, granule):
    server.latency = 0.03
    solo = make_client(tmp_path / "solo")
    _hammer(solo, granule, n=1)
    solo_reqs = server.requests
    solo.close()
    server.reset()
    many = make_client(tmp_path / "many")
    _hammer(many, granule, n=4)                       # 4 identical concurrent callers, cold caches
    assert many.counters["index_built"] == 1
    assert server.requests <= solo_reqs + 1           # not 4x
    many.close()


# ----------------------------------------------------------------- network behaviour
def test_resolve_once_and_no_auth_leak(client, server, granule):
    client.fetch(BOX, granules=[granule])
    assert server.data_hits <= 2 and not server.saw_auth_on_signed     # one resolve (+ at most a probe)
    assert server.requests > 5                                          # many ranges on the reused URL


def test_presigned_expiry_triggers_reresolve(client, server, granule):
    client.fetch(BOX, layers="unwrappedPhase", granules=[granule])
    rm(client.clips.root); rm(client.cfg.chunks_dir)
    client.ram._d.clear(); client.ram.size = 0
    server.sig = "v2"                                                   # old signed URL now 403s
    r = client.fetch(BOX, layers="unwrappedPhase", granules=[granule])
    assert not r.errors and server.data_hits >= 2


def test_transient_5xx_is_retried(client, server, granule):
    client.fetch(BOX, layers="unwrappedPhase", granules=[granule])      # builds index
    rm(client.clips.root); rm(client.cfg.chunks_dir)
    client.ram._d.clear(); client.ram.size = 0
    server.fail_next = 3
    r = client.fetch(BOX, layers="unwrappedPhase", granules=[granule])
    assert not r.errors and r.stats["retries"] >= 3


def test_stale_clip_served_when_network_down(tmp_path, server, granule):
    from conftest import make_client, rm
    c = make_client(tmp_path)
    c.cfg.cache["ttl_s"]["granule_clip"] = 0.05
    c.clips.ttl = 0.05
    c.fetch(BOX, layers="unwrappedPhase", granules=[granule])
    time.sleep(0.1)
    rm(c.cfg.chunks_dir); c.ram._d.clear(); c.ram.size = 0
    server.online = False
    c.io._resolved.clear()
    r = c.fetch(BOX, layers="unwrappedPhase", granules=[granule])
    assert r.degraded and r.clips and r.clips[0].source == "stale-clip-cache" and not r.errors
    c.close()


def test_failures_and_non_overlap_are_reported_not_swallowed(client, server, granule):
    far = (10.0, 10.0, 10.1, 10.1)
    r = client.fetch(far, granules=[granule])
    assert granule.granule_id in r.skipped and not r.clips
    bad = GranuleRef("nope", server.url("missing.h5"), "GUNW")
    r = client.fetch(BOX, granules=[bad])
    assert "nope" in r.errors


def test_chunk_tables_load_lazily_per_layer(tmp_path, server, granule):
    c = make_client(tmp_path)
    c.fetch(BOX, layers="unwrappedPhase", granules=[granule])
    assert c.counters["index_tables_layers"] == 1                  # not all ~10 layers
    ix = c._indexes[granule.granule_id]
    assert sum(L.loaded for L in ix.layers.values() if L.fast) == 1
    c.fetch(BOX, layers="coherenceMagnitude", granules=[granule])
    assert c.counters["index_tables_layers"] == 2
    c.close()
    c2 = make_client(tmp_path)                                      # persisted partial index is reused
    c2.fetch(BOX, layers="unwrappedPhase|coherenceMagnitude", granules=[granule])
    assert c2.counters["index_built"] == 0 and c2.counters["index_tables_layers"] == 0
    c2.close()


def _second_granule(server, served):
    from synth import make_granule
    name = "NISAR_L2_PR_GUNW_B_002_000_000_000_000_000_000_20260702T000000_20260702T000030_X05010_N_F_J_001.h5"
    if not (served / name).exists():
        make_granule(served / name, seed=7)
    return GranuleRef(name[:-3], server.url(name), "GUNW"), served / name


def test_catalog_makes_next_granule_targeted_and_correct(tmp_path, server, served, granule):
    c = make_client(tmp_path)
    c.fetch(BOX, layers="unwrappedPhase", granules=[granule])          # first of its type: full walk, learns catalog
    assert c.counters.get("index_targeted", 0) == 0 and (c.cfg.catalog_dirs[1] / "GUNW.json").exists()
    g2, path2 = _second_granule(server, served)
    r = c.fetch(BOX, layers="unwrappedPhase", granules=[g2])
    assert not r.errors and c.counters["index_targeted"] == 1
    ix = c._indexes[g2.granule_id]
    assert len(ix.layers) == 1                                          # only the requested layer was opened
    gc = r.clips[0].grids[GK]
    ix0 = int(round((gc.x[0] - X0) / DX)); iy0 = int(round((Y0 - gc.y[0]) / DX))
    with h5py.File(path2, "r") as f:
        want = f[f"{H}/unwrappedPhase"][iy0:iy0 + len(gc.y), ix0:ix0 + len(gc.x)]
    assert np.array_equal(gc.layers[K + "unwrappedPhase"], want)
    r = c.fetch(BOX, layers="coherenceMagnitude", granules=[g2])        # extends the partial index on demand
    assert not r.errors and len(c._indexes[g2.granule_id].layers) == 2
    c.close()


def test_stale_catalog_falls_back_to_full_walk(tmp_path, server, served, granule):
    import json
    c = make_client(tmp_path)
    d = c.cfg.catalog_dirs[1]; d.mkdir(parents=True, exist_ok=True)
    bad = {"v": 1, "grids": [{"path": "/no/such/grid", "projection": "/no/such/grid/projection", "extras": [], "layers": ["/no/such/grid/layer"]}]}
    (d / "GUNW.json").write_text(json.dumps(bad))
    r = c.fetch(BOX, layers="unwrappedPhase", granules=[granule])
    assert not r.errors and r.clips
    fresh = json.loads((d / "GUNW.json").read_text())
    assert fresh["grids"][0]["path"].startswith("/science/LSAR")        # relearned
    c.close()
