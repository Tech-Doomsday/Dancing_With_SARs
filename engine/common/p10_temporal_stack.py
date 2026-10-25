# P10 — temporal stack: per-pixel baseline (median / MAD / robust sigma), pair network, coherence baseline, data depth
"""What the stack looks like over time, and how much history there is to compare against.

Backscatter baseline (per polarization, G20, dB)
    prior epochs = every epoch before the newest one and before the event window (an epoch inside the event window
    must not define "normal"). median, MAD (unscaled), robust sigma = max(1.4826 * MAD, speckle floor) where the
    speckle floor is 4.343 / sqrt(looks) dB - the std of an L-look average in dB - so a quiet baseline of a few
    epochs cannot yield an impossibly small sigma. `cold_start` when there are fewer than `min_baseline_epochs`
    prior epochs; `seasonal_ok` only with at least `seasonal_min_history_days` of history.

Pair network
    dates are nodes, pairs are edges; connected components by union-find. `largest_component_*` is what an SBAS
    network can actually use.

Coherence baseline (G80)
    median / MAD / count of coherence over the pairs before the newest one (and before the event window).

Data depth
    one record per stack and an overall figure. The overall figure describes the BEST SINGLE STACK (a pipeline runs on
    one geometry), it is not a sum over tracks. `registry` gates `min_epochs` on it.
"""
from __future__ import annotations

import warnings
from datetime import datetime

import numpy as np

from .context import BuildContext, iso, parse_time
from .frozen import FrozenDict
from .scene_bundle import Baseline, CoherenceBaseline, DataDepth, PairNetwork

_DB_PER_NEPER = 4.342944819032518     # 10 / ln 10 : std of ln-power -> std in dB


def _cut(ctx: BuildContext, newest: datetime) -> datetime:
    """Samples at or after this instant may not define the baseline."""
    ew = ctx.request.event_window
    if ew and ew[0] is not None:
        start = parse_time(ew[0])
        if start <= newest:
            return start
    return newest


def _nan_stats(a: np.ndarray, mad_scale: float):
    """median, MAD, count over axis 0 of (N, ny, nx); silent on all-NaN columns."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        med = np.nanmedian(a, axis=0)
        mad = np.nanmedian(np.abs(a - med[None]), axis=0)
    n = np.isfinite(a).sum(axis=0).astype(np.int16)
    return med.astype(np.float32), mad.astype(np.float32), n


def backscatter_baseline(ctx: BuildContext, key: str, w) -> Baseline:
    t = ctx.cfg.temporal
    E = len(w.epochs)
    times = [parse_time(e.time) for e in w.epochs]
    cur = w.current_index
    cut = _cut(ctx, times[cur])
    prior = [i for i in range(E) if i < cur and times[i] < cut]
    shape = ctx.grids.g20.shape
    med, mad, sig, cnt = {}, {}, {}, {}
    looks = None
    if w.n_looks is not None and prior:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            looks = np.nanmedian(w.n_looks[prior], axis=0)
    floor_scalar = None
    if looks is not None and np.isfinite(looks).any():
        floor_scalar = float(_DB_PER_NEPER / np.sqrt(max(float(np.nanmedian(looks)), 1.0)))
        floor = _DB_PER_NEPER / np.sqrt(np.clip(np.nan_to_num(looks, nan=t.default_looks), 1.0, None))
    else:
        floor_scalar = float(_DB_PER_NEPER / np.sqrt(t.default_looks))
        floor = np.full(shape, floor_scalar)
    for p, g in w.gamma0_db.items():
        if prior:
            m, d, n = _nan_stats(g[prior], t.mad_scale)
            s = np.maximum(t.mad_scale * d, floor).astype(np.float32)
            s[n == 0] = np.nan
        else:
            m = np.full(shape, np.nan, np.float32)
            d = m.copy()
            s = m.copy()
            n = np.zeros(shape, np.int16)
        med[p], mad[p], sig[p], cnt[p] = m, d, s, n
    history = float((times[cur] - times[prior[0]]).total_seconds() / 86400.0) if prior else 0.0
    cold = len(prior) < t.min_baseline_epochs
    if cold:
        ctx.flag("cold_start", "warn", "P10", f"{key}: {len(prior)} prior epoch(s) < {t.min_baseline_epochs}; "
                 "backscatter baseline is not reliable", stack=key, prior_epochs=len(prior))
    if len(prior) < E - 1:
        ctx.flag("baseline_excludes_event", "info", "P10", f"{key}: {E - 1 - len(prior)} epoch(s) inside/after the event window "
                 "excluded from the baseline", stack=key)
    return Baseline(FrozenDict(med), FrozenDict(mad), FrozenDict(sig), FrozenDict(cnt), len(prior), tuple(prior), history, cold,
                    history >= t.seasonal_min_history_days, floor_scalar)


# ---------------------------------------------------------------------------------------------
def pair_network(pairs: tuple) -> PairNetwork:
    edges, unresolved, dts = [], 0, []
    for p in pairs:
        if not p.times_resolved or p.ref_time is None or p.sec_time is None:
            unresolved += 1
            continue
        edges.append((p.ref_time, p.sec_time))
        if p.dt_days is not None:
            dts.append(p.dt_days)
    if not edges:
        return PairNetwork(n_pairs=len(pairs), n_unresolved=unresolved)
    parent: dict = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in edges:
        parent[find(a)] = find(b)
    comp_dates: dict = {}
    for d in list(parent):
        comp_dates.setdefault(find(d), set()).add(d)
    comp_pairs: dict = {}
    for a, b in edges:
        comp_pairs[find(a)] = comp_pairs.get(find(a), 0) + 1
    big = max(comp_dates, key=lambda r: (comp_pairs.get(r, 0), len(comp_dates[r]), r))
    dates = tuple(sorted(parent))
    return PairNetwork(len(pairs), len(dates), len(comp_dates), comp_pairs.get(big, 0), len(comp_dates[big]),
                       min(dts) if dts else None, max(dts) if dts else None, len(comp_dates) == 1, dates, unresolved)


def coherence_baseline(ctx: BuildContext, key: str, w) -> CoherenceBaseline | None:
    t = ctx.cfg.temporal
    P = len(w.pairs)
    if w.coherence is None or P < 2:
        return None
    last = P - 1
    newest = w.pairs[last].sec_time
    cut = _cut(ctx, parse_time(newest)) if newest else None
    prior = [i for i in range(last)
             if w.pairs[i].times_resolved and (cut is None or parse_time(w.pairs[i].sec_time) < cut)]
    if len(prior) < t.coherence_baseline_min_pairs:
        return None
    med, mad, n = _nan_stats(w.coherence[prior], t.mad_scale)
    return CoherenceBaseline(med, mad, n, w.coherence[last].astype(np.float32), len(prior))


def run(ctx: BuildContext) -> None:
    per_stack: dict = {}
    for key in ctx.stack_keys:
        rec = dict(pairs=0, pairs_connected=0, dates=0, gcov_epochs=0, history_days=0.0, cold_start=True)
        w = ctx.ifg.get(key)
        if w is not None and w.pairs:
            w.network = pair_network(w.pairs)
            w.coherence_baseline = coherence_baseline(ctx, key, w)
            net = w.network
            rec.update(pairs=net.n_pairs, pairs_connected=net.largest_component_pairs, dates=net.n_dates)
            if net.dates:
                rec["history_days"] = (parse_time(net.dates[-1]) - parse_time(net.dates[0])).total_seconds() / 86400.0
            if not net.connected and net.n_pairs:
                ctx.flag("network_disconnected", "warn", "P10", f"{key}: {net.n_components} disconnected sub-networks; "
                         f"largest has {net.largest_component_pairs} of {net.n_pairs} pairs", stack=key)
            if net.n_unresolved:
                ctx.flag("pair_dates_unresolved", "warn", "P10", f"{key}: {net.n_unresolved} pair(s) with unparseable dates",
                         stack=key)
            rec["cold_start"] = net.n_dates - 1 < ctx.cfg.temporal.min_baseline_epochs
        c = ctx.cov.get(key)
        if c is not None and c.epochs:
            c.baseline = backscatter_baseline(ctx, key, c)
            rec["gcov_epochs"] = len(c.epochs)
            rec["history_days"] = max(rec["history_days"], c.baseline.history_days)
            rec["cold_start"] = c.baseline.cold_start if w is None or not w.pairs else (rec["cold_start"] and c.baseline.cold_start)
        per_stack[key] = rec
    if not per_stack:
        ctx.depth = DataDepth()
        return
    ctx.depth = DataDepth(
        n_gunw_pairs=max(r["pairs"] for r in per_stack.values()),
        n_gunw_pairs_connected=max(r["pairs_connected"] for r in per_stack.values()),
        n_gunw_dates=max(r["dates"] for r in per_stack.values()),
        n_gcov_epochs=max(r["gcov_epochs"] for r in per_stack.values()),
        history_days=max(r["history_days"] for r in per_stack.values()),
        cold_start=all(r["cold_start"] for r in per_stack.values()),
        per_stack=FrozenDict({k: FrozenDict(v) for k, v in per_stack.items()}))
