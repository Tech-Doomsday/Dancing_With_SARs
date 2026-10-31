"""NISAR retrieval client: search -> layout index -> chunk-exact streaming -> tiered caches.

    client = NisarClient()                       # settings from config/retrieval.yaml + .env
    res = client.fetch(bbox, "2026-07-01", "2026-10-20", product="GUNW", layers=r"unwrappedPhase|coherence")
    client.prefetch([bbox1, bbox2], ...)         # warm caches ahead of the scanner
"""
from __future__ import annotations

import asyncio
import logging
import json
import os
import re
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from itertools import product as iproduct

import numpy as np

from ..auth.secrets import EarthdataAuth, earthdata_auth
from .cache import ByteLRU, ClipStore, DiskChunkCache, SearchCache, sha
from ..settings import Settings, load_settings
from .decode import UnsupportedFilter, decode_chunk
from .fsutil import replace_file
from .h5_index import (GranuleIndex, GridLayout, LayerLayout, build_index, catalog_missing, catalog_of,
                       extend_index)
from .range_io import HttpIO, LoopThread, RangeError, RangeFile
from .types import FetchResult, GranuleClip, GranuleRef, GridClip

_VERSION_TAIL = re.compile(r"_([A-Z])(\d{5})(_[A-Z]_[A-Z]_[A-Z]_\d{3})$")


# ------------------------------------------------------------------ pure helpers (unit-tested)
def scene_key(granule_id: str) -> tuple[str, int]:
    """(identity of the scene without its processing version, version number)."""
    m = _VERSION_TAIL.search(granule_id)
    return (granule_id[:m.start()] + m.group(3), int(m.group(2))) if m else (granule_id, 0)


def drop_older_versions(refs: list[GranuleRef]) -> tuple[list[GranuleRef], int]:
    """Same scene processed more than once -> keep the highest processing version."""
    best: dict[str, tuple[int, int]] = {}
    for i, r in enumerate(refs):
        key, ver = scene_key(r.granule_id)
        if key not in best or ver > best[key][0]:
            best[key] = (ver, i)
    keep = sorted(i for _, i in best.values())
    return [refs[i] for i in keep], len(refs) - len(keep)


_slog = logging.getLogger("nisar.retrieval")


@lru_cache(maxsize=64)
def _transformer(epsg: int):
    from pyproj import Transformer
    return Transformer.from_crs(4326, epsg, always_xy=True)


def bbox_window(grid: GridLayout, bbox) -> tuple[slice, slice] | None:
    """Pixel window of `bbox` (lon/lat) on a grid, with a 1-cell margin; None if no overlap."""
    w, s, e, n = bbox
    tr = _transformer(grid.epsg)
    mx, my = (w + e) / 2, (s + n) / 2
    px, py = tr.transform([w, e, w, e, mx, mx, w, e], [s, s, n, n, s, n, my, my])
    x, y = grid.x, grid.y
    dx = abs(float(x[1] - x[0])) if len(x) > 1 else 0.0
    dy = abs(float(y[1] - y[0])) if len(y) > 1 else 0.0
    xi = np.nonzero((x >= min(px) - dx) & (x <= max(px) + dx))[0]
    yi = np.nonzero((y >= min(py) - dy) & (y <= max(py) + dy))[0]
    if xi.size == 0 or yi.size == 0:
        return None
    return slice(int(yi[0]), int(yi[-1]) + 1), slice(int(xi[0]), int(xi[-1]) + 1)


def plan_chunks(layer: LayerLayout, ys: slice, xs: slice) -> list[tuple[int, tuple]]:
    """[(allocated_chunk_index or -1, chunk_grid_coord)] for every chunk the window touches."""
    ch = layer.chunks
    rngs = [range(-(-layer.shape[i] // ch[i])) for i in range(len(ch) - 2)]
    rngs.append(range(ys.start // ch[-2], (ys.stop - 1) // ch[-2] + 1))
    rngs.append(range(xs.start // ch[-1], (xs.stop - 1) // ch[-1] + 1))
    t = layer.table
    return [(int(t[c]), c) for c in iproduct(*rngs)]


class RetrievalCancelled(Exception):
    """Raised inside a granule's fetch when the retrieval was cancelled; nothing partial is stored as a clip."""


# ------------------------------------------------------------------ client
class NisarClient:
    def __init__(self, settings: Settings | None = None, auth: EarthdataAuth | None = None):
        _check_deps()
        self.cfg = settings or load_settings()
        c = self.cfg.cache
        self.auth = auth or earthdata_auth()
        self.io = HttpIO(self.auth, self.cfg.net)
        self.ram = ByteLRU(int(c["ram_chunks_mb"]) << 20)
        self.disk = DiskChunkCache(self.cfg.chunks_dir, int(float(c["disk_chunks_gb"]) * (1 << 30)))
        self.clips = ClipStore(self.cfg.clips_dir, c["ttl_s"].get("granule_clip"))
        self.searches = SearchCache(self.cfg.search_dir, c["ttl_s"].get("search"))
        self.pool = ThreadPoolExecutor(int(self.cfg.decode["threads"]), thread_name_prefix="decode")
        self.blocking = ThreadPoolExecutor(8, thread_name_prefix="blocking")  # index build, fallback, clip IO
        self._indexes: dict[str, GranuleIndex] = {}
        self.last_search_truncated = False   # set by search(): the result list hit maxResults
        self.last_search_incomplete = False  # set by search(): ASF/CMR reported a short or failed page (not cached)
        self.last_search_warnings: list = []
        self._prog_lock = threading.Lock()
        self.progress = {"product": "", "total": 0, "done": 0, "failed": 0, "skipped": 0, "cached": 0,
                         "started": 0.0, "active": {}, "cum_done": 0, "cum_total": 0, "recent": []}
        self.cancel_event = threading.Event()   # set by a retrieval's Cancel; checked between chunk batches
        self.granule_states: dict[str, dict] = {}   # granule_id -> live state (queued/indexing/fetching/stored/...)
        self._granule_sem: asyncio.Semaphore | None = None
        self._inflight: dict[str, asyncio.Future] = {}
        self._index_inflight: dict[str, asyncio.Future] = {}
        self._fb: OrderedDict = OrderedDict()
        self._fb_lock = threading.RLock()
        self.counters = {"index_built": 0, "index_disk": 0, "clip_hit": 0, "chunks_net": 0,
                         "chunks_disk": 0, "chunks_ram": 0, "fallback_layers": 0,
                         "index_skeleton_s": 0.0, "index_tables_s": 0.0, "index_tables_layers": 0}
        self._tbl_locks: dict[str, asyncio.Lock] = {}
        self._catalogs: dict[str, dict] = {}

    # ---------------------------------------------------------------- search
    def search(self, bbox, t0=None, t1=None, product=None, maturity=None, fresh=False, max_results=None):
        """-> (refs, degraded). Uses asf_search; result lists are TTL-cached and served stale
        (degraded=True) if ASF is unreachable. bbox=None searches everywhere; fresh=True skips the
        cache read (the poller does this) but still falls back to a stale entry if ASF is down.

        Hitting the result budget sets last_search_truncated and logs a WARNING: a truncated list
        looks like 'no granules over the tile' downstream, which it is not.
        """
        keep_all = bool(self.cfg.search["keep_all_versions"])
        prods = [p.strip() for p in product.split(",")] if isinstance(product, str) else (product or [])
        k = self.searches.key(bbox or (0, 0, 0, 0), t0, t1, prods, maturity, keep_all)
        hit = None if fresh else self.searches.get(k)
        if hit:
            self.last_search_truncated = False
            self.last_search_incomplete = False
            self.last_search_warnings = []
            return [GranuleRef(**d) for d in hit[0]], False
        try:
            import asf_search as asf
            kw = {"dataset": "NISAR", "maxResults": int(max_results or self.cfg.search["max_results"])}
            if bbox:
                w, s, e, n = bbox
                kw["intersectsWith"] = f"POLYGON(({w} {s},{e} {s},{e} {n},{w} {n},{w} {s}))"
            if t0: kw["start"] = t0
            if t1: kw["end"] = t1
            if prods: kw["processingLevel"] = prods
            if maturity: kw["dataMaturity"] = maturity
            refs = []
            t_search = time.monotonic()
            _slog.info("ASF search start: product=%s bbox=%s window=%s..%s", prods or "any", bbox, t0, t1)
            tmo = float(self.cfg.search.get("timeout_s", 90) or 0)
            warns: list = []
            found = self._asf_search_bounded(asf, kw, tmo, warns)
            tries = int(self.cfg.search.get("retry_incomplete", 1) or 0)
            while warns and tries > 0:                   # CMR sometimes serves a short page; a second ask usually fixes it
                tries -= 1
                _slog.warning("ASF/CMR reported an incomplete result (%s); retrying the search", warns[0][:160])
                time.sleep(2.0)
                warns = []
                found = self._asf_search_bounded(asf, kw, tmo, warns)
            self.last_search_incomplete = bool(warns)
            self.last_search_warnings = list(warns)
            if warns:
                _slog.warning("ASF search still INCOMPLETE after retry (%s) — listing used but NOT cached", warns[0][:160])
            _slog.info("ASF search done: %d result(s) in %.1fs%s", len(found), time.monotonic() - t_search,
                       " [INCOMPLETE]" if warns else "")
            for r in found:
                p = r.properties
                name = p.get("fileName") or str(p.get("url", "")).rsplit("/", 1)[-1]
                gid = re.sub(r"\.h5$", "", str(p.get("fileID") or name))
                # E8: provider-side facts pass through verbatim (granule Explorer enrichment);
                # absent keys mean "the provider did not say" — the route never guesses them.
                extra = {k: p.get(k) for k in ("collectionName", "crid", "orbitType",
                                               "mainBandPolarization", "rangeBandwidth",
                                               "processingDate", "productionConfiguration")
                         if p.get(k) is not None}
                refs.append(GranuleRef(gid, p.get("url"), p.get("processingLevel"), p.get("startTime"),
                                       p.get("stopTime"), p.get("pathNumber"), p.get("frameNumber"),
                                       p.get("flightDirection"), _size(p), _ring(r), extra))
            budget = int(max_results or self.cfg.search["max_results"])
            truncated = len(refs) >= budget
            if truncated:
                self.last_search_truncated = True
                logging.getLogger("nisar.retrieval").warning(
                    "search hit maxResults=%d — result list is TRUNCATED; narrow the query "
                    "(smaller bbox / tighter time window) before treating it as complete", budget)
            else:
                self.last_search_truncated = False
            if not keep_all:
                refs, _ = drop_older_versions(refs)
            if not self.last_search_incomplete:          # never keep a short listing for the whole TTL
                self.searches.put(k, [r.to_dict() for r in refs])
            return refs, False
        except Exception:
            stale = self.searches.get(k, allow_stale=True)
            if stale and self.cfg.cache["stale_on_error"]:
                return [GranuleRef(**d) for d in stale[0]], True
            raise

    @staticmethod
    def _asf_search_bounded(asf, kw: dict, timeout_s: float, warnings: list | None = None):
        """asf.search() has no network timeout of its own, so a dead connection hangs the scan forever.
        Run it on a daemon thread and stop waiting after `timeout_s` (0/None = wait indefinitely).
        Every WARNING/ERROR the asf_search logger emits on that thread (e.g. "CMR returned page of incomplete
        results") is appended to `warnings`, so the caller can tell a complete listing from a short one."""
        class _Cap(logging.Handler):
            ident = None

            def emit(self, rec):
                if warnings is not None and rec.levelno >= logging.WARNING and rec.thread == self.ident:
                    warnings.append(rec.getMessage())
        cap = _Cap(level=logging.WARNING)
        alog = logging.getLogger("asf_search")
        # asf_search logs a ~60-line option dump per query at INFO; keep WARNING+ (NISAR_ASF_LOG_LEVEL=INFO restores it)
        alog.setLevel(os.environ.get("NISAR_ASF_LOG_LEVEL", "WARNING").upper())
        alog.addHandler(cap)
        try:
            if not timeout_s:
                cap.ident = threading.get_ident()
                return asf.search(**kw)
            box: dict = {}

            def work():
                cap.ident = threading.get_ident()
                try:
                    box["res"] = asf.search(**kw)
                except BaseException as ex:                      # re-raised in the caller
                    box["err"] = ex
            th = threading.Thread(target=work, name="asf-search", daemon=True)
            th.start()
            th.join(timeout_s)
            if th.is_alive():
                raise TimeoutError(f"ASF search did not answer within {timeout_s:.0f}s "
                                   f"(network/Earthdata problem? raise search.timeout_s or NISAR_SEARCH_TIMEOUT_S)")
            if "err" in box:
                raise box["err"]
            return box["res"]
        finally:
            alog.removeHandler(cap)

    # ---------------------------------------------------------------- public fetch
    def fetch(self, bbox, t0=None, t1=None, product=None, layers: str | None = None,
              maturity=None, granules: list[GranuleRef] | None = None) -> FetchResult:
        """Architecture §8 entry point: fetch(bbox, t0, t1, product, layers) -> clips."""
        if getattr(self, "_closed", False):
            raise RuntimeError("NisarClient is closed")
        degraded = False
        if granules is None:
            granules, degraded = self.search(bbox, t0, t1, product, maturity)
        with self._prog_lock:
            self.progress["product"] = product if isinstance(product, str) else ",".join(product or [])
        _slog.info("fetch start: %d granule(s), layers=%s", len(granules), (layers or "all")[:80])
        t_fetch = time.monotonic()
        res = self.io.lt.run(self._fetch_many(granules, tuple(bbox), layers))
        _m = self.io.metrics.snapshot()
        _slog.info("fetch done: %d clip(s), %d error(s) in %.1fs | %.1f MB on wire, %d requests, %d retries",
                   len(res.clips), len(res.errors), time.monotonic() - t_fetch,
                   _m["bytes_fetched"] / 1e6, _m["requests"], _m["retries"])
        res.degraded = res.degraded or degraded
        res.stats = {**self.io.metrics.snapshot(), **self.counters,
                     "ram_hits": self.ram.hits, "disk_hits": self.disk.hits}
        return res

    def fetch_tile(self, tile, t0=None, t1=None, product=None, layers=None, maturity=None,
                   granules=None, margin_m=None) -> FetchResult:
        """fetch() for one MGRS tile (engine.tiling.mgrs.Tile or a tile id string)."""
        from ...tiling.mgrs import tile_from_id
        t = tile_from_id(tile) if isinstance(tile, str) else tile
        m = self.cfg.tiling["margin_m"] if margin_m is None else margin_m
        return self.fetch(t.bbox(m), t0, t1, product, layers, maturity, granules)

    def prefetch(self, bboxes, t0=None, t1=None, product=None, layers=None, maturity=None):
        """Warm every tier for these boxes; results are discarded (arrays stay in the caches)."""
        for bb in bboxes:
            self.fetch(bb, t0, t1, product, layers, maturity)

    def close(self):
        if getattr(self, "_closed", False):
            return
        self._closed = True
        with self._fb_lock:                      # open h5py handles + their block caches
            for rf, h5 in list(self._fb.values()):
                for obj in (h5, rf):
                    try:
                        obj.close()
                    except Exception:
                        pass
            self._fb.clear()
        self._indexes.clear()
        self.io.close()
        self.pool.shutdown(wait=False)
        self.blocking.shutdown(wait=False)

    # ---------------------------------------------------------------- internals
    # ---------------------------------------------------------------- per-granule state
    def _gstate(self, gid: str, **kw) -> None:
        """Live per-granule state for the retrieval display (read by progress_snapshot)."""
        with self._prog_lock:
            st = self.granule_states.setdefault(gid, {"state": "queued", "bytes_planned": 0, "bytes_done": 0,
                                                      "chunks_planned": 0, "chunks_done": 0, "error": None,
                                                      "started": None, "seconds": None})
            st.update(kw)

    def granule_concurrency(self) -> int:
        try:
            return max(1, int(self.cfg.net.get("granule_concurrency", 4)))
        except (TypeError, ValueError):
            return 4

    async def _fetch_many(self, granules, bbox, layers) -> FetchResult:
        """Granules run through a bounded pool (`net.granule_concurrency`, default 4; the global range
        request limit in HttpIO is unchanged), so they FINISH one by one — each finished granule is already
        in the clip store (`_granule_clip_uncached`) when the next one starts. A cancel (`cancel_event`)
        lets in-flight ranges finish, stops queued granules, and keeps everything already stored."""
        res = FetchResult([], {}, {})
        n = len(granules)
        sem = asyncio.Semaphore(self.granule_concurrency())
        with self._prog_lock:
            pg = self.progress
            pg.update(total=n, done=0, failed=0, skipped=0, cached=0, started=time.monotonic(), active={})
            pg["cum_total"] += n
            for g in granules:
                self.granule_states[g.granule_id] = {
                    "state": "queued", "product": g.product, "bytes_planned": 0, "bytes_done": 0,
                    "chunks_planned": 0, "chunks_done": 0, "error": None, "started": None, "seconds": None}

        def finish(g, how: str, t_start: float, extra: str = "") -> None:
            with self._prog_lock:
                pg["active"].pop(g.granule_id, None)
                pg["done"] += 1
                pg["cum_done"] += 1
                if how == "failed":
                    pg["failed"] += 1
                elif how == "skipped":
                    pg["skipped"] += 1
                elif how.startswith(("clip-cache", "stale")):
                    pg["cached"] += 1
                done, total = pg["done"], pg["total"]
                pg["recent"] = (pg["recent"] + [{"id": g.granule_id, "how": how, "s": round(time.monotonic() - t_start, 1)}])[-8:]
            _slog.info("granule %d/%d %s in %.1fs: %s%s", done, total, how, time.monotonic() - t_start,
                       g.granule_id, f" ({extra})" if extra else "")

        async def one(g):
            async with sem:
                if self.cancel_event.is_set():
                    res.skipped[g.granule_id] = "cancelled before it started"
                    self._gstate(g.granule_id, state="cancelled")
                    with self._prog_lock:
                        pg["done"] += 1
                        pg["skipped"] += 1
                    return
                t_start = time.monotonic()
                with self._prog_lock:
                    pg["active"][g.granule_id] = t_start
                self._gstate(g.granule_id, state="indexing", started=t_start)
                try:
                    clip = await self._granule_clip(g, bbox, layers)
                    secs = round(time.monotonic() - t_start, 1)
                    if clip is None:
                        res.skipped[g.granule_id] = "bbox does not overlap this granule's grids"
                        self._gstate(g.granule_id, state="skipped", seconds=secs)
                        finish(g, "skipped", t_start, "bbox does not overlap this granule")
                    else:
                        res.clips.append(clip)
                        res.degraded |= clip.source.startswith("stale")
                        self._gstate(g.granule_id, state="stored", seconds=secs, source=clip.source)
                        finish(g, clip.source, t_start)
                except RetrievalCancelled:
                    res.skipped[g.granule_id] = "cancelled while fetching (chunks already fetched stay cached)"
                    self._gstate(g.granule_id, state="cancelled", seconds=round(time.monotonic() - t_start, 1))
                    finish(g, "skipped", t_start, "cancelled")
                except Exception as ex:  # reported, never swallowed
                    res.errors[g.granule_id] = f"{type(ex).__name__}: {ex}"
                    logging.getLogger("nisar.client").error("granule %s failed", g.granule_id, exc_info=True)   # keep the traceback
                    self._gstate(g.granule_id, state="failed", error=f"{type(ex).__name__}: {ex}"[:300],
                                 seconds=round(time.monotonic() - t_start, 1))
                    finish(g, "failed", t_start, f"{type(ex).__name__}: {ex}"[:160])

        await asyncio.gather(*(one(g) for g in granules))
        order = {g.granule_id: i for i, g in enumerate(granules)}
        res.clips.sort(key=lambda c: order[c.granule.granule_id])
        return res

    # ---------------------------------------------------------------- plan (no data chunks downloaded)
    def plan_tile(self, granules: list, bbox, layers: str | None = None) -> list[dict]:
        """Exact compressed bytes per granule and layer for the window — from the layout index
        (chunk tables), BEFORE any data chunk is downloaded. Builds/loads the index (small metadata reads)."""
        return self.io.lt.run(self._plan_many(granules, tuple(bbox), layers))

    async def _plan_many(self, granules, bbox, layers) -> list[dict]:
        sem = asyncio.Semaphore(self.granule_concurrency())

        async def one(g):
            async with sem:
                try:
                    return await self._plan_granule(g, bbox, layers)
                except Exception as ex:   # reported as a row, never swallowed
                    return [{"granule_id": g.granule_id, "product": g.product, "error": f"{type(ex).__name__}: {ex}"[:300]}]
        out: list[dict] = []
        for rows in await asyncio.gather(*(one(g) for g in granules)):
            out.extend(rows)
        return out

    async def _plan_granule(self, g, bbox, layers) -> list[dict]:
        pat = re.compile(layers) if layers else None
        ix = await self._index(g, pat)
        rows: list[dict] = []
        for gpath, gl in ix.grids.items():
            win = bbox_window(gl, bbox)
            if win is None:
                continue
            ys, xs = win
            for p in gl.layers:
                if pat and not pat.search(p):
                    continue
                L = ix.layers[p]
                row = {"granule_id": g.granule_id, "product": g.product, "grid": gl.key, "layer": L.key,
                       "shape": list(L.shape), "chunk_shape": list(L.chunks), "dtype": str(L.dtype),
                       "window": [ys.start, ys.stop, xs.start, xs.stop], "fast": bool(L.fast)}
                if not L.fast:
                    row.update(chunks=0, bytes=None, note="read through h5py (contiguous / exotic filter): bytes not planned")
                else:
                    plan = [ci for ci, _ in plan_chunks(L, ys, xs) if ci >= 0]
                    row.update(chunks=len(plan), bytes=int(sum(int(L.sizes[ci]) for ci in plan)))
                rows.append(row)
        return rows

    def progress_snapshot(self) -> dict:
        """Everything the scan display can show about the retriever, read-only and cheap: wire counters
        (+ live gauges), per-granule progress of the current fetch, cache hit counters."""
        now = time.monotonic()
        with self._prog_lock:
            pg = self.progress
            active = sorted(((now - t0, gid) for gid, t0 in pg["active"].items()), reverse=True)
            done, total, started = pg["done"], pg["total"], pg["started"]
            elapsed = (now - started) if started else 0.0
            eta = (elapsed / done * (total - done)) if done >= 2 and total > done else None
            gran = {"product": pg["product"], "total": total, "done": done, "failed": pg["failed"],
                    "skipped": pg["skipped"], "cached": pg["cached"], "in_flight": len(active),
                    "elapsed_s": round(elapsed, 1), "eta_s": None if eta is None else round(eta),
                    "cum_done": pg["cum_done"], "cum_total": pg["cum_total"],
                    "oldest": [{"id": gid, "age_s": round(age, 1)} for age, gid in active[:4]],
                    "recent": list(pg["recent"][-4:])}
        cache = {**{k: v for k, v in self.counters.items() if not k.endswith("_s")},
                 "ram_hits": self.ram.hits, "disk_hits": self.disk.hits}
        with self._prog_lock:
            gran["states"] = {gid: dict(st) for gid, st in self.granule_states.items()}
        return {"net": self.io.metrics.live(), "granules": gran, "cache": cache,
                "search": {"truncated": bool(self.last_search_truncated), "incomplete": bool(self.last_search_incomplete)}}

    async def _granule_clip(self, g: GranuleRef, bbox, layers) -> GranuleClip | None:
        k = self.clips.key(g.granule_id, bbox, [layers] if layers else None)
        fut = self._inflight.get(k)            # singleflight: identical concurrent requests share work
        if fut is not None:
            return await asyncio.shield(fut)
        fut = asyncio.ensure_future(self._granule_clip_uncached(g, bbox, layers, k))
        self._inflight[k] = fut
        try:
            return await fut
        finally:
            self._inflight.pop(k, None)

    async def _granule_clip_uncached(self, g, bbox, layers, k):
        loop = asyncio.get_running_loop()
        hit = await loop.run_in_executor(self.blocking, self.clips.get, k)
        if hit:
            self.counters["clip_hit"] += 1
            return await loop.run_in_executor(self.blocking, self._from_zarr, g, bbox, hit[0], "clip-cache")
        try:
            clip = await self._stream_clip(g, bbox, layers)
        except (RangeError, OSError, asyncio.TimeoutError) as ex:
            stale = await loop.run_in_executor(self.blocking, lambda: self.clips.get(k, allow_stale=True))
            if stale and self.cfg.cache["stale_on_error"]:
                return await loop.run_in_executor(self.blocking, self._from_zarr, g, bbox, stale[0], "stale-clip-cache")
            raise
        if clip is not None:
            await loop.run_in_executor(self.blocking, self.clips.put, k, clip.grids,
                                       {"granule": g.granule_id, "bbox": list(bbox), "layers": layers,
                                        "product": g.product})
        return clip

    @staticmethod
    def _from_zarr(g, bbox, root, source) -> GranuleClip:
        grids = {}
        for _, grp in root.groups():
            key = grp.attrs["grid_key"]
            ex_names = set(grp.attrs.get("extras", []))
            extras = {k: np.asarray(grp[k][:]) for k in ex_names}
            lay = {}
            for k in grp.array_keys():
                lk = grp[k].attrs.get("layer_key")
                if lk is not None:
                    lay[lk] = np.asarray(grp[k][:])
            grids[key] = GridClip(key, int(grp.attrs["epsg"]), np.asarray(grp["x"][:]), np.asarray(grp["y"][:]), extras, lay)
        return GranuleClip(g, tuple(bbox), grids, source)

    # ---------------------------------------------------------------- layout index
    async def _index(self, g: GranuleRef, pat: "re.Pattern | None" = None) -> GranuleIndex:
        ix = self._indexes.get(g.granule_id)
        if ix is None:
            fut = self._index_inflight.get(g.granule_id)
            if fut is None:
                fut = asyncio.ensure_future(self._index_load_or_build(g, pat))
                self._index_inflight[g.granule_id] = fut
                fut.add_done_callback(lambda _f: self._index_inflight.pop(g.granule_id, None))
            ix = await asyncio.shield(fut)
        loop = asyncio.get_running_loop()
        lock = self._tbl_locks.setdefault(g.granule_id, asyncio.Lock())
        cat = self._catalog(g)
        if cat and catalog_missing(ix, cat, pat):            # partial (targeted) index: add layers on demand
            async with lock:
                if catalog_missing(ix, cat, pat):
                    await loop.run_in_executor(self.blocking, self._extend_index, g, ix, cat, pat)
        want = [p for p, L in ix.layers.items() if L.fast and not (pat and not pat.search(p))]
        if ix.missing_tables(want):
            async with lock:
                miss = ix.missing_tables(want)
                if miss:
                    await loop.run_in_executor(self.blocking, self._load_tables, g, ix, miss)
        return ix

    async def _index_load_or_build(self, g: GranuleRef, pat) -> GranuleIndex:
        path = self._index_path(g)
        loop = asyncio.get_running_loop()
        ix = await loop.run_in_executor(self.blocking, GranuleIndex.load, path)
        if ix:
            self.counters["index_disk"] += 1
        else:
            ix = await loop.run_in_executor(self.blocking, self._build_index, g, path, pat)
            self.counters["index_built"] += 1
        self._indexes[g.granule_id] = ix
        return ix

    def _index_path(self, g):
        return self.cfg.index_dir / f"{sha(g.granule_id)}.npz"

    # catalog = layer/grid paths learned from one full walk of a product type; later granules are
    # then opened by path (a handful of metadata pages) instead of walking every object in the file.
    # The runtime cache (learned from files of THIS product type, self-healing) is consulted before
    # any shipped catalog: a shipped json for a different product revision must not shadow it, or
    # every granule pays the stale-catalog KeyError -> full-walk fallback (regression 2026-10-27).
    def _catalog(self, g: GranuleRef) -> dict | None:
        if not g.product:
            return None
        if g.product in self._catalogs:
            return self._catalogs[g.product]
        cache = self.cfg.catalog_dirs[-1:]          # runtime-learned, self-healing
        shipped = self.cfg.catalog_dirs[:-1]        # repo defaults (last resort)
        for d in (*cache, *shipped):
            try:
                self._catalogs[g.product] = json.loads((d / f"{g.product}.json").read_text())
                return self._catalogs[g.product]
            except (OSError, ValueError):
                continue
        return None

    def _save_catalog(self, g: GranuleRef, ix: GranuleIndex):
        if not g.product:
            return
        cat = catalog_of(ix)
        d = self.cfg.catalog_dirs[1]
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / f"{g.product}.{os.getpid()}.{threading.get_ident()}.tmp"
        tmp.write_text(json.dumps(cat))
        replace_file(tmp, d / f"{g.product}.json")
        self._catalogs[g.product] = cat

    def _full_walk(self, g: GranuleRef) -> GranuleIndex:
        rf, h5 = self._fallback_pair(g)                       # use the pair we were handed: another thread may evict the cache entry
        with self._fb_lock:
            ix = build_index(h5, g.granule_id, rf.size)
        self._save_catalog(g, ix)
        return ix

    def _build_index(self, g: GranuleRef, path, pat) -> GranuleIndex:
        t = time.perf_counter()
        cat = self._catalog(g)
        ix = None
        if cat:
            rf, h5 = self._fallback_pair(g)
            ix = GranuleIndex(g.granule_id, rf.size, {}, {})
            try:
                with self._fb_lock:
                    extend_index(ix, h5, cat, pat)
                if not ix.layers:                             # catalog knows none of the wanted layers: stale
                    raise KeyError("catalog matched no layers")
                self.counters["index_targeted"] = self.counters.get("index_targeted", 0) + 1
            except KeyError:                                  # product layout changed: relearn
                self._catalogs.pop(g.product, None)
                ix = None
        if ix is None:
            ix = self._full_walk(g)
        ix.save(path)
        self.counters["index_skeleton_s"] += time.perf_counter() - t
        return ix

    def _extend_index(self, g: GranuleRef, ix: GranuleIndex, cat: dict, pat):
        t = time.perf_counter()
        h5 = self._fallback_file(g)
        try:
            with self._fb_lock:
                extend_index(ix, h5, cat, pat)
        except KeyError:
            self._catalogs.pop(g.product, None)
            full = self._full_walk(g)
            # the KeyError usually means the CATALOG was stale, not the file: chunk tables already
            # read for unchanged layouts stay loaded (a full rebuild forgets them and every later
            # fetch of those layers re-reads their chunk tables from the network)
            for p, L in list(ix.layers.items()):
                new = full.layers.get(p)
                if new is not None and (L.shape, L.dtype.str, L.chunks, L.filters) == (new.shape, new.dtype.str, new.chunks, new.filters):
                    full.layers[p] = L
            ix.grids, ix.layers = full.grids, full.layers
        ix.save(self._index_path(g))
        self.counters["index_skeleton_s"] += time.perf_counter() - t

    def _load_tables(self, g: GranuleRef, ix: GranuleIndex, paths):
        t = time.perf_counter()
        h5 = self._fallback_file(g)
        with self._fb_lock:
            n = ix.ensure_tables(h5, paths)
        ix.save(self._index_path(g))
        self.counters["index_tables_s"] += time.perf_counter() - t
        self.counters["index_tables_layers"] += n

    def index_experiment(self, g: GranuleRef, pat, include_old: bool = False, report=print) -> list[dict]:
        """Time the layout-index strategies on one granule (nothing is cached). Used by probe_granule."""
        import h5py
        m = self.io.metrics
        rows = []

        def run(label, block, pagebuf, targeted, cat=None):
            before = m.snapshot()
            t = time.perf_counter()
            rf = RangeFile(self._fallback_io(), g.url, block=block, max_blocks=256)
            rf.warm()
            kw = {"page_buf_size": 16 << 20} if pagebuf else {}
            with h5py.File(rf, "r", **kw) as h5:
                if targeted:
                    ix = GranuleIndex(g.granule_id, rf.size, {}, {})
                    extend_index(ix, h5, cat, pat)
                else:
                    ix = build_index(h5, g.granule_id, rf.size)
                want = [p for p, L in ix.layers.items() if L.fast and not (pat and not pat.search(p))]
                ix.ensure_tables(h5, want)
            dt = time.perf_counter() - t
            d = {k: m.snapshot()[k] - before[k] for k in before}
            row = {"label": label, "seconds": dt, "requests": d["requests"], "mb": d["bytes_fetched"] / 1e6, "ix": ix}
            rows.append(row)
            report(f"  {label:48} {dt:7.1f}s {d['requests']:5d} requests {d['bytes_fetched'] / 1e6:8.1f} MB")
            return ix

        ix = run("full walk, 512 KiB blocks, no page buffer", 512 << 10, False, False)
        self._save_catalog(g, ix)
        run("targeted by catalog, 512 KiB, no page buffer", 512 << 10, False, True, self._catalog(g))
        if include_old:
            run("full walk, 4 MiB blocks + page buffer (old)", 4 << 20, True, False)
        return rows

    async def _stream_clip(self, g: GranuleRef, bbox, layers) -> GranuleClip | None:
        pat = re.compile(layers) if layers else None
        ix = await self._index(g, pat)
        if self.cancel_event.is_set():
            raise RetrievalCancelled(g.granule_id)
        loop = asyncio.get_running_loop()
        out_grids: dict[str, GridClip] = {}
        jobs: list = []                      # (layer, ckey, out, dst, src_slices_fn...)
        slow: list = []                      # (grid, layer, ys, xs) served by the h5py fallback
        for gpath, gl in ix.grids.items():
            win = bbox_window(gl, bbox)
            if win is None:
                continue
            ys, xs = win
            sel = [ix.layers[p] for p in gl.layers if not (pat and not pat.search(p))]
            if not sel:
                continue
            gc = GridClip(gl.key, gl.epsg, gl.x[xs], gl.y[ys], dict(gl.extras), {})
            out_grids[gl.key] = gc
            for L in sel:
                if not L.fast:
                    slow.append((gc, L, ys, xs))
                    continue
                shape = L.shape[:-2] + (ys.stop - ys.start, xs.stop - xs.start)
                fill = L.fill if L.fill is not None else 0
                arr = np.full(shape, fill, dtype=L.dtype) if L.dtype.fields is None else np.zeros(shape, L.dtype)
                gc.layers[L.key] = arr
                for ci, coord in plan_chunks(L, ys, xs):
                    if ci >= 0:
                        jobs.append((L, ci, coord, arr, ys, xs))
        if not out_grids:
            return None
        self._gstate(g.granule_id, state="fetching", chunks_planned=len(jobs),
                     bytes_planned=int(sum(int(j[0].sizes[j[1]]) for j in jobs)))
        stats = await self._run_jobs(g, jobs)
        if slow:
            await loop.run_in_executor(self.blocking, self._fallback_layers, g, slow)
        return GranuleClip(g, tuple(bbox), out_grids, "network", stats)

    async def _run_jobs(self, g: GranuleRef, jobs) -> dict:
        """RAM -> disk -> network for every chunk; decode overlaps with the network."""
        loop = asyncio.get_running_loop()
        need: list = []                      # jobs missing from RAM+disk -> network
        pending: list = []

        def place(L, ci, coord, arr, ys, xs, chunk: np.ndarray):
            o = L.origins[ci]
            lo = [max(int(o[d]), 0) for d in range(arr.ndim)]
            lo[-2], lo[-1] = max(int(o[-2]), ys.start), max(int(o[-1]), xs.start)
            hi = [min(int(o[d]) + L.chunks[d], L.shape[d]) for d in range(arr.ndim)]
            hi[-2], hi[-1] = min(hi[-2], ys.stop), min(hi[-1], xs.stop)
            src = tuple(slice(lo[d] - int(o[d]), hi[d] - int(o[d])) for d in range(arr.ndim))
            off = [0] * arr.ndim
            off[-2], off[-1] = ys.start, xs.start
            dst = tuple(slice(lo[d] - off[d], hi[d] - off[d]) for d in range(arr.ndim))
            arr[dst] = chunk[src]

        def decode_place(job, raw):
            L, ci, coord, arr, ys, xs = job
            chunk = decode_chunk(raw, L.filters, int(L.masks[ci]), L.dtype, L.chunks)
            self.ram.put(f"{g.granule_id}|{L.path}|{ci}", chunk)
            place(L, ci, coord, arr, ys, xs, chunk)

        for job in jobs:
            L, ci, coord, arr, ys, xs = job
            hit = self.ram.get(f"{g.granule_id}|{L.path}|{ci}")
            if hit is not None:
                self.counters["chunks_ram"] += 1
                place(L, ci, coord, arr, ys, xs, hit)
                continue
            raw = self.disk.get(f"{g.granule_id}|{L.path}|{ci}")
            if raw is not None:
                self.counters["chunks_disk"] += 1
                pending.append(loop.run_in_executor(self.pool, decode_place, job, raw))
            else:
                need.append(job)
        if need:
            self.counters["chunks_net"] += len(need)
            ranges = [(int(L.offsets[ci]), int(L.sizes[ci])) for L, ci, *_ in need]
            cancelled = False
            async for idxs, mvs in self.io.stream_ranges(g.url, ranges):
                for i, mv in zip(idxs, mvs):
                    job = need[i]
                    pending.append(loop.run_in_executor(self.pool, self._decode_and_store, g, job, mv, decode_place))
                with self._prog_lock:
                    st = self.granule_states.get(g.granule_id)
                    if st is not None:
                        st["chunks_done"] += len(idxs)
                        st["bytes_done"] += sum(int(need[i][0].sizes[need[i][1]]) for i in idxs)
                if self.cancel_event.is_set():       # between chunk batches: stop asking; in-flight work finishes below
                    cancelled = True
                    break
        if pending:
            await asyncio.gather(*pending)
        if need and cancelled:
            raise RetrievalCancelled(g.granule_id)
        return {"chunks": len(jobs), "network": len(need)}

    def _decode_and_store(self, g, job, mv, decode_place):
        L, ci = job[0], job[1]
        self.disk.put(f"{g.granule_id}|{L.path}|{ci}", mv)
        decode_place(job, mv)

    def _fallback_file(self, g: GranuleRef):
        return self._fallback_pair(g)[1]

    def _fallback_io(self) -> HttpIO:
        """A private inline HTTP twin for the h5py-serving path.

        A thread inside an h5py callback holds h5py's global lock for the whole call. If that call
        waits on THE event loop (for a range read) while the loop thread finalises any h5py object
        (cyclic GC runs on whichever thread allocates), the two wait on each other forever. Serving
        RangeFile reads from a second, private loop breaks the cycle: loop-side GC can then only
        ever wait for a reader that is itself waiting on a loop that carries no h5py work
        (regression: test_index_build_survives_h5py_objects_being_finalised_on_the_loop_thread).
        """
        return self.io.inline_io("h5py-fallback")

    def _fallback_pair(self, g: GranuleRef):
        """-> (RangeFile, h5py.File). One open handle per granule, kept in an LRU so repeated fallback reads reuse its
        block cache. The LRU must hold more entries than there are worker threads: evicting closes the handle, and a
        thread that is still using it (or looks it up again after waiting for the lock) would fail with a bare
        KeyError(granule_id)."""
        import h5py
        with self._fb_lock:
            hit = self._fb.pop(g.granule_id, None)
            if hit is None:
                rf = RangeFile(self._fallback_io(), g.url, block=int(self.cfg.net["index_block_size_bytes"]), max_blocks=256)
                rf.warm()
                h5 = None
                if self.cfg.net["index_page_buffer"]:
                    try:
                        h5 = h5py.File(rf, "r", page_buf_size=16 << 20)
                    except Exception:
                        h5 = None
                if h5 is None:
                    h5 = h5py.File(rf, "r")
                hit = (rf, h5)
            self._fb[g.granule_id] = hit
            while len(self._fb) > max(16, 2 * getattr(self.blocking, "_max_workers", 8)):
                _, (_, old) = self._fb.popitem(last=False)
                try:
                    old.close()
                except Exception:
                    pass
            return hit

    def _fallback_layers(self, g: GranuleRef, slow):
        """Contiguous / exotic-filter layers: plain h5py over the same ranged file object."""
        h5 = self._fallback_file(g)
        for gc, L, ys, xs in slow:
            with self._fb_lock:
                d = h5[L.path]
                idx = (slice(None),) * (d.ndim - 2) + (ys, xs)
                gc.layers[L.key] = d[idx]
            self.counters["fallback_layers"] += 1


def _ring(r):
    """Outer footprint ring [[lon, lat], ...] from an asf_search product (attribute or geojson())."""
    for get in (lambda: r.geometry, lambda: r.geojson()["geometry"]):
        try:
            ring = get()["coordinates"][0]
            return [[float(x), float(y)] for x, y, *_ in ring]
        except (AttributeError, KeyError, IndexError, TypeError, ValueError):
            continue
    return None


def _check_deps():
    missing = []
    for m in ("numpy", "h5py", "aiohttp", "zarr", "pyproj", "yaml", "shapely"):
        try:
            __import__(m)
        except ImportError:
            missing.append({"yaml": "pyyaml"}.get(m, m))
    if missing:
        raise ImportError(f"missing packages: {', '.join(missing)}  ->  pip install -r requirements-retrieval.txt")


def _size(p) -> int | None:
    for key, scale in (("bytes", 1), ("sizeMB", 1e6)):
        v = p.get(key)
        if isinstance(v, dict):
            try:
                v = sum(float(x.get("bytes", 0)) for x in v.values())
            except (AttributeError, TypeError, ValueError):
                v = None
        try:
            if v is not None and float(v) > 0:
                return int(float(v) * scale)
        except (TypeError, ValueError):
            pass
    return None
