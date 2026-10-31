"""UI-build-3 Phase 3 — explicit retrieval stages with a persistent store between them.

    Resolve (P01 search) -> Plan (exact compressed bytes, no data download) -> Retrieve (bounded, cancellable,
    resumable) -> Inspect (stored clips) -> Preprocess (P03..P13 from the store, zero network).

A retrieval has an id (`ret-…`) and a MANIFEST (`<data>/retrievals/<id>/manifest.json`): tile, bbox, window,
products, layer regex, caps, the granule list with clip key / bytes / state / timings, the plan and flags. The
manifest is the contract between stages. Clips themselves live in the retriever's ClipStore (zarr, written per
granule the moment it finishes), so a cancelled or crashed retrieval keeps everything already stored and a re-run
of the same retrieval skips stored granules (clip-cache hit, no network).

Runs on the RETRIEVAL worker (`JobRunner._pool`, single worker): one retrieval/scan at a time, never the metadata lane.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import threading
import time
import uuid
from pathlib import Path

import numpy as np

_log = logging.getLogger("nisar.retrieval.job")
_ID_OK = __import__("re").compile(r"ret-[0-9a-f]{12}")


class RetrievalError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _now() -> float:
    return time.time()


# --------------------------------------------------------------------------------------- stored retriever
class StoredRetriever:
    """The retriever interface P01/P02 use, backed ONLY by a retrieval's manifest + clip store.

    `search` answers with the granules the retrieval stored, `fetch_tile` loads their clips from the ClipStore.
    There is no HTTP client in here: nothing can touch the network (tests assert this with fetch_tile that raises
    on the live client). A granule missing from the store is reported in `errors`, never fetched."""

    stored = True

    def __init__(self, manifest: dict, clips_dir: Path):
        from engine.retrieval.clients.cache import ClipStore
        self.manifest = manifest
        self.clips = ClipStore(Path(clips_dir), None)
        self.cfg = type("_Cfg", (), {"tiling": {"margin_m": manifest.get("margin_m", 300.0)}})()
        self.retrieval_id = manifest["id"]

    def _refs(self, product: str | None):
        from engine.retrieval.clients.types import GranuleRef
        want = {p.strip() for p in (product or "").split(",") if p.strip()}
        out = []
        for g in self.manifest["granules"]:
            if want and g["product_name"] not in want and g["product"] not in want:
                continue
            out.append(GranuleRef(g["granule_id"], g.get("url") or "", g["product"], g.get("start"), g.get("stop"),
                                  g.get("track"), g.get("frame"), g.get("direction"), g.get("size_bytes"),
                                  g.get("footprint"), g.get("extra")))
        return out

    def search(self, bbox, t0=None, t1=None, product=None, maturity=None, fresh=False, max_results=None):
        return self._refs(product), False

    def fetch_tile(self, tile, t0=None, t1=None, product=None, layers=None, maturity=None, granules=None,
                   margin_m=None):
        from engine.retrieval.clients.nisar_client import NisarClient
        from engine.retrieval.clients.types import FetchResult
        from engine.tiling.mgrs import tile_from_id
        t = tile_from_id(tile) if isinstance(tile, str) else tile
        m = self.manifest.get("margin_m", 300.0) if margin_m is None else margin_m
        bbox = tuple(t.bbox(m))
        res = FetchResult([], {}, {})
        by_id = {g["granule_id"]: g for g in self.manifest["granules"]}
        for ref in (granules if granules is not None else self._refs(product)):
            g = by_id.get(ref.granule_id)
            key = (g or {}).get("clip_key") or self.clips.key(ref.granule_id, bbox, [layers] if layers else None)
            hit = self.clips.get(key)
            if hit is None:
                res.errors[ref.granule_id] = (f"not in retrieval {self.retrieval_id}'s store (state: "
                                              f"{(g or {}).get('state', 'unknown')}) — preprocessing never downloads; "
                                              "re-run the retrieval to fetch it")
                continue
            res.clips.append(NisarClient._from_zarr(ref, bbox, hit[0], "retrieval-store"))
        res.stats = {"network_requests": 0, "retrieval_id": self.retrieval_id}
        return res

    def progress_snapshot(self) -> dict:
        return {}

    def close(self) -> None:
        pass


# --------------------------------------------------------------------------------------- manager
class RetrievalManager:
    def __init__(self, runner):
        self.runner = runner
        self.root = Path(runner.data_dir) / "retrievals"
        self._jobs: dict[str, dict] = {}
        self._clients: dict[str, object] = {}
        self._lock = threading.Lock()

    # ----------------------------------------------------------- storage
    def _dir(self, rid: str) -> Path:
        if not _ID_OK.fullmatch(rid or ""):
            raise RetrievalError(400, f"invalid retrieval id {rid!r}")
        return self.root / rid

    def _save(self, job: dict) -> None:
        d = self._dir(job["id"])
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / "manifest.json.tmp"
        tmp.write_text(json.dumps(job, default=str))
        tmp.replace(d / "manifest.json")

    def get(self, rid: str) -> dict | None:
        with self._lock:
            j = self._jobs.get(rid)
            if j is not None:
                return json.loads(json.dumps(j, default=str))
        f = self._dir(rid) / "manifest.json"
        if f.exists():
            try:
                j = json.loads(f.read_text())
            except (OSError, ValueError):
                return None
            if j.get("status") in ("queued", "running", "cancelling"):   # engine restarted mid-retrieval
                j["status"] = "interrupted"
                j["error"] = "the engine restarted while this retrieval was running; start it again to resume"
            return j
        return None

    def list(self) -> list[dict]:
        out = []
        if self.root.is_dir():
            for d in sorted(self.root.iterdir(), reverse=True):
                j = self.get(d.name) if _ID_OK.fullmatch(d.name) else None
                if j:
                    out.append({k: j.get(k) for k in ("id", "status", "mode", "tile", "created_at", "stage", "totals")})
        return out[:30]

    def busy(self) -> dict | None:
        with self._lock:
            for rid, j in self._jobs.items():
                if j["status"] in ("queued", "running", "cancelling"):
                    return {"kind": "retrieval", "run_id": rid, "step": j.get("stage"), "started_at": j.get("created_at")}
        return None

    # ----------------------------------------------------------- start / cancel
    def start(self, *, lon: float, lat: float, mode: str = "retrieve", as_of: str | None = None,
              lookback_days: float | None = None, max_gcov_epochs: int | None = None,
              max_gunw_pairs: int | None = None, products: list[str] | None = None,
              pols: list[str] | None = None, retriever_factory=None, resume_of: str | None = None) -> str:
        from .jobs import ScanError, earthdata_present, NO_EARTHDATA_MESSAGE, scans_enabled
        if mode not in ("plan", "retrieve"):
            raise RetrievalError(400, "mode must be 'plan' or 'retrieve'")
        if not scans_enabled():
            raise ScanError(403, "retrievals are disabled on this engine (set NISAR_ENABLE_SCANS=1)")
        if retriever_factory is None and not earthdata_present():
            raise ScanError(503, NO_EARTHDATA_MESSAGE)
        busy = self.runner.busy()
        if busy:
            raise RetrievalError(409, f"the engine is busy with a {busy['kind']} ({busy['run_id']}, "
                                      f"step: {busy.get('step') or 'starting'}) — wait for it or cancel it")
        rid = f"ret-{uuid.uuid4().hex[:12]}"
        job = {"id": rid, "mode": mode, "status": "queued", "stage": "queued", "created_at": _now(),
               "request": {"lon": lon, "lat": lat, "as_of": as_of, "lookback_days": lookback_days,
                           "max_gcov_epochs": max_gcov_epochs, "max_gunw_pairs": max_gunw_pairs,
                           "products": products, "pols": pols},
               "tile": None, "bbox": None, "margin_m": None, "window": None, "layers": {}, "caps": {},
               "granules": [], "plan": [], "totals": {}, "flags": [], "error": None, "cancel_requested": False,
               "storage_note": "stored on the engine's disk (on Colab: the runtime disk — lost when the session ends "
                               "unless downloaded)"}
        with self._lock:
            self._jobs[rid] = job
        self._save(job)
        self.runner._pool.submit(self._run, rid, retriever_factory)
        return rid

    def cancel(self, rid: str) -> dict:
        with self._lock:
            j = self._jobs.get(rid)
            if j is None or j["status"] not in ("queued", "running"):
                raise RetrievalError(409, "this retrieval is not running")
            j["cancel_requested"] = True
            j["status"] = "cancelling" if j["status"] == "running" else "cancelled"
            cl = self._clients.get(rid)
        if cl is not None:
            cl.cancel_event.set()          # in-flight ranges finish; nothing new is requested; stored clips stay
        return {"ok": True, "id": rid, "status": j["status"]}

    # ----------------------------------------------------------- the job
    def _flag(self, job: dict, code: str, severity: str, message: str, **detail) -> None:
        job["flags"].append({"code": code, "severity": severity, "message": message, **detail})

    def _run(self, rid: str, retriever_factory) -> None:
        job = self._jobs[rid]
        if job["status"] == "cancelled":
            return
        t0 = time.monotonic()
        retriever = None
        stop = threading.Event()
        try:
            with self._lock:
                job["status"], job["stage"] = "running", "resolve"
            self._save(job)
            from engine.api.jobs import _default_retriever_factory
            from engine.common import p01_granule_resolution as p01
            from engine.common.config import load_common_config
            from engine.common.context import BuildContext, BuildRequest, iso
            from engine.common.grids import make_tile_grids
            from engine.common.layers import request_layers
            from engine.common.context import parse_time
            from engine.retrieval.settings import tile_cell_size_m
            from engine.tiling.mgrs import tile_at
            from datetime import datetime, timedelta, timezone

            rq = job["request"]
            cfg = load_common_config()
            win = cfg.window
            over = {k: v for k, v in (("max_gcov_epochs", rq["max_gcov_epochs"]),
                                      ("max_gunw_pairs", rq["max_gunw_pairs"])) if v}
            if over:
                cfg = dataclasses.replace(cfg, window=dataclasses.replace(win, **over))
            if rq.get("pols"):
                cfg = dataclasses.replace(cfg, products=dataclasses.replace(cfg.products, pols=tuple(rq["pols"])))
            tile = tile_at(rq["lon"], rq["lat"], size=tile_cell_size_m())
            as_of = parse_time(rq["as_of"]) if rq.get("as_of") else datetime.now(timezone.utc).replace(microsecond=0)
            lb = cfg.window.lookback_days if rq.get("lookback_days") is None else float(rq["lookback_days"])
            req = BuildRequest(tile, as_of, as_of - timedelta(days=lb), as_of)
            retriever = (retriever_factory or _default_retriever_factory())()
            with self._lock:
                self._clients[rid] = retriever
            if hasattr(retriever, "cancel_event"):
                retriever.cancel_event.clear()
            margin = cfg.clip.margin_m
            if margin is None:
                margin = getattr(getattr(retriever, "cfg", None), "tiling", {}).get("margin_m", 300.0)
            bbox = tuple(tile.bbox(margin))
            job.update(tile=tile.id, bbox=list(bbox), margin_m=margin,
                       window={"as_of": iso(as_of), "t0": iso(req.t0), "t1": iso(req.t1), "lookback_days": lb},
                       caps={"max_gcov_epochs": cfg.window.max_gcov_epochs, "max_gunw_pairs": cfg.window.max_gunw_pairs,
                             "pols": list(cfg.products.pols)})

            # ---- 1. resolve (same P01 logic, shared) ----
            ctx = BuildContext(req, cfg, make_tile_grids(tile, cfg.grids), retriever)
            p01.run(ctx)
            for f in ctx.flags:
                self._flag(job, f.code, f.severity, f.message)
            res = ctx.resolved
            names = {"GUNW": cfg.products.gunw, "GCOV": cfg.products.gcov, "GOFF": cfg.products.goff}
            want = [p.upper() for p in (rq.get("products") or [])]
            fetch: dict = {}
            if res is not None:
                for prod, refs in res.to_fetch.items():
                    if want and prod not in want:
                        continue
                    layers = request_layers(names[prod] if prod != "GOFF" else "GOFF", None,
                                            cfg.products.frequency, tuple(cfg.products.pols),
                                            cfg.products.use_noise_floor)
                    if layers is None:
                        continue
                    fetch[prod] = (names[prod], layers, list(refs))
                    job["layers"][prod] = layers
            retr_cls_key = getattr(retriever, "clips", None)
            for prod, (pname, layers, refs) in fetch.items():
                for r in refs:
                    key = retr_cls_key.key(r.granule_id, bbox, [layers]) if retr_cls_key is not None else None
                    job["granules"].append({
                        "granule_id": r.granule_id, "product": prod, "product_name": pname, "url": r.url,
                        "track": r.track, "frame": r.frame, "direction": r.direction, "start": r.start,
                        "stop": r.stop, "size_bytes": r.size_bytes, "footprint": r.footprint, "extra": r.extra,
                        "clip_key": key, "state": "planned", "bytes_planned": None, "bytes_done": 0,
                        "chunks_planned": None, "chunks_done": 0, "seconds": None, "error": None})
            job["totals"] = {"granules": len(job["granules"]),
                             **{f"{p.lower()}_granules": sum(1 for g in job["granules"] if g["product"] == p) for p in fetch}}
            self._check_cancel(job)

            # ---- 2. plan (exact bytes from the layout index; no data chunk is downloaded) ----
            with self._lock:
                job["stage"] = "plan"
            self._save(job)
            planner = getattr(retriever, "plan_tile", None)
            if planner is not None:
                for prod, (pname, layers, refs) in fetch.items():
                    rows = planner(refs, bbox, layers)
                    job["plan"].extend(rows)
                self._apply_plan(job)
            else:
                self._flag(job, "plan_unavailable", "warn", "this retriever cannot plan byte sizes")
            self._check_cancel(job)
            if job["mode"] == "plan":
                self._finish(job, "planned", t0)
                return

            # ---- 3. retrieve (bounded granule concurrency, per-granule state, cancel, resume) ----
            with self._lock:
                job["stage"] = "download"
            self._save(job)
            hb = threading.Thread(target=self._monitor, args=(job, retriever, stop), daemon=True,
                                  name=f"retrieval-monitor-{rid}")
            hb.start()
            for prod, (pname, layers, refs) in fetch.items():
                fr = retriever.fetch_tile(tile, iso(req.t0), iso(req.t1), product=pname, layers=layers,
                                          granules=refs, margin_m=cfg.clip.margin_m)
                self._absorb(job, retriever, fr)
                if fr.degraded:
                    self._flag(job, "fetch_degraded", "warn", f"{prod}: served from stale cache")
            stop.set()
            self._sync_states(job, retriever)
            stored = sum(1 for g in job["granules"] if g["state"] == "stored")
            if job["cancel_requested"]:
                self._finish(job, "cancelled", t0)
            else:
                self._finish(job, "done" if stored or not job["granules"] else "failed", t0)
        except _Cancelled:
            self._finish(job, "cancelled", t0)
        except Exception as ex:  # noqa: BLE001 — a failed retrieval is a status + the reason
            _log.exception("retrieval %s failed", rid)
            job["error"] = f"{type(ex).__name__}: {ex}"
            self._finish(job, "error", t0)
        finally:
            stop.set()
            with self._lock:
                self._clients.pop(rid, None)
            if retriever is not None:
                try:
                    retriever.close()
                except Exception:
                    pass

    def _check_cancel(self, job: dict) -> None:
        if job["cancel_requested"]:
            raise _Cancelled()

    def _finish(self, job: dict, status: str, t0: float) -> None:
        with self._lock:
            job["status"], job["stage"] = status, "finished"
            job["seconds"] = round(time.monotonic() - t0, 1)
            self._tally(job)
        self._save(job)

    @staticmethod
    def _tally(job: dict) -> None:
        gs = job["granules"]
        t = job.setdefault("totals", {})
        t.update(granules=len(gs),
                 bytes_planned=sum(g["bytes_planned"] or 0 for g in gs),
                 bytes_done=sum(g["bytes_done"] or 0 for g in gs),
                 stored=sum(1 for g in gs if g["state"] == "stored"),
                 failed=sum(1 for g in gs if g["state"] == "failed"),
                 skipped=sum(1 for g in gs if g["state"] in ("skipped", "cancelled")))

    @staticmethod
    def _apply_plan(job: dict) -> None:
        by: dict = {}
        for r in job["plan"]:
            if r.get("bytes") is not None:
                d = by.setdefault(r["granule_id"], [0, 0])
                d[0] += r["bytes"]
                d[1] += r["chunks"]
        for g in job["granules"]:
            if g["granule_id"] in by:
                g["bytes_planned"], g["chunks_planned"] = by[g["granule_id"]]
        RetrievalManager._tally(job)

    def _sync_states(self, job: dict, retriever) -> None:
        snap = getattr(retriever, "progress_snapshot", lambda: {})() or {}
        states = ((snap.get("granules") or {}).get("states")) or {}
        with self._lock:
            for g in job["granules"]:
                st = states.get(g["granule_id"])
                if not st:
                    continue
                if g["state"] in ("stored", "failed", "skipped", "cancelled") and st["state"] == "queued":
                    continue
                g["state"] = st["state"]
                for k in ("bytes_done", "chunks_done", "seconds", "error"):
                    if st.get(k) is not None:
                        g[k] = st[k]
                if st.get("bytes_planned"):
                    g["bytes_planned"] = st["bytes_planned"]
                if st.get("chunks_planned"):
                    g["chunks_planned"] = st["chunks_planned"]
                if st.get("source"):
                    g["source"] = st["source"]
            net = snap.get("net") or {}
            job["net"] = net
            job["cache"] = snap.get("cache") or {}
            self._tally(job)

    def _monitor(self, job: dict, retriever, stop: threading.Event) -> None:
        n = 0
        while not stop.wait(1.0):
            n += 1
            try:
                self._sync_states(job, retriever)
                if n % 5 == 0:
                    self._save(job)
            except Exception:
                pass

    def _absorb(self, job: dict, retriever, fr) -> None:
        with self._lock:
            by = {g["granule_id"]: g for g in job["granules"]}
            for clip in fr.clips:
                g = by.get(clip.granule.granule_id)
                if g is not None:
                    g["state"], g["source"] = "stored", clip.source
            for gid, why in fr.errors.items():
                if gid in by:
                    by[gid]["state"], by[gid]["error"] = "failed", why
            for gid, why in fr.skipped.items():
                if gid in by:
                    by[gid]["state"] = "cancelled" if "cancel" in why else "skipped"
                    by[gid]["note"] = why
        self._save(job)


class _Cancelled(Exception):
    pass


# --------------------------------------------------------------------------------------- stored clips (Inspect)
class ClipReader:
    """Reads a retrieval's stored clips straight from the ClipStore — no network, no re-download."""

    def __init__(self, manifest: dict, clips_dir: Path):
        from engine.retrieval.clients.cache import ClipStore
        self.manifest = manifest
        self.store = ClipStore(Path(clips_dir), None)

    def _root(self, gid: str):
        g = next((x for x in self.manifest["granules"] if x["granule_id"] == gid), None)
        if g is None or not g.get("clip_key"):
            raise RetrievalError(404, f"granule {gid!r} is not part of this retrieval")
        hit = self.store.get(g["clip_key"], allow_stale=True)
        if hit is None:
            raise RetrievalError(404, f"granule {gid!r} is not stored (state: {g.get('state')})")
        return g, hit[0], hit[1]

    def listing(self) -> list[dict]:
        out = []
        for g in self.manifest["granules"]:
            if g.get("state") != "stored":
                continue
            try:
                _, root, meta = self._root(g["granule_id"])
            except RetrievalError:
                continue
            for _, grp in root.groups():
                key = grp.attrs["grid_key"]
                for k in grp.array_keys():
                    lk = grp[k].attrs.get("layer_key")
                    if lk is None:
                        continue
                    a = grp[k]
                    out.append({"granule_id": g["granule_id"], "product": g["product"], "grid": key, "layer": lk,
                                "shape": list(a.shape), "dtype": str(a.dtype), "epsg": int(grp.attrs["epsg"]),
                                "bytes": int(np.prod(a.shape)) * int(a.dtype.itemsize), "start": g.get("start"),
                                "track": g.get("track"), "direction": g.get("direction"),
                                "fill_value": _plain(getattr(a, "fill_value", None))})
        return out

    def array(self, gid: str, grid: str, layer: str):
        _, root, _ = self._root(gid)
        for _, grp in root.groups():
            if grp.attrs["grid_key"] != grid:
                continue
            for k in grp.array_keys():
                if grp[k].attrs.get("layer_key") == layer:
                    return np.asarray(grp[k][:]), grp
        raise RetrievalError(404, f"no layer {layer!r} on grid {grid!r} in granule {gid!r}")


def clip_corners(grp) -> tuple:
    """-> (corners_lonlat | None, note | None) for a stored clip group: [TL, TR, BR, BL] of the pixel
    EDGES, from the group's own x / y axes. No axes (or an unprojectable / antimeridian clip) -> (None,
    reason): never a guess."""
    try:
        keys = set(grp.array_keys())
        if "x" not in keys or "y" not in keys:
            return None, "no corners: this stored clip has no x / y axes"
        from engine.common.grids import edge_corners_lonlat
        return edge_corners_lonlat(np.asarray(grp["x"][:], np.float64), np.asarray(grp["y"][:], np.float64),
                                   int(grp.attrs["epsg"]))
    except Exception as err:
        return None, f"no corners: {type(err).__name__}: {err}"


def _plain(v):
    try:
        return v.item() if hasattr(v, "item") else v
    except Exception:
        return None


def displayable(a: np.ndarray) -> np.ndarray:
    """2-D float view of the LAST two axes (first slice of any leading axes); complex -> magnitude. No resampling."""
    if a.dtype.fields is not None:
        raise RetrievalError(422, "structured dtype — export it instead of drawing it")
    while a.ndim > 2:
        a = a[0]
    if np.iscomplexobj(a):
        a = np.abs(a)
    return a.astype(np.float32, copy=False)


def array_stats(a: np.ndarray, bins: int = 32) -> dict:
    v = displayable(a)
    fin = v[np.isfinite(v)]
    if fin.size == 0:
        return {"valid": 0, "total": int(v.size)}
    hist, edges = np.histogram(fin, bins=bins)
    return {"valid": int(fin.size), "total": int(v.size), "min": float(fin.min()), "max": float(fin.max()),
            "mean": float(fin.mean()), "std": float(fin.std()),
            "hist": {"counts": hist.tolist(), "edges": [float(e) for e in edges]},
            "note": "measured on the stored array (first slice of any leading axis; complex shown as magnitude)"}
