"""
Data retrieval with a flight recorder.

Everything that touches the network or the disk goes through a `Trace`, so the report can show
exactly what was asked, how long it took and how many bytes moved. Sources handled:

  * ASF / CMR granule search          (needs `asf_search`; or reuse a saved nisar_results.csv)
  * remote HDF5 over HTTPS            (needs `earthaccess`, `fsspec`, `h5py`, an Earthdata login)
  * local HDF5                        (needs `h5py`)
  * .npz clips from nisar_region.py / nisar_fast.py   (numpy only)
"""
from __future__ import annotations

import csv
import math
import os
import re
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime

import numpy as np

from . import anatomy, geo
from .model import Granule, LayerData

MAX_EVENTS = 1500
TS = re.compile(r"\d{8}T\d{6}")
FN = re.compile(r"_(\d{3})_(\d{3})_([AD])_(\d{3})_(\d{3})_")       # refCycle track dir frame secCycle
_VERSION_TAIL = re.compile(r"_([A-Z])(\d{5})(_[A-Z]_[A-Z]_[A-Z]_\d{3})$")


# ----------------------------------------------------------------------------- flight recorder

class Trace:
    """Thread-safe event + log recorder."""

    def __init__(self):
        self.t_start = time.time()
        self.events: list = []
        self.log: list = []
        self.dropped = 0
        self.requests = 0
        self.bytes = 0
        self._lock = threading.Lock()

    def event(self, kind, name, t0, t1, status="ok", nbytes=None, detail=""):
        with self._lock:
            if len(self.events) < MAX_EVENTS:
                self.events.append({"kind": kind, "name": name, "t0": t0, "t1": t1, "status": status,
                                    "bytes": nbytes, "detail": detail})
            else:
                self.dropped += 1

    @contextmanager
    def step(self, kind, name):
        ev = {"kind": kind, "name": name, "t0": time.time(), "t1": None, "status": "ok", "bytes": None, "detail": ""}
        with self._lock:
            self.events.append(ev)
        try:
            yield ev
        except Exception as ex:
            ev["status"] = "error"
            ev["detail"] = f"{type(ex).__name__}: {ex}"
            self.note("ERROR", f"{kind} '{name}' failed: {type(ex).__name__}: {ex}")
            raise
        finally:
            ev["t1"] = time.time()

    def note(self, level, msg):
        with self._lock:
            self.log.append({"t": time.time(), "level": level, "msg": str(msg)})


# ----------------------------------------------------------------------------- credentials / session

def load_env():
    """Read EARTHDATA_* from a .env next to the cwd or this package (python-dotenv optional)."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for p in (os.path.join(os.getcwd(), ".env"), os.path.join(here, ".env")):
        if not os.path.exists(p):
            continue
        try:
            from dotenv import load_dotenv
            load_dotenv(p, override=False)
        except ImportError:
            for line in open(p, encoding="utf-8", errors="replace"):
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip("'\""))


def earthdata_fs(trace: Trace):
    """fsspec HTTPS filesystem authenticated against Earthdata, or None (reason goes into the trace)."""
    load_env()
    user, pw = os.environ.get("EARTHDATA_USERNAME"), os.environ.get("EARTHDATA_PASSWORD")
    if not (user and pw):
        trace.note("WARN", "EARTHDATA_USERNAME / EARTHDATA_PASSWORD not set (env or .env): remote files can't be opened")
        return None
    try:
        import earthaccess
    except ImportError:
        trace.note("WARN", "earthaccess is not installed (pip install earthaccess fsspec aiohttp)")
        return None
    with trace.step("login", "Earthdata login") as ev:
        earthaccess.login(strategy="environment")
        fs = earthaccess.get_fsspec_https_session()
        ev["detail"] = "ok"
    return fs


# ----------------------------------------------------------------------------- granules

def _iso(t):
    return f"{t[:4]}-{t[4:6]}-{t[6:8]}T{t[9:11]}:{t[11:13]}:{t[13:15]}Z"


def _ts(iso):
    return datetime.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S")


def parse_name(fid: str) -> dict:
    """Pull dates / track / frame / direction out of a NISAR granule id."""
    ts = [_iso(t) for t in TS.findall(fid)]
    m = FN.search(fid)
    out = {"ref": None, "sec": None, "t0": ts[0] if ts else "", "t1": ts[1] if len(ts) > 1 else (ts[0] if ts else ""),
           "baseline": None, "dir": "?", "track": None, "frame": None, "ref_cycle": None, "sec_cycle": None}
    if len(ts) >= 4:                                   # interferogram: reference pair + secondary pair
        ref, sec = ts[0], ts[2]
        out.update(ref=ref, sec=sec, t0=min(ref, sec), t1=max(ref, sec),
                   baseline=abs((_ts(sec) - _ts(ref)).total_seconds()) / 86400.0)
    elif ts:
        out["ref"] = ts[0]
    if m:
        out.update(dir=m.group(3), track=int(m.group(2)), frame=int(m.group(4)),
                   ref_cycle=int(m.group(1)), sec_cycle=int(m.group(5)))
    return out


def _rings(geom):
    """GeoJSON-ish geometry -> list of rings of (lon, lat)."""
    if not geom:
        return None
    try:
        if hasattr(geom, "get"):
            kind, co = geom.get("type"), geom.get("coordinates")
        else:
            gi = getattr(geom, "__geo_interface__", None)
            kind, co = (gi or {}).get("type"), (gi or {}).get("coordinates")
        if kind == "Polygon":
            return [[(float(p[0]), float(p[1])) for p in co[0]]]
        if kind == "MultiPolygon":
            return [[(float(p[0]), float(p[1])) for p in poly[0]] for poly in co]
    except Exception:
        return None
    return None


def _size_mb(p):
    for key, scale in (("bytes", 1), ("sizeMB", 1e6)):
        v = p.get(key)
        if isinstance(v, dict):
            try:
                v = sum(float(x.get("bytes", 0)) for x in v.values())
            except (AttributeError, TypeError, ValueError):
                v = None
        try:
            if v is not None and float(v) > 0:
                return float(v) * scale / 1e6
        except (TypeError, ValueError):
            pass
    return None


def granule_from_result(i, r) -> Granule:
    p = r.properties
    fid = re.sub(r"\.h5$", "", str(p.get("fileID") or p.get("fileName") or i))
    n = parse_name(fid)
    pol = p.get("polarization") or p.get("mainBandPolarization")
    if isinstance(pol, (list, tuple)):
        pol = "/".join(str(x) for x in pol)
    t0 = n["t0"] or str(p.get("startTime") or "")
    return Granule(
        id=fid, name=str(p.get("fileName") or os.path.basename(str(p.get("url")))), url=p.get("url"),
        level=p.get("processingLevel"), t0=t0, t1=n["t1"] or str(p.get("stopTime") or t0), ref=n["ref"], sec=n["sec"],
        baseline_days=n["baseline"], size_mb=_size_mb(p),
        dir=(n["dir"] if n["track"] is not None else str(p.get("flightDirection") or "?")[:1]).upper(),
        track=n["track"] if n["track"] is not None else p.get("pathNumber"),
        frame=n["frame"] if n["frame"] is not None else p.get("frameNumber"),
        ref_cycle=n["ref_cycle"], sec_cycle=n["sec_cycle"], pol=pol, maturity=p.get("dataMaturity"),
        footprint=_rings(getattr(r, "geometry", None)), props={k: str(v)[:300] for k, v in dict(p).items()})


def drop_older_versions(items, key=lambda g: g.id):
    """Same scene processed more than once -> keep the highest version number. Returns (kept, n_dropped)."""
    best = {}
    for i, it in enumerate(items):
        fid = key(it)
        m = _VERSION_TAIL.search(fid)
        k, ver = (fid[:m.start()] + m.group(3), int(m.group(2))) if m else (fid, 0)
        if k not in best or ver > best[k][0]:
            best[k] = (ver, i)
    keep = sorted(i for _, i in best.values())
    return [items[i] for i in keep], len(items) - len(keep)


def search_granules(bbox, start=None, end=None, level=None, maturity=None, max_n=250, keep_all=False, trace=None):
    """Live ASF search. Returns (granules, info) where info has found / dropped / capped."""
    trace = trace or Trace()
    try:
        import asf_search as asf
    except ImportError:
        raise RuntimeError("asf_search is not installed (pip install asf_search), or pass --csv nisar_results.csv")
    w, s, e, n = bbox
    kw = {"dataset": "NISAR", "intersectsWith": f"POLYGON(({w} {s},{e} {s},{e} {n},{w} {n},{w} {s}))", "maxResults": max_n}
    if start:
        kw["start"] = start
    if end:
        kw["end"] = end
    if level:
        kw["processingLevel"] = [v.strip() for v in level.split(",")]
    if maturity:
        kw["dataMaturity"] = maturity
    with trace.step("search", "ASF search " + ", ".join(f"{k}={v}" for k, v in kw.items() if k != "intersectsWith")) as ev:
        results = asf.search(**kw)
        ev["detail"] = f"{len(results)} hit(s)"
    gs = [granule_from_result(i, r) for i, r in enumerate(results)]
    found, dropped = len(gs), 0
    if not keep_all:
        gs, dropped = drop_older_versions(gs)
    return gs, {"found": found, "dropped": dropped, "capped": found >= max_n, "source": "asf_search"}


def granules_from_csv(path: str, trace=None):
    """Re-use the CSV that nisar_region.py / nisar_fast.py write (nisar_results.csv)."""
    trace = trace or Trace()
    with trace.step("search", f"read {os.path.basename(path)}") as ev:
        rows = list(csv.DictReader(open(path, newline="", encoding="utf-8-sig")))
        ev["detail"] = f"{len(rows)} row(s)"
    gs = []
    for i, r in enumerate(rows):
        fid = re.sub(r"\.h5$", "", str(r.get("fileID") or os.path.basename(r.get("url") or "") or i))
        n = parse_name(fid)
        try:
            size = float(str(r.get("size_mb", "")).replace(",", "")) or None
        except ValueError:
            size = None
        t0 = n["t0"] or str(r.get("start") or "")
        gs.append(Granule(
            id=fid, name=os.path.basename(r.get("url") or "") or fid + ".h5", url=r.get("url") or None,
            level=r.get("level") or None, t0=t0, t1=n["t1"] or str(r.get("stop") or t0), ref=n["ref"], sec=n["sec"],
            baseline_days=n["baseline"], size_mb=size,
            dir=(n["dir"] if n["track"] is not None else str(r.get("direction") or "?")[:1]).upper(),
            track=n["track"] if n["track"] is not None else _int(r.get("path")),
            frame=n["frame"] if n["frame"] is not None else _int(r.get("frame")),
            ref_cycle=n["ref_cycle"], sec_cycle=n["sec_cycle"], pol=r.get("polarization") or None,
            props={k: str(v)[:300] for k, v in r.items()}))
    return gs, {"found": len(gs), "dropped": 0, "capped": False, "source": os.path.basename(path)}


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


# ----------------------------------------------------------------------------- opening files

class H5Handle:
    """Context manager around a local / remote HDF5 (or an already-open h5py-like object)."""

    def __init__(self, src, trace: Trace, fs=None, block_mb: int = 4, display: str = ""):
        self.src, self.trace, self.fs, self.block = src, trace, fs, block_mb << 20
        self.remote = isinstance(src, str) and src.startswith(("http://", "https://"))
        self.display = display or (src if isinstance(src, str) else type(src).__name__)
        self.h5, self._f = None, None
        self.reader = "h5py"

    def __enter__(self):
        if not isinstance(self.src, str):                         # already an open file-like object
            self.h5, self.reader = self.src, getattr(self.src, "reader", "in-memory object")
            return self
        try:
            import h5py
        except ImportError:
            raise RuntimeError("h5py is not installed (pip install h5py)")
        with self.trace.step("open", f"open {os.path.basename(self.display)}") as ev:
            if not self.remote:
                self.h5 = h5py.File(self.src, "r")
                ev["detail"] = "local file"
            else:
                if self.fs is None:
                    raise RuntimeError("no Earthdata session: set EARTHDATA_USERNAME / EARTHDATA_PASSWORD")
                self._f = self.fs.open(self.src, mode="rb", cache_type="blockcache", block_size=self.block)
                self._patch(self._f)
                try:
                    self.h5 = h5py.File(self._f, "r", page_buf_size=16 << 20, rdcc_nbytes=4 << 20)
                except (ValueError, OSError):                     # file is not paged: plain open
                    self.h5 = h5py.File(self._f, "r")
                ev["detail"] = f"remote, block {self.block >> 20} MB"
                ev["bytes"] = self.trace.bytes
        return self

    def _patch(self, f):
        """Record every byte-range request the block cache sends to the server."""
        cache = getattr(f, "cache", None)
        fetch = getattr(cache, "fetcher", None)
        if fetch is None:
            self.trace.note("WARN", "this fsspec version does not expose the block fetcher: individual requests are not traced")
            return
        tr = self.trace

        def traced(a, b, *rest):
            t = time.time()
            try:
                d = fetch(a, b, *rest)
            except Exception as ex:
                tr.event("request", f"bytes {a:,}..{b:,}", t, time.time(), "error", nbytes=b - a, detail=f"{type(ex).__name__}: {ex}")
                raise
            n = len(d) if d is not None else b - a
            tr.event("request", f"bytes {a:,}..{b:,}", t, time.time(), "ok", nbytes=n)
            tr.requests += 1
            tr.bytes += n
            return d

        cache.fetcher = traced

    def __exit__(self, *exc):
        for obj in (self.h5 if isinstance(self.src, str) else None, self._f):
            try:
                if obj is not None:
                    obj.close()
            except Exception:
                pass
        return False


# ----------------------------------------------------------------------------- link probe

def probe_link(url: str, trace: Trace) -> dict:
    """Measure the raw link to the server: single range requests, then 1/2/4 parallel ones."""
    import concurrent.futures as cf
    try:
        import earthaccess
    except ImportError:
        raise RuntimeError("earthaccess is not installed")
    load_env()
    earthaccess.login(strategy="environment")
    mk = earthaccess.get_requests_https_session
    sess, size, rows, headers = mk(), None, [], {}

    def rng(s, off, n):
        t = time.time()
        r = s.get(url, headers={"Range": f"bytes={off}-{off + n - 1}"}, stream=True, timeout=(30, 120))
        first, got = time.time() - t, 0
        for blk in r.iter_content(256 * 1024):
            got += len(blk)
        return r, first, time.time() - t, got

    for label, off, n in (("4 KB (incl. login redirect)", 0, 4096), ("4 KB again", 0, 4096),
                          ("1 MB at start", 0, 1 << 20), ("8 MB at start", 0, 8 << 20)):
        with trace.step("probe", label) as ev:
            try:
                r, first, total, got = rng(sess, off, n)
                cr = r.headers.get("Content-Range", "")
                if "/" in cr:
                    size = int(cr.split("/")[-1])
                if not headers:
                    headers = {k: r.headers.get(k) for k in ("Server", "Via", "X-Cache", "Accept-Ranges", "Content-Range") if r.headers.get(k)}
                rows.append({"test": label, "status": r.status_code, "first_byte_s": round(first, 2), "total_s": round(total, 2),
                             "mb": round(got / 1e6, 2), "mb_s": round(got / 1e6 / max(total, 1e-9), 2)})
                ev["bytes"] = got
            except Exception as ex:
                rows.append({"test": label, "status": "ERR", "first_byte_s": None, "total_s": None, "mb": None, "mb_s": None, "error": f"{type(ex).__name__}: {ex}"})
                raise
    base = 100_000_000
    sessions = [mk() for _ in range(4)]
    with cf.ThreadPoolExecutor(4) as ex:                          # warm every session outside the timing
        list(ex.map(lambda i: rng(sessions[i], 0, 4096), range(4)))
    for n in (1, 2, 4):
        with trace.step("probe", f"{n} parallel x 4 MB") as ev:
            t = time.time()
            with cf.ThreadPoolExecutor(n) as ex:
                res = list(ex.map(lambda i: rng(sessions[i], base + i * 40_000_000, 4 << 20), range(n)))
            dt, tot = time.time() - t, sum(r[3] for r in res)
            rows.append({"test": f"{n} parallel x 4 MB", "status": "ok", "first_byte_s": None, "total_s": round(dt, 2),
                         "mb": round(tot / 1e6, 2), "mb_s": round(tot / 1e6 / max(dt, 1e-9), 2)})
            ev["bytes"] = tot
    return {"rows": rows, "file_size": size, "headers": headers}


# ----------------------------------------------------------------------------- windows & layer reads

def window_for_bbox(grp, bbox, epsg):
    """Pixel window of a lon/lat box on this group's x/y axes + lon/lat of the window's UL, UR, LL corners."""
    tr = geo.make_transformer(epsg)
    if tr is None:
        raise ValueError(f"cannot map a lon/lat box onto EPSG:{epsg} (pip install pyproj)")
    xs = np.asarray(grp["xCoordinates"][()], dtype="float64")
    ys = np.asarray(grp["yCoordinates"][()], dtype="float64")
    w, s, e, n = bbox
    t = np.linspace(0, 1, 21)
    lon = np.r_[w + (e - w) * t, w + (e - w) * t, np.full(21, w), np.full(21, e)]
    lat = np.r_[np.full(21, s), np.full(21, n), s + (n - s) * t, s + (n - s) * t]
    px, py = tr.fwd(lon, lat)
    dx = float(xs[1] - xs[0]) if xs.size > 1 else 1.0
    dy = float(ys[1] - ys[0]) if ys.size > 1 else 1.0
    ix = np.where((xs >= np.min(px) - abs(dx)) & (xs <= np.max(px) + abs(dx)))[0]
    iy = np.where((ys >= np.min(py) - abs(dy)) & (ys <= np.max(py) + abs(dy)))[0]
    if not ix.size or not iy.size:
        raise ValueError("the box does not overlap this layer's grid")
    return _win_with_corners(xs, ys, dx, dy, int(ix.min()), int(ix.max()) + 1, int(iy.min()), int(iy.max()) + 1, tr)


def full_window(grp, epsg):
    xs = np.asarray(grp["xCoordinates"][()], dtype="float64")
    ys = np.asarray(grp["yCoordinates"][()], dtype="float64")
    dx = float(xs[1] - xs[0]) if xs.size > 1 else 1.0
    dy = float(ys[1] - ys[0]) if ys.size > 1 else 1.0
    return _win_with_corners(xs, ys, dx, dy, 0, xs.size, 0, ys.size, geo.make_transformer(epsg) if epsg else None)


def _win_with_corners(xs, ys, dx, dy, x0, x1, y0, y1, tr):
    corners = None
    if tr is not None:
        L, R, T, B = xs[x0] - dx / 2, xs[x1 - 1] + dx / 2, ys[y0] - dy / 2, ys[y1 - 1] + dy / 2
        lon, lat = tr.inv([L, R, L], [T, T, B])
        corners = [[float(lon[i]), float(lat[i])] for i in range(3)]
    return x0, x1, y0, y1, corners


def make_layers(arr, *, path, category, full_shape, dtype, units, fill, step, window, corners, grid, secs,
                nbytes, nz, source) -> list:
    """Raw array -> LayerData list (complex data is split into amplitude and phase)."""
    a = np.asarray(arr)
    cx = np.iscomplexobj(a)
    bad = ~np.isfinite(a)
    if fill is not None and not cx and isinstance(fill, (int, float)) and np.isfinite(fill):
        bad |= (a == fill)
    outs = [("amplitude", "amp", np.abs(a)), ("phase", "phase", np.angle(a))] if cx else [("", "cat" if category in ("components", "mask") else "data", a)]
    layers = []
    for suffix, kind, x in outs:
        f = x.astype("float32")
        f[bad] = np.nan
        layers.append(LayerData(
            path=path, label=("/".join(path.strip("/").split("/")[-3:]) + (" " + suffix if suffix else "")), kind=kind, category=category, array=f,
            full_shape=tuple(full_shape), dtype=str(dtype), units=units, fill=fill, step=step, window=window, corners=corners,
            grid=grid, secs=secs, remote_bytes=nbytes, nz=nz, source=source))
    return layers


def read_layers(h5, rasters, bbox, max_px, trace: Trace, source="", notes=None, default_epsg=None) -> list:
    """Read the chosen raster entries (from anatomy.inspect_file) for the box; decimate to <= max_px each."""
    notes = notes if notes is not None else []
    wins, out = {}, []
    for ent in rasters:
        path, gp = ent["path"], ent["grid"]
        try:
            if gp not in wins:
                with trace.step("window", f"grid {gp}") as ev:
                    grp = h5[gp]
                    epsg = anatomy.find_epsg(grp)
                    inherited = epsg is None and default_epsg is not None
                    epsg = epsg if epsg is not None else default_epsg
                    if bbox:
                        wins[gp] = window_for_bbox(grp, bbox, epsg) + (epsg,)
                    else:
                        wins[gp] = full_window(grp, epsg) + (epsg,)
                    x0, x1, y0, y1, _, _ = wins[gp]
                    ev["detail"] = (f"EPSG {epsg}{' (inherited from another grid)' if inherited else ''}, "
                                    f"columns {x0}:{x1}, rows {y0}:{y1} ({x1 - x0}x{y1 - y0} px)")
            x0, x1, y0, y1, corners, epsg = wins[gp]
            grp_shape = (int(h5[gp]["yCoordinates"].shape[0]), int(h5[gp]["xCoordinates"].shape[0]))
            if tuple(ent["shape"][-2:]) != grp_shape:
                notes.append(f"{path}: size {ent['shape'][-2:]} differs from its grid {grp_shape}; skipped")
                continue
            nz = ent["shape"][0] if len(ent["shape"]) == 3 else None
            st = max(1, math.ceil(math.sqrt((x1 - x0) * (y1 - y0) / max(max_px, 1))))
            with trace.step("read", path.split("/")[-1]) as ev:
                obj, t0, b0 = h5[path], time.time(), trace.bytes
                a = obj[nz // 2, y0:y1:st, x0:x1:st] if nz else obj[y0:y1:st, x0:x1:st]
                secs = time.time() - t0
                ev["bytes"] = trace.bytes - b0 if trace.bytes > b0 else None
                ev["detail"] = f"{ent['dtype']} {tuple(a.shape)} step {st}" + (f", slice {nz // 2}/{nz}" if nz else "")
            attrs = getattr(obj, "attrs", {})
            units = anatomy.to_py(attrs["units"]) if "units" in attrs else None
            fill = anatomy.to_py(attrs["_FillValue"]) if "_FillValue" in attrs else None
            out += make_layers(a, path=path, category=ent["cat"], full_shape=ent["shape"], dtype=ent["dtype"], units=units, fill=fill,
                               step=st, window=(x0, x1, y0, y1), corners=corners, grid=gp, secs=secs,
                               nbytes=(trace.bytes - b0) if trace.bytes > b0 else None, nz=nz, source=source)
        except Exception as ex:
            notes.append(f"{path}: {type(ex).__name__}: {ex}")
            trace.note("ERROR", f"read {path}: {type(ex).__name__}: {ex}")
    return out


def pick_rasters(rasters, pattern=None, max_layers=24):
    """Default: the categories every downstream step needs first. A regex (--layers) overrides that."""
    if pattern:
        rx = re.compile(pattern, re.I)
        chosen = [r for r in rasters if rx.search(r["path"])]
    else:
        chosen = [r for r in rasters if r["cat"] in anatomy.DEFAULT_CATS]
    order = {c: i for i, c in enumerate(anatomy.CAT_ORDER)}
    chosen.sort(key=lambda r: (order.get(r["cat"], 99), r["path"]))
    return chosen[:max_layers], max(0, len(chosen) - max_layers)


# ----------------------------------------------------------------------------- npz clips

def load_npz(path: str, max_px: int, trace: Trace):
    """Layers + meta from a clip written by nisar_region.py / nisar_fast.py."""
    layers, meta, notes = [], {"file": os.path.basename(path)}, []
    with trace.step("read", f"npz {os.path.basename(path)}"):
        z = np.load(path, allow_pickle=False)
        keys = list(z.files)
        for k in ("bbox", "source"):
            if k in z.files:
                try:
                    v = z[k]
                    meta[k] = v.tolist() if v.ndim else v.item()
                except Exception:
                    pass
        grids = sorted(k[:-len("__xCoordinates")] for k in keys if k.endswith("__xCoordinates"))
        meta["keys"] = len(keys)
        meta["grids"] = grids
        for base in grids:
            xs, ys = np.asarray(z[base + "__xCoordinates"], float), np.asarray(z[base + "__yCoordinates"], float)
            epsg = int(z[base + "__epsg"]) if (base + "__epsg") in z.files else None
            tr = geo.make_transformer(epsg) if epsg else None
            corners = None
            if tr is not None and xs.size > 1 and ys.size > 1:
                dx, dy = xs[1] - xs[0], ys[1] - ys[0]
                L, R, T, B = xs[0] - dx / 2, xs[-1] + dx / 2, ys[0] - dy / 2, ys[-1] + dy / 2
                lon, lat = tr.inv([L, R, L], [T, T, B])
                corners = [[float(lon[i]), float(lat[i])] for i in range(3)]
            for k in keys:
                if not k.startswith(base + "__") or k.endswith(("__xCoordinates", "__yCoordinates", "__epsg")):
                    continue
                a = z[k]
                if a.ndim not in (2, 3) or a.shape[-2:] != (ys.size, xs.size):
                    continue
                nz = a.shape[0] if a.ndim == 3 else None
                if nz:
                    a = a[nz // 2]
                st = max(1, math.ceil(math.sqrt(a.size / max(max_px, 1))))
                gpath = "/" + k.replace("__", "/")
                layers += make_layers(a[::st, ::st], path=gpath, category=anatomy.categorize(gpath), full_shape=z[k].shape,
                                      dtype=z[k].dtype, units=None, fill=None, step=st, window=(0, xs.size, 0, ys.size),
                                      corners=corners, grid="/" + base.replace("__", "/"), secs=0.0, nbytes=None, nz=nz,
                                      source=os.path.basename(path))
        if not grids:
            notes.append("no *__xCoordinates keys: this does not look like a clip from nisar_region.py / nisar_fast.py")
    return layers, meta, notes
