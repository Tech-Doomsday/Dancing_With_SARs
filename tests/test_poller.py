import threading
from datetime import datetime, timezone

import h5py
import numpy as np
import pytest

from conftest import make_client
from engine.retrieval.clients.nisar_client import scene_key
from engine.retrieval.clients.types import GranuleRef
from engine.retrieval.scheduler.granule_poller import GranulePoller
from engine.retrieval.state import State
from engine.tiling.mgrs import tile_at, tile_from_id
from synth import DX, GRID, X0, Y0, lonlat_box

NOW = datetime(2026, 10, 24, 6, 0, tzinfo=timezone.utc)


def ring(w, s, e, n):
    return [[w, s], [e, s], [e, n], [w, n], [w, s]]


def gid(frame, version="P05023", day="20261008"):
    return f"NISAR_L2_PR_GCOV_033_034_A_{frame:03d}_4005_DHDH_A_{day}T130823_{day}T130857_{version}_N_F_J_001"


def ref(frame, fp, version="P05023", product="GCOV", day="20261008"):
    return GranuleRef(gid(frame, version, day), f"https://x/{frame}.h5", product, f"{day}T130823Z", f"{day}T130857Z",
                      footprint=fp)


class Fake:
    def __init__(self, by_product):
        self.by_product, self.calls, self.fail = by_product, [], set()

    def __call__(self, bbox, t0, t1, product):
        self.calls.append((bbox, t0, t1, product))
        if product in self.fail:
            raise ConnectionError("asf down")
        return list(self.by_product.get(product, [])), False


@pytest.fixture()
def poller(tmp_path):
    c = make_client(tmp_path)
    c.cfg.poll.update(products=["GCOV", "GUNW"], lookback_days=30, region=None)
    fake = Fake({"GCOV": [ref(19, ring(-118.6, 34.0, -118.4, 34.2)), ref(20, ring(-118.3, 34.0, -118.2, 34.1))]})
    p = GranulePoller(c, searcher=fake)
    p.fake = fake
    yield p
    c.close()


def test_scene_key_ignores_only_the_processing_version():
    k1, v1 = scene_key(gid(19, "P05023")); k2, v2 = scene_key(gid(19, "X05010")); k3, _ = scene_key(gid(20))
    assert k1 == k2 and k1 != k3 and v1 == 5023 and v2 == 5010


def test_first_poll_queues_every_tile_of_every_footprint(poller):
    r = poller.poll(NOW)
    assert len(r.new) == 2 and not r.errors and not r.degraded
    box1 = {t for t in r.dirty if tile_from_id(t).bbox()[0] < -118.4 and tile_from_id(t).bbox()[2] > -118.6}
    assert box1 and all(len(v) >= 1 for v in r.dirty.values())
    assert sum(poller.state.counts().values()) == sum(len(v) for v in r.dirty.values())
    bb, t0, t1, prod = poller.fake.calls[0]
    assert bb is None and t0.startswith("2026-09-24") and t1.startswith("2026-10-24")


def test_second_poll_finds_nothing_new(poller):
    poller.poll(NOW)
    before = poller.state.counts()
    r = poller.poll(NOW)
    assert r.new == [] and r.dirty == {} and poller.state.counts() == before


def test_new_processing_version_supersedes_and_dequeues_old(poller):
    poller.poll(NOW)
    newer = ref(19, ring(-118.6, 34.0, -118.4, 34.2), version="P05100")
    poller.fake.by_product["GCOV"].append(newer)
    r = poller.poll(NOW)
    assert r.superseded == [(gid(19, "P05023"), newer.granule_id)]
    c = poller.state.counts()
    assert c.get("superseded", 0) > 0 and c.get("pending", 0) > 0
    claimed = poller.state.claim(1000)
    assert gid(19, "P05023") not in {x["granule_id"] for x in claimed}      # old version no longer queued


def test_late_arriving_older_version_is_recorded_but_not_queued(poller):
    poller.fake.by_product["GCOV"] = [ref(19, ring(-118.6, 34.0, -118.4, 34.2), version="P05100")]
    poller.poll(NOW)
    poller.fake.by_product["GCOV"].append(ref(19, ring(-118.6, 34.0, -118.4, 34.2), version="P05023"))
    r = poller.poll(NOW)
    assert len(r.new) == 1 and r.dirty == {}


def test_region_limits_queued_tiles(poller):
    poller.cfg.poll["region"] = [-118.55, 34.05, -118.45, 34.15]
    r = poller.poll(NOW)
    assert 0 < len(r.dirty) < 12
    for t in r.dirty:
        w, s, e, n = tile_from_id(t).bbox()
        assert e > -118.56 and w < -118.44 and n > 34.04 and s < 34.16
    assert poller.fake.calls[0][0] == (-118.55, 34.05, -118.45, 34.15)


def test_asf_failure_is_reported_and_other_products_continue(poller):
    poller.fake.fail = {"GCOV"}
    poller.fake.by_product["GUNW"] = [ref(5, ring(-118.6, 34.0, -118.5, 34.1), product="GUNW")]
    r = poller.poll(NOW)
    assert "GCOV" in r.errors and len(r.new) == 1


def test_granule_without_footprint_is_flagged_not_dropped(poller):
    poller.fake.by_product["GCOV"] = [ref(33, None)]
    r = poller.poll(NOW)
    assert len(r.new) == 1 and r.no_footprint == [gid(33)] and r.dirty == {}


def test_claim_is_exclusive_across_concurrent_workers(poller):
    poller.poll(NOW)
    total = sum(poller.state.counts().values())
    got, lock = [], threading.Lock()

    def worker(i):
        s = State(poller.state.path)
        while True:
            items = s.claim(7, f"w{i}")
            if not items:
                return
            with lock:
                got.extend((x["tile"], x["granule_id"]) for x in items)

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert len(got) == total and len(set(got)) == total                     # nothing twice, nothing lost


def test_complete_failed_and_stale_requeue(poller):
    poller.poll(NOW)
    it = poller.state.claim(1)[0]
    poller.state.complete(it["tile"], it["granule_id"], ok=False)
    assert poller.state.counts().get("pending", 0) == sum(poller.state.counts().values())
    it = poller.state.claim(1)[0]
    assert poller.state.requeue_stale(older_than_s=-1) == 1


# ------------------------------------------------------------------ tile -> fetch end to end
def test_fetch_tile_returns_the_tile_window_matching_h5py(tmp_path, server, served, granule):
    c = make_client(tmp_path)
    lon, lat = lonlat_box(500, 500, 501, 501)[:2]
    tile = tile_at(lon, lat, 10_000)
    r = c.fetch_tile(tile, layers=r"unwrappedPhase", granules=[granule])
    assert not r.errors and r.clips
    gc = r.clips[0].grids["science__LSAR__GUNW__grids__frequencyA"]
    # 10 km tile + 300 m margin each side = ~10.6 km / 80 m = ~133 px (+ the 1-pixel window margin)
    assert 130 <= len(gc.x) <= 140 and 130 <= len(gc.y) <= 140
    ix0 = int(round((gc.x[0] - X0) / DX)); iy0 = int(round((Y0 - gc.y[0]) / DX))
    with h5py.File(next(served.glob("*.h5")), "r") as f:
        want = f[f"{GRID}/unwrappedInterferogram/HH/unwrappedPhase"][iy0:iy0 + len(gc.y), ix0:ix0 + len(gc.x)]
    assert np.array_equal(gc.layers["science__LSAR__GUNW__grids__frequencyA__unwrappedInterferogram__HH__unwrappedPhase"], want)
    r2 = c.fetch_tile(tile.id, layers=r"unwrappedPhase", granules=[granule])      # id string works too
    assert r2.clips[0].source == "clip-cache"
    c.close()
