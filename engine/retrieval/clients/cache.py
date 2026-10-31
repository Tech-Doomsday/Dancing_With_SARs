"""Tiered cache pieces: decoded-chunk RAM LRU, compressed-chunk disk cache (LRU by mtime),
Zarr clip store with TTL / stale-while-error, TTL'd search cache."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
from collections import OrderedDict
from pathlib import Path

import numpy as np

from .fsutil import replace_dir, replace_file


def sha(*parts) -> str:
    return hashlib.sha1("\x1f".join(str(p) for p in parts).encode()).hexdigest()


# ------------------------------------------------------------------ RAM
class ByteLRU:
    """Thread-safe LRU bounded by array bytes. Values are stored read-only."""

    def __init__(self, cap_bytes: int):
        self.cap, self.size = cap_bytes, 0
        self._d: OrderedDict[str, np.ndarray] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = self.misses = 0

    def get(self, k):
        with self._lock:
            v = self._d.get(k)
            if v is None:
                self.misses += 1
                return None
            self._d.move_to_end(k)
            self.hits += 1
            return v

    def put(self, k, v: np.ndarray):
        if v.nbytes > self.cap:
            return
        v.flags.writeable = False
        with self._lock:
            old = self._d.pop(k, None)
            if old is not None:
                self.size -= old.nbytes
            self._d[k] = v
            self.size += v.nbytes
            while self.size > self.cap and self._d:
                _, ev = self._d.popitem(last=False)
                self.size -= ev.nbytes


# ------------------------------------------------------------------ disk: compressed chunks
class DiskChunkCache:
    """Content = the exact compressed bytes stored in the granule. Keys embed the granule id
    (which carries the processing version), so entries are immutable and never need TTLs."""

    def __init__(self, root: Path, cap_bytes: int):
        self.root, self.cap = Path(root), cap_bytes
        self._bytes: int | None = None
        self._lock = threading.Lock()
        self.hits = self.misses = 0

    def _p(self, key: str) -> Path:
        h = sha(key)
        return self.root / h[:2] / h[2:]

    def get(self, key: str) -> bytes | None:
        p = self._p(key)
        try:
            data = p.read_bytes()
        except OSError:
            self.misses += 1
            return None
        try:
            os.utime(p)            # keep LRU order honest
        except OSError:
            pass
        self.hits += 1
        return data

    def put(self, key: str, data) -> None:
        p = self._p(key)
        if p.exists():                         # immutable content: first writer wins
            return
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_bytes(data)
        replace_file(tmp, p, keep_existing=True)
        with self._lock:
            if self._bytes is None:
                self._bytes = self._scan()
            else:
                self._bytes += len(data)
            over = self._bytes > self.cap
        if over:
            self.trim()

    def _scan(self) -> int:
        return sum(f.stat().st_size for f in self.root.rglob("*") if f.is_file() and not f.name.endswith(".tmp"))

    def trim(self, keep: float = 0.85):
        with self._lock:
            files = [(f.stat().st_mtime, f.stat().st_size, f) for f in self.root.rglob("*")
                     if f.is_file() and not f.name.endswith(".tmp")]
            total = sum(s for _, s, _ in files)
            for _, s, f in sorted(files):
                if total <= self.cap * keep:
                    break
                try:
                    f.unlink()
                    total -= s
                except OSError:
                    pass
            self._bytes = total


# ------------------------------------------------------------------ disk: decoded clips (Zarr)
class ClipStore:
    """One Zarr group per (granule, bbox, layers). Written to a temp dir then renamed, so a crash
    never leaves a half clip under the real name."""

    def __init__(self, root: Path, ttl_s: float | None):
        self.root, self.ttl = Path(root), ttl_s

    def key(self, granule_id: str, bbox, layers: list[str] | None, schema: int = 2) -> str:
        bb = ",".join(f"{v:.6f}" for v in bbox)
        return sha("clip", schema, granule_id, bb, ",".join(sorted(layers or ["*"])))

    def _dir(self, k: str) -> Path:
        return self.root / k[:2] / k

    def get(self, k: str, allow_stale: bool = False):
        """-> (arrays_by_grid, meta, fresh:bool) or None."""
        import zarr
        d = self._dir(k)
        mf = d / "meta.json"
        if not mf.exists():
            return None
        try:
            meta = json.loads(mf.read_text())
            fresh = self.ttl is None or (time.time() - meta["created"]) < self.ttl
            if not fresh and not allow_stale:
                return None
            root = zarr.open_group(str(d / "data.zarr"), mode="r")
            return root, meta, fresh
        except Exception:
            return None

    def put(self, k: str, grids, meta: dict):
        import zarr
        from zarr.codecs import BloscCodec
        d = self._dir(k)
        tmp = d.with_name(d.name + f".tmp{os.getpid()}.{threading.get_ident()}")
        shutil.rmtree(tmp, ignore_errors=True)
        (tmp).mkdir(parents=True)
        root = zarr.open_group(str(tmp / "data.zarr"), mode="w", zarr_format=3)
        codec = BloscCodec(cname="zstd", clevel=3, shuffle="shuffle")
        for g in grids.values():
            # short hashed names keep the on-disk path far below Windows' 260-char limit; the real
            # names live in attrs
            grp = root.require_group("g" + sha(g.grid)[:10])
            grp.attrs["grid_key"] = g.grid
            grp.attrs["epsg"] = int(g.epsg)
            grp.attrs["extras"] = list(g.extras)
            for name, arr in [("x", g.x), ("y", g.y), *g.extras.items()]:
                a_ = np.asarray(arr)
                grp.create_array(name, data=a_, chunks=a_.shape or (1,))
            for lk, arr in g.layers.items():
                ch = tuple(arr.shape[:-2]) + (min(arr.shape[-2], 512), min(arr.shape[-1], 512))
                za = grp.create_array("L" + sha(lk)[:12], data=arr, chunks=ch, compressors=codec)
                za.attrs["layer_key"] = lk
        (tmp / "meta.json").write_text(json.dumps({**meta, "created": time.time()}))
        d.parent.mkdir(parents=True, exist_ok=True)
        replace_dir(tmp, d)


# ------------------------------------------------------------------ search listings
class SearchCache:
    def __init__(self, root: Path, ttl_s: float | None):
        self.root, self.ttl = Path(root), ttl_s

    def key(self, bbox, t0, t1, products, maturity, keep_all) -> str:
        return sha("search", ",".join(f"{v:.6f}" for v in bbox), t0, t1, ",".join(sorted(products or [])),
                   maturity, keep_all)

    def get(self, k: str, allow_stale: bool = False):
        p = self.root / f"{k}.json"
        try:
            d = json.loads(p.read_text())
        except (OSError, ValueError):
            return None
        fresh = self.ttl is None or time.time() - d["created"] < self.ttl
        if not fresh and not allow_stale:
            return None
        return d["items"], fresh

    def put(self, k: str, items: list[dict]):
        self.root.mkdir(parents=True, exist_ok=True)
        p = self.root / f"{k}.json"
        tmp = p.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps({"created": time.time(), "items": items}))
        replace_file(tmp, p)
