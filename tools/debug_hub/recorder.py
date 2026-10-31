"""Observe the retriever without changing it.

`Recorder` implements the same two methods the chain uses (search, fetch_tile) by forwarding to the real
retriever and recording what went in, what came out, how long it took, and how many bytes moved meanwhile.
Everything else (cfg, ...) is delegated untouched. `MetricsPoller` samples the real client's byte/request
counters while a call is in flight, which is what makes loading visible in real time.
"""
from __future__ import annotations

import threading
import time

from .util import short_exc


def _delta(a: dict, b: dict) -> dict:
    return {k: (b.get(k, 0) - a.get(k, 0)) for k in b if isinstance(b.get(k), (int, float)) and k in a}


class MetricsPoller:
    def __init__(self, job, fn, period: float = 0.4):
        self.job, self.fn, self.period = job, fn, period
        self._stop = threading.Event()
        self._th = threading.Thread(target=self._run, name="hub-metrics", daemon=True)
        self._last = None
        self.series: list = []

    def start(self):
        if self.fn is not None:
            self._th.start()
        return self

    def stop(self):
        self._stop.set()
        if self.fn is not None and self._th.is_alive():
            self._th.join(1.0)
        self._sample(force=True)

    def _sample(self, force: bool = False):
        if self.fn is None:
            return
        try:
            m = self.fn()
        except Exception as ex:                                   # a failing probe is itself information
            self.job.emit("net_error", f"metrics probe failed: {short_exc(ex)}")
            return
        if force or m != self._last:
            self._last = m
            self.job.net_last = m
            self.series.append((round(time.time() - self.job.t0, 2), m.get("bytes_fetched", 0), m.get("requests", 0)))
            self.job.emit("net", "network counters", **m)

    def _run(self):
        while not self._stop.wait(self.period):
            self._sample()


class Recorder:
    def __init__(self, inner, job, metrics_fn=None):
        self._inner, self._job, self._metrics = inner, job, metrics_fn

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def _snap(self) -> dict:
        try:
            return dict(self._metrics()) if self._metrics else {}
        except Exception:
            return {}

    def _record(self, method: str, args: dict, fn):
        job = self._job
        n = len(job.calls) + 1
        rec = {"n": n, "method": method, "args": args, "t_start": round(time.time() - job.t0, 3), "ok": None}
        job.calls.append(rec)
        job.emit("call", f"{method} {args.get('product') or ''} started", stage="P01" if method == "search" else "P02", n=n,
                 method=method, args=args)
        before, t = self._snap(), time.perf_counter()
        try:
            out = fn()
            rec["ok"] = True
            return out
        except Exception as ex:
            rec["ok"], rec["error"] = False, short_exc(ex)
            raise
        finally:
            rec["seconds"] = round(time.perf_counter() - t, 3)
            rec["net_delta"] = _delta(before, self._snap()) if before else {}
            job.emit("call_end", f"{method} {args.get('product') or ''} {'ok' if rec['ok'] else 'FAILED'} in {rec['seconds']}s",
                     stage="P01" if method == "search" else "P02", n=n, ok=rec["ok"], seconds=rec["seconds"],
                     error=rec.get("error"), net_delta=rec["net_delta"])

    # -- the two methods engine.common uses ---------------------------------------------------
    def search(self, bbox, t0=None, t1=None, product=None, *a, **kw):
        args = {"bbox": [round(float(v), 6) for v in bbox] if bbox else None, "t0": t0, "t1": t1, "product": product}

        def go():
            out = self._inner.search(bbox, t0, t1, product, *a, **kw)
            refs, degraded = out
            self._job.search_refs.setdefault(product or "?", []).extend(refs)
            rec = self._job.calls[-1]
            rec["result"] = {"n_granules": len(refs), "degraded": bool(degraded)}
            return out
        return self._record("search", args, go)

    def fetch_tile(self, tile, t0=None, t1=None, product=None, layers=None, *a, granules=None, **kw):
        args = {"tile": getattr(tile, "id", str(tile)), "t0": t0, "t1": t1, "product": product, "layers": layers,
                "granules": [g.granule_id for g in (granules or [])], "margin_m": kw.get("margin_m")}

        def go():
            fr = self._inner.fetch_tile(tile, t0, t1, product, layers, *a, granules=granules, **kw)
            self._job.fetch_results[product or "?"] = fr
            rec = self._job.calls[-1]
            rec["result"] = {"n_clips": len(fr.clips), "n_errors": len(fr.errors), "n_skipped": len(fr.skipped),
                             "degraded": bool(fr.degraded), "client_stats": {k: v for k, v in (fr.stats or {}).items()
                                                                           if isinstance(v, (int, float))}}
            return fr
        return self._record("fetch_tile", args, go)
