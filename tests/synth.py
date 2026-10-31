"""Synthetic NISAR-like HDF5 + a local range server that mimics ASF's behaviour:
URS-style auth on the data host, 302 to a 'presigned' URL on another host that REJECTS an
Authorization header (like S3), Range support, injectable latency / failures / expiry."""
from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import h5py
import numpy as np
from aiohttp import web

GRID = "science/LSAR/GUNW/grids/frequencyA"
EPSG = 32611
X0, Y0, DX = 300000.0, 4000000.0, 80.0


def make_granule(path: Path, ny=1200, nx=1000, seed=0, paged=True):
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:ny, 0:nx].astype("f4")
    smooth = (np.sin(xx / 90) + np.cos(yy / 70)).astype("f4")
    kw = dict(fs_strategy="page", fs_page_size=1 << 20) if paged else {}
    with h5py.File(path, "w", **kw) as f:
        g = f.create_group(f"{GRID}/unwrappedInterferogram/HH")
        top = f[GRID]
        top["xCoordinates"] = X0 + DX * np.arange(nx)
        top["yCoordinates"] = Y0 - DX * np.arange(ny)
        p = top.create_dataset("projection", data=np.uint32(EPSG)); p.attrs["epsg_code"] = EPSG
        g.create_dataset("unwrappedPhase", data=smooth + rng.normal(0, .05, (ny, nx)).astype("f4"),
                         chunks=(256, 256), compression="gzip", shuffle=True)
        g.create_dataset("coherenceMagnitude", data=rng.random((ny, nx)).astype("f4"),
                         chunks=(128, 128), compression="gzip", shuffle=True)
        g.create_dataset("connectedComponents", data=(smooth > 0).astype("u4"),
                         chunks=(256, 256), compression="gzip")                 # deflate, no shuffle
        e = g.create_dataset("sparse", shape=(ny, nx), dtype="f4", chunks=(256, 256),
                             fillvalue=-9999.0, compression="gzip")
        e[0:256, 0:256] = 1.0                                                    # rest never written
        g.create_dataset("rawplain", data=smooth, chunks=(256, 256))              # chunked, no filters
        g.create_dataset("complexLayer", data=(smooth + 1j * smooth).astype("c8"), chunks=(256, 256),
                         compression="gzip", shuffle=True)
        g.create_dataset("contig", data=smooth)                                   # contiguous -> fallback
        # second grid with a 3-D cube (radarGrid-like), coarser spacing
        rg = f.create_group("science/LSAR/GUNW/metadata/radarGrid")
        rg["xCoordinates"] = X0 + 4 * DX * np.arange(nx // 4)
        rg["yCoordinates"] = Y0 - 4 * DX * np.arange(ny // 4)
        rg["heightAboveEllipsoid"] = np.array([0.0, 500.0, 1000.0])
        rp = rg.create_dataset("projection", data=np.uint32(EPSG)); rp.attrs["epsg_code"] = EPSG
        cube = rng.random((3, ny // 4, nx // 4)).astype("f4")
        rg.create_dataset("incidenceAngle", data=cube, chunks=(1, 64, 64), compression="gzip", shuffle=True)
    return path


def lonlat_box(ix0, iy0, ix1, iy1):
    """bbox (W,S,E,N) in lon/lat covering pixel range [ix0:ix1, iy0:iy1] of the main grid."""
    from pyproj import Transformer
    tr = Transformer.from_crs(EPSG, 4326, always_xy=True)
    xs = [X0 + DX * ix0, X0 + DX * ix1]
    ys = [Y0 - DX * iy1, Y0 - DX * iy0]
    lon, lat = tr.transform([xs[0], xs[1], xs[0], xs[1]], [ys[0], ys[0], ys[1], ys[1]])
    return (min(lon), min(lat), max(lon), max(lat))


class RangeServer:
    def __init__(self, root: Path, latency_s=0.0, bandwidth_mbps=None, token="test"):
        self.root, self.latency, self.bw, self.token = Path(root), latency_s, bandwidth_mbps, token
        self.requests = 0          # range GETs against /signed
        self.data_hits = 0         # hits on the auth'd entry point (redirect resolves)
        self.bytes_sent = 0
        self.fail_next = 0         # next N signed requests answer 503
        self.sig = "v1"            # rotate to emulate presigned-URL expiry (old sig -> 403)
        self.saw_auth_on_signed = False
        self.online = True
        self._lock = threading.Lock()
        self.loop = asyncio.new_event_loop()
        self.port = None
        ready = threading.Event()
        threading.Thread(target=self._run, args=(ready,), daemon=True).start()
        ready.wait(10)

    def url(self, name: str) -> str:
        return f"http://127.0.0.1:{self.port}/data/{name}"

    def _run(self, ready):
        asyncio.set_event_loop(self.loop)
        app = web.Application()
        app.router.add_get("/data/{name}", self._data)
        app.router.add_get("/signed/{name}", self._signed)
        runner = web.AppRunner(app)
        self.loop.run_until_complete(runner.setup())
        site = web.TCPSite(runner, "127.0.0.1", 0)
        self.loop.run_until_complete(site.start())
        self.port = site._server.sockets[0].getsockname()[1]
        ready.set()
        self.loop.run_forever()

    async def _data(self, req):
        with self._lock:
            self.data_hits += 1
        if not self.online:
            return web.Response(status=503)
        if req.headers.get("Authorization") != f"Bearer {self.token}":
            return web.Response(status=401)
        raise web.HTTPFound(f"http://localhost:{self.port}/signed/{req.match_info['name']}?sig={self.sig}")

    async def _signed(self, req):
        if "Authorization" in req.headers:           # S3 rejects double auth; so do we
            with self._lock:
                self.saw_auth_on_signed = True
            return web.Response(status=400, text="auth header on presigned url")
        if not self.online:
            return web.Response(status=503)
        if req.query.get("sig") != self.sig:
            return web.Response(status=403)
        with self._lock:
            self.requests += 1
            if self.fail_next > 0:
                self.fail_next -= 1
                return web.Response(status=503)
        if self.latency:
            await asyncio.sleep(self.latency)
        data = (self.root / req.match_info["name"]).read_bytes() if False else None
        path = self.root / req.match_info["name"]
        size = path.stat().st_size
        rng = req.headers.get("Range", "")
        if not rng.startswith("bytes="):
            body = path.read_bytes()
            return web.Response(body=body, status=200)
        a, b = rng[6:].split("-")
        a = int(a); b = min(int(b), size - 1) if b else size - 1
        with open(path, "rb") as f:
            f.seek(a); body = f.read(b - a + 1)
        if self.bw:
            await asyncio.sleep(len(body) / (self.bw * 1e6 / 8))
        with self._lock:
            self.bytes_sent += len(body)
        return web.Response(body=body, status=206, headers={"Content-Range": f"bytes {a}-{b}/{size}"})

    def reset(self):
        with self._lock:
            self.requests = self.data_hits = self.bytes_sent = 0
