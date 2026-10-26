"""Run one tile through the REAL engine path and publish what each stage produced.

    live      engine.retrieval NisarClient  -> P01..P13  -> SceneBundle -> Registry.plan
    selftest  tests/common_fixtures.FakeRetriever (synthetic arrays in real NISAR layer-key layout) -> same chain.
              Exists to test the hub itself; every screen is stamped SELF-TEST when it is used.

The hub never alters a result: the retriever is wrapped by `Recorder`, the chain is observed through its own
`observer` hook, and failures are reported with the engine's own exception text.
"""
from __future__ import annotations

import sys
import tempfile
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from . import handoff, imaging
from .events import Job
from .recorder import MetricsPoller, Recorder
from .store import ArrayStore, Entry
from .util import plain, short_exc

REPO = Path(__file__).resolve().parents[2]
RUN_LOCK = threading.Lock()                      # one job at a time: the real client owns threads and an event loop


# ------------------------------------------------------------------------------------------------ raw stats
def raw_stats(a: np.ndarray) -> dict:
    out = {"shape": list(a.shape), "dtype": str(a.dtype), "nbytes": int(a.nbytes)}
    if a.size == 0:
        return out
    k = a.dtype.kind
    v = np.abs(a) if k == "c" else a
    if k in "fc":
        fin = np.isfinite(v)
        out["finite_frac"] = float(fin.mean())
        if fin.any():
            f = v[fin]
            p2, p98 = np.percentile(f, [2, 98])
            out.update(min=float(f.min()), max=float(f.max()), mean=float(f.mean()), p2=float(p2), p98=float(p98),
                       zero_frac=float((f == 0).mean()))
    elif k in "iub":
        u, c = np.unique(v, return_counts=True)
        out.update(min=float(u.min()), max=float(u.max()), zero_frac=float((v == 0).mean()), n_unique=int(u.size))
        top = np.argsort(-c)[:6]
        out["top_values"] = [[float(u[i]), int(c[i])] for i in top]
    return out


def _axes_extent(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    if x.size < 2 or y.size < 2:
        return None
    dx, dy = abs(float(np.median(np.diff(x)))), abs(float(np.median(np.diff(y))))
    return (float(x.min() - dx / 2), float(x.max() + dx / 2), float(y.min() - dy / 2), float(y.max() + dy / 2)), dx, dy


def _lonlat_box(ext, epsg):
    try:
        from engine.common._geo import transform_xy
        x0, x1, y0, y1 = ext
        lon, lat = transform_xy(np.array([x0, x1, x1, x0]), np.array([y0, y0, y1, y1]), f"EPSG:{epsg}", "EPSG:4326")
        return [float(lon.min()), float(lat.min()), float(lon.max()), float(lat.max())]
    except Exception:
        return None


# ------------------------------------------------------------------------------------------------ retrieval payload
def retrieval_payload(job: Job) -> dict:
    """What the retriever returned, before the engine interpreted any of it."""
    from engine.common.layers import parse_layer_key
    store: ArrayStore = job.store
    sel: dict = {}
    for c in job.calls:
        if c["method"] == "fetch_tile":
            for gid in c["args"].get("granules", []):
                sel[gid] = c["args"].get("product")
    fetched = {}
    for prod, fr in job.fetch_results.items():
        for clip in fr.clips:
            fetched[clip.granule.granule_id] = ("clip", clip)
        for gid, why in fr.errors.items():
            fetched[gid] = ("error", why)
        for gid, why in fr.skipped.items():
            fetched[gid] = ("skipped", why)
    granules, seen = [], set()
    for prod, refs in job.search_refs.items():
        for g in refs:
            if g.granule_id in seen:
                continue
            seen.add(g.granule_id)
            st = fetched.get(g.granule_id)
            host = g.url.split("/")[2] if g.url and "://" in g.url else None
            granules.append({"id": g.granule_id, "product": g.product or prod, "start": g.start, "stop": g.stop,
                             "track": g.track, "frame": g.frame, "direction": g.direction, "size_bytes": g.size_bytes,
                             "url_host": host, "footprint": g.footprint, "selected": g.granule_id in sel,
                             "status": st[0] if st else ("not fetched" if g.granule_id not in sel else "no result"),
                             "detail": st[1] if st and st[0] != "clip" else None,
                             "source": st[1].source if st and st[0] == "clip" else None})
    clips = []
    n = 0
    for prod, fr in job.fetch_results.items():
        for clip in fr.clips:
            grids = []
            for gk, g in clip.grids.items():
                ax = _axes_extent(g.x, g.y)
                ext, dx, dy = ax if ax else (None, None, None)
                layers = []
                for lk, arr in g.layers.items():
                    n += 1
                    lid = parse_layer_key(lk)
                    a2 = imaging.finite_view(arr)
                    shp_ok = ext is not None and a2.shape == (len(g.y), len(g.x))
                    note = f"slice {arr.shape[0] // 2} of {arr.shape[0]} shown" if arr.ndim == 3 else ""
                    aid = store.put(Entry(f"c{n}", a2, lk, ext if shp_ok else None, g.epsg if shp_ok else None, 1,
                                          "int" if arr.dtype.kind in "iub" else "float", note))
                    layers.append({"aid": aid, "key": lk, "name": lid.name, "role": lid.role, "pol": lid.pol,
                                   "section": lid.section, "georef": bool(shp_ok), "note": note,
                                   "stats": raw_stats(arr)})
                grids.append({"key": gk, "epsg": int(g.epsg), "nx": int(len(g.x)), "ny": int(len(g.y)),
                              "dx": dx, "dy": dy, "extent": list(ext) if ext else None,
                              "lonlat": _lonlat_box(ext, g.epsg) if ext else None,
                              "extras": [{"name": k, "shape": list(np.shape(v)), "dtype": str(np.asarray(v).dtype)}
                                         for k, v in g.extras.items()], "layers": layers})
            clips.append({"granule_id": clip.granule.granule_id, "product": clip.granule.product, "source": clip.source,
                          "bbox": [float(v) for v in clip.bbox], "stats": plain(clip.stats), "grids": grids})
    return {"calls": plain(job.calls), "granules": granules, "clips": clips,
            "errors": {gid: w for gid, (s, w) in fetched.items() if s == "error"},
            "skipped": {gid: w for gid, (s, w) in fetched.items() if s == "skipped"},
            "net": job.net_last, "n_layers": n}


def p01_payload(ctx) -> dict:
    r = ctx.resolved
    if r is None:
        return {}
    stacks = {}
    for key in sorted(set(r.gunw) | set(r.gcov) | set(r.goff)):
        pg, eg = r.gunw.get(key, []), r.gcov.get(key, [])
        stacks[key] = {
            "gunw_pairs": [{"ref": p.ref_time.isoformat() if p.ref_time else None, "sec": p.sec_time.isoformat() if p.sec_time else None,
                            "dt_days": p.dt_days, "flipped": p.flipped, "resolved": p.resolved,
                            "granules": [g.ref.granule_id for g in p.granules]} for p in pg],
            "gcov_epochs": [{"time": e.time.isoformat(), "granules": [g.ref.granule_id for g in e.granules]} for e in eg]}
    return {"stacks": stacks, "to_fetch": {k: [g.granule_id for g in v] for k, v in r.to_fetch.items()},
            "dropped_older": r.dropped_older, "search_degraded": r.search_degraded, "search_errors": plain(r.search_errors)}


# ------------------------------------------------------------------------------------------------ processing payload
def _arr_entry(job: Job, key: str, rec, aids: dict) -> dict:
    store: ArrayStore = job.store
    aid = aids.setdefault(key, f"t{len(aids) + 1}")
    store.put(Entry(aid, rec.thumb, rec.label, rec.extent, rec.epsg, int(rec.stride), rec.kind, rec.note))
    return {"aid": aid, "path": rec.path, "label": rec.label, "group": rec.group, "step": rec.step, "first_step": rec.first_step,
            "shape": list(rec.shape), "dtype": rec.dtype, "kind": rec.kind, "stats": plain(rec.stats), "stride": int(rec.stride),
            "georef": rec.extent is not None, "note": rec.note, "categories": plain(rec.categories),
            "fill": plain(rec.fill)}


class LiveObserver:
    """Composes the engine's own Tracer with live publishing. Read-only on the BuildContext."""

    def __init__(self, job: Job, tracer, steps_by_code: dict):
        self.job, self.tracer, self.steps_by_code, self.aids = job, tracer, steps_by_code, {}

    def __call__(self, step, ctx, rec):
        job = self.job
        self.tracer(step, ctx, rec)                                     # Tracer swallows its own errors
        snap = self.tracer.trace.steps[-1] if self.tracer.trace.steps and self.tracer.trace.steps[-1].code == step.code else None
        counts: dict = {}
        for r in self.tracer.trace.arrays.values():
            counts[r.group] = counts.get(r.group, 0) + 1
        prev = job.step_counts[job.steps[-1]["code"]] if job.steps else {}
        job.step_counts[step.code] = counts
        reads = []
        for attr in step.needs:
            n = None if attr not in counts and attr not in prev else prev.get(attr, 0)
            try:
                v = getattr(ctx, attr, None)
                size = len(v) if hasattr(v, "__len__") else (None if v is None else 1)
            except Exception:
                size = None
            reads.append({"attr": attr, "arrays_before": n, "size": size})
        new = [_arr_entry(job, p, self.tracer.trace.arrays[p], self.aids) for p in (snap.arrays if snap else []) if p in self.tracer.trace.arrays]
        wrote: dict = {}
        for a in new:
            wrote[a["group"]] = wrote.get(a["group"], 0) + 1
        job.steps.append({"code": step.code, "name": step.name, "status": rec.status, "seconds": rec.seconds,
                          "message": rec.message, "reads": reads, "wrote": wrote, "n_new": len(new), "arrays": new,
                          "flags": snap.new_flags if snap else [], "assumptions": snap.new_assumptions if snap else [],
                          "arrays_total_after": sum(counts.values())})
        job.emit("step", f"{step.code} {step.name}: {rec.status} in {rec.seconds:.2f}s, {len(new)} arrays", stage=step.code,
                 status=rec.status, seconds=rec.seconds, n_new=len(new))
        if step.code == "P01":
            job.p01 = p01_payload(ctx)
        if step.code == "P02":
            job.retrieval = retrieval_payload(job)
            job.emit("retrieval", f"{len(job.retrieval['clips'])} clips, {job.retrieval['n_layers']} layers retrieved", stage="P02")


# ------------------------------------------------------------------------------------------------ sources
def make_source(kind: str, job: Job, tile, params: dict):
    """-> (retriever, close_fn, metrics_fn, ancillary, info)"""
    from engine.common.ancillary import DirectoryAncillary, NullAncillary
    from engine.common.config import data_root
    if kind == "selftest":
        sys.path.insert(0, str(REPO / "tests"))
        import common_fixtures as cf
        retr = cf.FakeRetriever(cf.make_scene(tile))
        anc = NullAncillary()
        tmp = None
        if params.get("synthetic_ancillary"):
            tmp = tempfile.mkdtemp(prefix="hub_anc_")
            cf.write_ancillary(tmp, tile)
            anc = DirectoryAncillary(tmp)
        return retr, (lambda: None), None, anc, {"kind": "selftest", "label": "SELF-TEST SOURCE: synthetic fixture, not NISAR data",
                                                 "ancillary": "synthetic" if tmp else "none"}
    from engine.retrieval.clients.nisar_client import NisarClient         # missing deps surface here, verbatim
    client = NisarClient()
    droot = data_root()
    anc = DirectoryAncillary(droot / "ancillary") if (droot / "ancillary").is_dir() else NullAncillary()

    def metrics():
        m = client.io.metrics.snapshot()
        m.update({k: client.counters[k] for k in ("chunks_net", "chunks_disk", "chunks_ram", "clip_hit", "index_built", "index_disk")})
        m.update(ram_hits=client.ram.hits, disk_hits=client.disk.hits)
        return m

    return client, client.close, metrics, anc, {"kind": "live", "label": "LIVE: engine.retrieval NisarClient",
                                                "auth": "present" if client.auth.available else "NONE",
                                                "ancillary": f"directory {droot / 'ancillary'}" if isinstance(anc, DirectoryAncillary) else "none (no data/ancillary folder)"}


# ------------------------------------------------------------------------------------------------ job
def run_job(job: Job) -> None:
    p = job.params
    job.store, job.p01 = ArrayStore(), {}
    if not RUN_LOCK.acquire(blocking=False):
        job.finish("failed", "another job is running")
        return
    job.state = "running"
    close = lambda: None                                                    # noqa: E731
    poller = None
    try:
        from engine.common.chain import build_scene_bundle, STEPS
        from engine.common.config import load_common_config
        from engine.common.contract import check_bundle
        from engine.common.debug.trace import Tracer
        from engine.tiling.mgrs import tile_at
        lon, lat = float(p["lon"]), float(p["lat"])
        tile = tile_at(lon, lat)
        job.tile = {"id": tile.id, "epsg": int(tile.epsg), "bbox": [float(v) for v in tile.bbox(0.0)], "center": [float(v) for v in tile.center]}
        job.emit("job", f"tile {tile.id} (EPSG:{tile.epsg}) for lon {lon:.5f}, lat {lat:.5f}", stage="setup", tile=job.tile)
        cfg = load_common_config()
        retr, close, metrics_fn, anc, info = make_source(p.get("source", "live"), job, tile, p)
        job.source = info
        job.emit("job", f"source: {info['label']}; ancillary: {info['ancillary']}", stage="setup", source=info)
        if info.get("auth") == "NONE":
            job.emit("warn", "no Earthdata credentials in the environment/.env: search works, opening files will fail", stage="setup")
        as_of = p.get("as_of") or ("2026-09-20T00:00:00Z" if info["kind"] == "selftest" else datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        lookback = float(p["lookback_days"]) if p.get("lookback_days") else None
        job.request = {"as_of": as_of, "lookback_days": lookback if lookback is not None else cfg.window.lookback_days,
                       "margin_m": getattr(getattr(retr, "cfg", None), "tiling", {}).get("margin_m") if hasattr(retr, "cfg") else None}
        rec = Recorder(retr, job, metrics_fn)
        poller = MetricsPoller(job, metrics_fn).start()
        from engine.common.debug.trace import Tracer
        tracer = Tracer()
        obs = LiveObserver(job, tracer, {s.code: s for s in STEPS})
        job.emit("job", f"building bundle as_of {as_of}", stage="P01")
        bundle = build_scene_bundle(tile, rec, as_of=as_of, ancillary=anc, cfg=cfg, lookback_days=lookback, observer=obs)
        tracer.trace.bundle = bundle
        poller.stop()
        if job.retrieval is None:                                           # chain skipped P02: still publish what exists
            job.retrieval = retrieval_payload(job)
        job.retrieval["net"] = job.net_last
        job.retrieval["net_series"] = poller.series
        job.emit("job", f"bundle {bundle.status}: {bundle.bundle_id}", stage="P13", status=bundle.status)
        issues = check_bundle(bundle, deep=True)
        summ = handoff.bundle_summary(bundle)
        summ["contract_issues"] = [str(i) for i in issues]
        summ["tracer_errors"] = list(tracer.trace.errors if hasattr(tracer.trace, "errors") else tracer.trace.tracer_errors)
        fin = {}
        for path, r in tracer.trace.arrays.items():
            if r.group in ("ifg", "cov", "stable", "ancillary", "geometry"):
                fin.setdefault(r.group, []).append(_arr_entry(job, path, r, obs.aids))
        summ["final_arrays"] = fin
        job.final = summ
        job.emit("final", f"contract check: {len(issues)} issue(s)", stage="P13")
        job.handoff = handoff.pipeline_feed(bundle)
        job.emit("handoff", f"pipeline feed: {job.handoff['tally']}", stage="handoff")
        job.finish("done")
        job.emit("done", "finished", stage="done")
    except Exception as ex:
        tb = traceback.format_exc()
        job.emit("error", short_exc(ex), stage="job", traceback=tb[-4000:])
        job.finish("failed", short_exc(ex))
        job.traceback = tb
    finally:
        try:
            if poller is not None:
                poller.stop()
        except Exception:
            pass
        try:
            close()
        except Exception:
            pass
        RUN_LOCK.release()
