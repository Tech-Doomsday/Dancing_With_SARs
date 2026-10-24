"""Helpers shared by tools/final_check.py, the probe and the multiprocess tests."""
from __future__ import annotations

import hashlib
import os
import time

import numpy as np


def hash_clip(res) -> str:
    """Order-independent fingerprint of every array in a FetchResult."""
    h = hashlib.sha1()
    for clip in sorted(res.clips, key=lambda c: c.granule.granule_id):
        for gk in sorted(clip.grids):
            g = clip.grids[gk]
            h.update(gk.encode()); h.update(np.ascontiguousarray(g.x).tobytes()); h.update(np.ascontiguousarray(g.y).tobytes())
            for lk in sorted(g.layers):
                a = np.ascontiguousarray(g.layers[lk])
                h.update(lk.encode()); h.update(str(a.shape).encode()); h.update(str(a.dtype).encode()); h.update(a.tobytes())
    return h.hexdigest()


def valid_fraction(arr):
    if arr.dtype.kind == "f":
        return float(np.mean(np.isfinite(arr) & (arr != 0)))
    return None


def verify_clip(client, granule, res) -> list[tuple[str, bool, tuple, str]]:
    """Compare every array in `res` with plain h5py reading the same pixel window of the same file.
    Returns [(layer key, identical?, shape, dtype)]."""
    ix = client._indexes[granule.granule_id]
    h5 = client._fallback_file(granule)
    out = []
    for clip in res.clips:
        for gr in clip.grids.values():
            gl = next(v for v in ix.grids.values() if v.key == gr.grid)
            x0 = int(np.nonzero(gl.x == gr.x[0])[0][0])
            y0 = int(np.nonzero(gl.y == gr.y[0])[0][0])
            for lk, arr in gr.layers.items():
                L = next(v for v in ix.layers.values() if v.key == lk)
                with client._fb_lock:
                    d = h5[L.path]
                    ref = d[(slice(None),) * (d.ndim - 2) + (slice(y0, y0 + arr.shape[-2]), slice(x0, x0 + arr.shape[-1]))]
                same = (arr.shape == ref.shape and arr.dtype == ref.dtype and
                        (np.array_equal(arr, ref, equal_nan=True) if arr.dtype.kind in "fc" else np.array_equal(arr, ref)))
                out.append((lk, bool(same), tuple(arr.shape), str(arr.dtype)))
    return out


def fetch_worker(a: dict) -> dict:
    """Runs in a spawned process: fresh client on a shared data dir, fetch one tile / bbox."""
    os.environ["NISAR_DATA_DIR"] = a["data_dir"]
    from .auth.secrets import EarthdataAuth
    from .clients.nisar_client import NisarClient
    from .clients.types import GranuleRef
    from .settings import load_settings

    cfg = load_settings()
    auth = None
    if a.get("selftest"):
        cfg.net.update(auth_hosts=["127.0.0.1"], retries=3, connect_timeout_s=5, stall_timeout_s=10)
        auth = EarthdataAuth(token="test")
    c = NisarClient(cfg, auth)
    g = GranuleRef(**a["ref"])
    t = time.perf_counter()
    try:
        if a.get("tile"):
            res = c.fetch_tile(a["tile"], product=g.product, layers=a["layers"], granules=[g])
        else:
            res = c.fetch(tuple(a["bbox"]), product=g.product, layers=a["layers"], granules=[g])
        out = {"errors": res.errors, "hash": hash_clip(res) if res.clips else None, "clips": len(res.clips),
               "seconds": time.perf_counter() - t, "net_chunks": c.counters["chunks_net"]}
    finally:
        c.close()
    return out
