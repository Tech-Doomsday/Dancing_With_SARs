"""Sample data for `debug_view.py --demo`. Stdlib only (no numpy/h5py/etc): builds a throwaway
data dir with a realistic-looking state.sqlite (granules, a worked tile queue, poll history)
plus a few dummy cache files, using engine.retrieval.state.State's own public methods so the
shape matches exactly what the real poller would produce.
"""
from __future__ import annotations

import os
import random
import re
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.retrieval.state import State  # noqa: E402

PRODUCTS = ["GCOV", "GUNW"]
BANDS, COLS = "STUVWX", "ABCDEFGHJKLMNPQRSTUVWXYZ"

# mirrors engine.retrieval.clients.nisar_client.scene_key, reimplemented here so this demo
# generator never has to import nisar_client.py (which pulls in aiohttp/h5py/zarr/etc.)
_VERSION_TAIL = re.compile(r"_([A-Z])(\d{5})(_[A-Z]_[A-Z]_[A-Z]_\d{3})$")


def scene_key(granule_id: str) -> tuple[str, int]:
    m = _VERSION_TAIL.search(granule_id)
    return (granule_id[:m.start()] + m.group(3), int(m.group(2))) if m else (granule_id, 0)


def _tile_id(rng: random.Random) -> str:
    zone = rng.randint(10, 13)
    band = rng.choice(BANDS)
    sq = rng.choice(COLS) + rng.choice(COLS)
    col, row = rng.randint(0, 9), rng.randint(0, 9)
    return f"{zone}{band}{sq}{col}{row}"


def _granule_id(product: str, track: int, frame: int, start: time.struct_time, version: int) -> str:
    ts = time.strftime("%Y%m%dT%H%M%S", start)
    return (f"NISAR_L2_PR_{product}_{track:03d}_{frame:03d}_000_000_000_000_000_"
            f"{ts}_{ts}_X{version:05d}_N_F_J_001")


def make_demo_dir() -> Path:
    rng = random.Random(7)
    data_dir = Path(tempfile.mkdtemp(prefix="retrieval_debug_demo_"))
    state = State(data_dir / "state" / "retrieval.sqlite")
    now = time.time()

    all_tiles: dict[str, list[str]] = {}       # granule_id -> tiles, for later queue work
    poll_log: list[tuple] = []                 # (product, ts, found, new, error)

    # six 6-hourly polls per product over the last ~36h, each finding a few new granules
    for product in PRODUCTS:
        track_pool = [rng.randint(10, 150) for _ in range(3)]
        scenes: list[tuple[int, int, time.struct_time, int]] = []  # (track, frame, start, version)
        for poll_i in range(6, -1, -1):
            poll_ts = now - poll_i * 6 * 3600
            n_new, n_found = 0, rng.randint(2, 5)
            for _ in range(n_found):
                # occasionally reprocess an already-seen scene (same track/frame/start, version+1)
                # instead of always minting a brand new one, so supersession actually shows up
                if scenes and poll_i < 6 and rng.random() < 0.2:
                    track, frame, start_struct, old_version = rng.choice(scenes)
                    version = old_version + 1
                else:
                    track = rng.choice(track_pool)
                    frame = rng.randint(1, 20)
                    start_struct = time.gmtime(poll_ts - rng.randint(0, 3 * 3600))
                    version = 1
                gid = _granule_id(product, track, frame, start_struct, version)
                if state.seen(gid):
                    continue
                ref = SimpleNamespace(
                    granule_id=gid, product=product,
                    start=time.strftime("%Y-%m-%dT%H:%M:%SZ", start_struct),
                    stop=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.mktime(start_struct) + 30)),
                    url=f"https://datapool.asf.alaska.edu/{product}/NISAR/{gid}.h5",
                    footprint=None,
                )
                key, ver = scene_key(gid)
                tiles = [_tile_id(rng) for _ in range(rng.randint(1, 4))]
                state.add_granule(ref, key, ver, tiles, poll_ts)
                all_tiles[gid] = tiles
                scenes.append((track, frame, start_struct, version))
                n_new += 1
            error = None
            if product == "GUNW" and poll_i == 3:
                error = "TimeoutError: ASF search timed out after 20s"
                n_found = n_new = 0
            if error is None:
                state.log_poll(product, n_found, n_new)
            else:
                state.log_poll(product, 0, 0, error)
            # log_poll stamps its own time.time(); backdate it directly for a believable history
            poll_log.append((product, poll_ts, n_found, n_new, error))

    _backdate_poll_log(state, poll_log)

    # work some of the queue: a couple of workers claim batches, most complete, a few don't
    # (left 'claimed' to show in-flight work; State.requeue_stale would normally recycle these)
    for worker in ("worker-a", "worker-b"):
        claimed = state.claim(12, worker=worker)
        for i, item in enumerate(claimed):
            if i % 5 == 4:
                continue  # leave ~1 in 5 claimed/in-flight
            ok = not (i % 11 == 0)  # the rare failure goes back to pending
            state.complete(item["tile"], item["granule_id"], ok=ok)

    _fake_cache_files(data_dir, rng)
    return data_dir


def _backdate_poll_log(state: State, entries: list[tuple]) -> None:
    import sqlite3
    with sqlite3.connect(state.path) as c:
        c.execute("DELETE FROM poll_log")
        c.executemany("INSERT INTO poll_log VALUES(?,?,?,?,?)",
                      [(ts, product, found, new, error) for product, ts, found, new, error in entries])


def _fake_cache_files(data_dir: Path, rng: random.Random) -> None:
    for sub, n, size in (("cache/chunks", 40, 4096), ("clips", 15, 65536),
                         ("cache/search", 6, 2048), ("cache/layout", 10, 8192)):
        d = data_dir / sub
        d.mkdir(parents=True, exist_ok=True)
        for i in range(n):
            (d / f"f{i:03d}.bin").write_bytes(os.urandom(rng.randint(size // 2, size)))


if __name__ == "__main__":
    print(make_demo_dir())
