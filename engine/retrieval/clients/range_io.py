"""Async HTTP range IO: one background event loop, pooled keep-alive connections, redirect
resolved once (presigned URLs reused), coalesced + concurrent range GETs, retry/backoff.

Nothing here knows about HDF5. Callers hand in (offset, length) lists and get bytes back.
"""
from __future__ import annotations

import asyncio
import random
import sys
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

import aiohttp

from ..auth.secrets import EarthdataAuth


class RangeError(Exception):
    pass


class AuthError(RangeError):
    pass


# --------------------------------------------------------------------------- loop thread
class LoopThread:
    """A private asyncio loop on a daemon thread; sync code calls .run(coro)."""

    def __init__(self, name: str = "nisar-io"):
        self.loop = asyncio.new_event_loop()
        self._t = threading.Thread(target=self._main, name=name, daemon=True)
        self._t.start()

    def _main(self):
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_forever()
        finally:
            if sys.platform != "win32":           # release the loop's self-pipe sockets (a leak otherwise);
                try:                              # not on Windows: ProactorEventLoop.close() can spin on open sockets
                    self.loop.run_until_complete(self.loop.shutdown_asyncgens())
                finally:
                    self.loop.close()

    def run(self, coro, timeout: float | None = None):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def close(self):
        self.loop.call_soon_threadsafe(self.loop.stop)


# --------------------------------------------------------------------------- metrics
@dataclass
class Metrics:
    requests: int = 0
    bytes_fetched: int = 0     # on the wire, includes coalesced gap bytes
    bytes_useful: int = 0      # bytes the caller actually asked for
    retries: int = 0
    resolves: int = 0
    seconds_in_flight: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, **kw):
        with self._lock:
            for k, v in kw.items():
                setattr(self, k, getattr(self, k) + v)

    def snapshot(self) -> dict:
        return {k: getattr(self, k) for k in
                ("requests", "bytes_fetched", "bytes_useful", "retries", "resolves")}


# --------------------------------------------------------------------------- coalescing
def coalesce(ranges: list[tuple[int, int]], gap: int, max_req: int):
    """[(offset, length)] -> [(start, end, [original indices])], merging near neighbours."""
    order = sorted(range(len(ranges)), key=lambda i: ranges[i][0])
    out: list[tuple[int, int, list[int]]] = []
    for i in order:
        off, ln = ranges[i]
        end = off + ln
        if out:
            s, e, idx = out[-1]
            if off - e <= gap and max(e, end) - s <= max_req:
                out[-1] = (s, max(e, end), idx + [i])
                continue
        out.append((off, end, [i]))
    return out


# --------------------------------------------------------------------------- HTTP core
class HttpIO:
    def __init__(self, auth: EarthdataAuth, net: dict, loop: LoopThread | None = None,
                 metrics: Metrics | None = None):
        self.auth, self.net = auth, net
        self.lt = loop or LoopThread()
        self.metrics = metrics or Metrics()
        self._session: aiohttp.ClientSession | None = None
        self._sem: asyncio.Semaphore | None = None
        self._resolved: dict[str, tuple[str, float, int | None]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._suffixes = tuple(net.get("auth_hosts", []))

    # -- lazily created inside the loop thread
    async def _ensure(self):
        if self._session is None:
            n = int(self.net["concurrency"])
            conn = aiohttp.TCPConnector(limit=n * 2, limit_per_host=n, ttl_dns_cache=600,
                                        keepalive_timeout=60)
            to = aiohttp.ClientTimeout(total=None, connect=float(self.net["connect_timeout_s"]),
                                       sock_read=float(self.net["stall_timeout_s"]))
            self._session = aiohttp.ClientSession(connector=conn, timeout=to, auto_decompress=False)
            self._sem = asyncio.Semaphore(n)

    def _headers_for(self, url: str) -> dict[str, str]:
        host = urlsplit(url).hostname or ""
        if any(host == s or host.endswith("." + s) for s in self._suffixes):
            return self.auth.header()
        return {}  # never leak credentials to presigned S3 / unknown hosts

    async def resolve(self, url: str, force: bool = False) -> tuple[str, int | None]:
        """Follow redirects by hand, attaching credentials only to allow-listed hosts.
        Returns (final_url, total_size). Cached for net.url_ttl_s."""
        await self._ensure()
        hit = self._resolved.get(url)
        if hit and not force and time.time() - hit[1] < float(self.net["url_ttl_s"]):
            return hit[0], hit[2]
        lock = self._locks.setdefault(url, asyncio.Lock())
        async with lock:
            hit = self._resolved.get(url)
            if hit and not force and time.time() - hit[1] < float(self.net["url_ttl_s"]):
                return hit[0], hit[2]
            cur = url
            for _ in range(10):
                hdr = {"Range": "bytes=0-0", **self._headers_for(cur)}
                async with self._session.get(cur, headers=hdr, allow_redirects=False) as r:
                    if r.status in (301, 302, 303, 307, 308):
                        cur = urljoin(cur, r.headers["Location"])
                        await r.read()
                        continue
                    if r.status in (401, 403):
                        raise AuthError(f"HTTP {r.status} resolving {urlsplit(url).netloc}: check Earthdata "
                                        "token/credentials and that the ASF Data Access app is authorized")
                    if r.status not in (200, 206):
                        raise RangeError(f"HTTP {r.status} resolving {url}")
                    size = None
                    cr = r.headers.get("Content-Range", "")
                    if "/" in cr and cr.rsplit("/", 1)[1].isdigit():
                        size = int(cr.rsplit("/", 1)[1])
                    elif r.status == 200 and r.headers.get("Content-Length"):
                        size = int(r.headers["Content-Length"])
                    await r.read()
                    self._resolved[url] = (cur, time.time(), size)
                    self.metrics.add(resolves=1)
                    return cur, size
            raise RangeError("too many redirects")

    async def _get(self, url: str, start: int, end: int) -> bytes:
        """One range GET [start, end) with retry. Re-resolves the URL on 401/403."""
        want = end - start
        last: Exception | None = None
        reresolved = False
        for attempt in range(int(self.net["retries"]) + 1):
            try:
                final, _ = await self.resolve(url)
                hdr = {"Range": f"bytes={start}-{end - 1}", **self._headers_for(final)}
                t0 = time.perf_counter()
                async with self._sem:
                    async with self._session.get(final, headers=hdr, allow_redirects=False) as r:
                        if r.status in (401, 403) and not reresolved:
                            reresolved = True
                            await r.read()
                            await self.resolve(url, force=True)
                            continue
                        if r.status in (401, 403):
                            raise AuthError(f"HTTP {r.status} on range read")
                        if r.status == 416:
                            raise RangeError(f"range {start}-{end} beyond end of file")
                        if r.status in (408, 429) or r.status >= 500:
                            raise aiohttp.ClientResponseError(r.request_info, (), status=r.status)
                        if r.status == 200 and start > 0:
                            raise RangeError("server ignored Range header (HTTP 200)")
                        if r.status not in (200, 206):
                            raise RangeError(f"HTTP {r.status}")
                        body = await r.read()
                if r.status == 200:
                    body = body[start:end]
                if len(body) != want:
                    raise aiohttp.ClientPayloadError(f"short read {len(body)}/{want}")
                self.metrics.add(requests=1, bytes_fetched=want, seconds_in_flight=time.perf_counter() - t0)
                return body
            except (AuthError, RangeError):
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as ex:
                last = ex
                self.metrics.add(retries=1)
                await asyncio.sleep(min(20.0, 0.5 * 2 ** attempt) * (0.5 + random.random()))
        raise RangeError(f"gave up on bytes {start}-{end} after retries: {last!r}")

    async def read_ranges(self, url: str, ranges: list[tuple[int, int]]) -> list[memoryview]:
        """Fetch many (offset, length) ranges: coalesce, run concurrently, slice back apart."""
        if not ranges:
            return []
        await self._ensure()
        plan = coalesce(ranges, int(self.net["coalesce_gap_bytes"]), int(self.net["max_request_bytes"]))
        blobs = await asyncio.gather(*(self._get(url, s, e) for s, e, _ in plan))
        out: list[memoryview | None] = [None] * len(ranges)
        for (s, e, idx), blob in zip(plan, blobs):
            mv = memoryview(blob)
            for i in idx:
                off, ln = ranges[i]
                out[i] = mv[off - s: off - s + ln]
        self.metrics.add(bytes_useful=sum(ln for _, ln in ranges))
        return out  # type: ignore[return-value]

    async def stream_ranges(self, url: str, ranges: list[tuple[int, int]]):
        """Like read_ranges but yields (indices, memoryviews) as each coalesced request finishes,
        so decoding can overlap with the network."""
        if not ranges:
            return
        await self._ensure()
        plan = coalesce(ranges, int(self.net["coalesce_gap_bytes"]), int(self.net["max_request_bytes"]))

        async def one(item):
            return item, await self._get(url, item[0], item[1])

        tasks = [asyncio.ensure_future(one(p)) for p in plan]
        try:
            for fut in asyncio.as_completed(tasks):
                (s, e, idx), blob = await fut
                mv = memoryview(blob)
                yield idx, [mv[ranges[i][0] - s: ranges[i][0] - s + ranges[i][1]] for i in idx]
        finally:                      # on error/cancel: stop siblings and consume their exceptions
            for t in tasks:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        self.metrics.add(bytes_useful=sum(ln for _, ln in ranges))

    async def aclose(self):
        if self._session:
            await self._session.close()

    def close(self):
        if getattr(self, "_closed", False):
            return
        self._closed = True
        try:
            self.lt.run(self.aclose(), timeout=10)
        finally:
            self.lt.close()


# --------------------------------------------------------------------------- sync file-like for h5py
class RangeFile:
    """Read-only, seekable, block-cached file object over a URL. Used for the one-time layout
    scan and for the h5py fallback path; the hot path never touches it."""

    def __init__(self, io: HttpIO, url: str, block: int | None = None, max_blocks: int = 64):
        self.io, self.url = io, url
        self.block = int(block or io.net["block_size_bytes"])
        _, size = io.lt.run(io.resolve(url))
        if size is None:
            raise RangeError("server did not report file size")
        self.size, self.pos, self.closed = size, 0, False
        self._blocks: dict[int, bytes] = {}
        self._max = max_blocks
        self._lock = threading.Lock()
        self.fetches: list[tuple[int, int, float]] = []   # (blocks, bytes, seconds) per fill

    def warm(self, head_blocks: int = 2, tail_blocks: int = 2):
        """One parallel round trip for the file's first/last blocks (HDF5 metadata lives there)."""
        nb = (self.size + self.block - 1) // self.block
        want = sorted(set(range(min(head_blocks, nb))) | set(range(max(0, nb - tail_blocks), nb)))
        self._fill_blocks(want)

    # file protocol
    def readable(self): return True
    def seekable(self): return True
    def writable(self): return False
    def tell(self): return self.pos
    def close(self): self.closed = True
    def flush(self): pass

    def seek(self, off, whence=0):
        self.pos = {0: off, 1: self.pos + off, 2: self.size + off}[whence]
        return self.pos

    def _fill(self, first: int, last: int):
        self._fill_blocks(range(first, last + 1))

    def _fill_blocks(self, blocks):
        need = [b for b in blocks if b not in self._blocks]
        if not need:
            return
        rng = [(b * self.block, min(self.block, self.size - b * self.block)) for b in need]
        t0 = time.perf_counter()
        got = self.io.lt.run(self.io.read_ranges(self.url, rng))
        self.fetches.append((len(need), sum(r[1] for r in rng), time.perf_counter() - t0))
        with self._lock:
            for b, mv in zip(need, got):
                self._blocks[b] = bytes(mv)
            while len(self._blocks) > self._max:
                self._blocks.pop(next(iter(self._blocks)))

    def read(self, n=-1):
        if n is None or n < 0:
            n = self.size - self.pos
        n = max(0, min(n, self.size - self.pos))
        if n == 0:
            return b""
        b0, b1 = self.pos // self.block, (self.pos + n - 1) // self.block
        self._fill(b0, b1)
        with self._lock:
            buf = b"".join(self._blocks[b] for b in range(b0, b1 + 1))
        s = self.pos - b0 * self.block
        self.pos += n
        return buf[s:s + n]

    def readinto(self, b):
        data = self.read(len(b))
        b[:len(data)] = data
        return len(data)
