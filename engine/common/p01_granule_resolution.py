# P01 — keep newest processing version, build GUNW pair graph, tag track/frame/direction
"""Which granules make up this tile's picture, and how they relate.

Searches through the injected retriever (anything with `search(bbox, t0, t1, product)`; `NisarClient` is one),
then:

1. keeps only the newest processing version of each scene,
2. tags every granule with track / frame / direction, taking ASF's metadata first and the granule id second
   (a disagreement is flagged, not guessed away),
3. groups granules by stack = (direction, relative orbit), merges adjacent frames of one pass into a single
   epoch / pair,
4. for GUNW, reads reference and secondary times from the id and orders every pair so that ref < sec
   (a pair stored the other way round is marked `flipped`; P06 normalises the sign),
5. keeps the newest `max_gcov_epochs` epochs / `max_gunw_pairs` pairs per stack and lists the granules to fetch.

Nothing is fetched here. A pair whose dates cannot be parsed is kept but marked unresolved and excluded from
the network metrics, never assigned made-up dates.
"""
from __future__ import annotations

import re
from datetime import timedelta

from .context import (BuildContext, EpochGroup, GranuleMeta, PairGroup, Resolved, iso, parse_time)
from .layers import wanted_products

_TS = re.compile(r"(?<!\d)(\d{8}T\d{6})(?!\d)")
_TRK = re.compile(r"_(\d{3})_([AD])_(\d{3})(?=_)")
_VERSION_TAIL = re.compile(r"_([A-Z])(\d{5})(_[A-Z]_[A-Z]_[A-Z]_\d{3})$")   # same convention as retrieval.scene_key


def parse_granule_id(gid: str) -> dict:
    """Best-effort fields from a NISAR granule id: all timestamps in order, track, direction, frame."""
    times = [parse_time(t) for t in _TS.findall(gid)]
    m = _TRK.search(gid)
    return {"times": tuple(times),
            "track": int(m.group(1)) if m else None,
            "direction": m.group(2) if m else None,
            "frame": int(m.group(3)) if m else None}


def stack_key(direction: str | None, track: int | None) -> str:
    """"A005": direction + relative orbit. "UNK" when neither is known; a partial tag keeps what is known."""
    if not direction and track is None:
        return "UNK"
    return f"{direction or 'X'}{'' if track is None else format(int(track), '03d')}"


def _norm_dir(d) -> str | None:
    if not d:
        return None
    d = str(d).strip().upper()
    return {"ASCENDING": "A", "A": "A", "DESCENDING": "D", "D": "D"}.get(d)


def _scene_key(gid: str) -> tuple[str, int]:
    try:
        from ..retrieval.clients.nisar_client import scene_key
        return scene_key(gid)
    except ImportError:                                  # retrieval's optional deps (aiohttp...) not installed
        m = _VERSION_TAIL.search(gid)
        return (gid[:m.start()] + m.group(3), int(m.group(2))) if m else (gid, 0)


def meta_for(ref, ctx: BuildContext | None = None) -> GranuleMeta:
    p = parse_granule_id(ref.granule_id)
    d_asf, d_id = _norm_dir(ref.direction), p["direction"]
    t_asf, t_id = ref.track, p["track"]
    direction, track = d_asf or d_id, t_asf if t_asf is not None else t_id
    if ctx is not None:
        if d_asf and d_id and d_asf != d_id or (t_asf is not None and t_id is not None and int(t_asf) != t_id):
            ctx.flag("track_mismatch", "warn", "P01", "ASF track/direction disagrees with the granule id; ASF used",
                     granule=ref.granule_id, asf=(t_asf, d_asf), parsed=(t_id, d_id))
    frame = ref.frame if ref.frame is not None else p["frame"]
    times = p["times"]
    if not times and ref.start:
        try:
            times = (parse_time(ref.start),)
        except ValueError:
            times = ()
    return GranuleMeta(ref, ref.product or "", times, stack_key(direction, track),
                       int(track) if track is not None else None, direction,
                       int(frame) if frame is not None else None, _scene_key(ref.granule_id)[1])


def _drop_older(refs: list) -> tuple[list, int]:
    best: dict = {}
    for i, r in enumerate(refs):
        key, ver = _scene_key(r.granule_id)
        if key not in best or ver > best[key][0]:
            best[key] = (ver, i)
    keep = sorted(i for _, i in best.values())
    return [refs[i] for i in keep], len(refs) - len(keep)


def _cluster(items: list, key, minutes: float) -> list:
    """Group consecutive items (already sorted) whose `key` is within `minutes` of the previous one."""
    groups: list = []
    for it in items:
        if groups and abs((key(it) - key(groups[-1][-1])).total_seconds()) <= minutes * 60:
            groups[-1].append(it)
        else:
            groups.append([it])
    return groups


def _search(ctx: BuildContext, product: str):
    r = ctx.retriever
    margin = ctx.cfg.clip.margin_m
    if margin is None:
        margin = getattr(getattr(r, "cfg", None), "tiling", {}).get("margin_m", 300.0) if r is not None else 300.0
    bbox = ctx.tile.bbox(margin)
    return r.search(bbox, iso(ctx.request.t0), iso(ctx.request.t1), product)


def resolve(ctx: BuildContext, products: list[str]) -> Resolved:
    """Search + grouping for `products`, with every flag P01 raises while doing it.

    The one implementation of P01's algorithm: `run()` (the chain step) and the granule-availability
    endpoint (engine/api/jobs.py) both call this. Installs the result on `ctx.resolved` and returns it.
    Post-loop bookkeeping (to_fetch, the no_granules / older_versions_dropped flags) stays with the
    caller, because each caller derives "nothing found" from its own view of the groups."""
    res = ctx.resolved = Resolved()
    cfg = ctx.cfg
    names = {"GUNW": cfg.products.gunw, "GCOV": cfg.products.gcov, "GOFF": cfg.products.goff}
    as_of = ctx.request.as_of
    merge = cfg.window.epoch_merge_minutes

    for prod in products:
        name = names[prod]
        try:
            refs, degraded = _search(ctx, name)
        except Exception as ex:                          # reported, never silent
            res.search_errors[prod] = f"{type(ex).__name__}: {ex}"
            ctx.flag("search_error", "error", "P01", f"{prod} search failed: {ex}", product=prod)
            continue
        if degraded:
            res.search_degraded = True
            ctx.flag("search_degraded", "warn", "P01", f"{prod} listing served from a stale cache (ASF unreachable)", product=prod)
        refs = [r for r in refs if (r.product or name) == name]
        refs, dropped = _drop_older(refs)
        res.dropped_older += dropped
        metas = [meta_for(r, ctx) for r in refs]

        if prod == "GCOV":
            _group_epochs(ctx, res, metas, merge, as_of)
        else:
            _group_pairs(ctx, res, metas, prod, merge, as_of)
    return res


def run(ctx: BuildContext) -> None:
    ctx.resolved = Resolved()
    if ctx.retriever is None:
        ctx.flag("no_retriever", "error", "P01", "no retriever supplied: nothing to resolve")
        return
    cfg = ctx.cfg
    products = wanted_products(ctx.request.active_codes, cfg.products.include_goff)
    if not products:
        ctx.flag("no_nisar_measurement", "info", "P01",
                 "none of the active manifests needs a NISAR measurement (M1/M2/M3)", codes=sorted(ctx.request.active_codes or []))
        return
    res = resolve(ctx, products)

    for prod, target in (("GUNW", res.gunw), ("GCOV", res.gcov), ("GOFF", res.goff)):
        ids = []
        for groups in target.values():
            for g in groups:
                ids.extend(m.ref for m in g.granules)
        if ids:
            res.to_fetch[prod] = ids
    if not res.to_fetch and not res.search_errors:
        ctx.flag("no_granules", "warn", "P01", "no NISAR granules cover this tile in the window",
                 t0=iso(ctx.request.t0), t1=iso(ctx.request.t1))
    if res.dropped_older:
        ctx.flag("older_versions_dropped", "info", "P01", f"{res.dropped_older} superseded processing version(s) ignored")


def _group_epochs(ctx, res: Resolved, metas: list, merge: float, as_of) -> None:
    by_stack: dict = {}
    for m in metas:
        if not m.times:
            ctx.flag("undated_granule", "warn", "P01", "granule has no parseable acquisition time; ignored",
                     granule=m.ref.granule_id)
            continue
        if m.times[0] > as_of:
            continue
        by_stack.setdefault(m.stack_key, []).append(m)
    for key, ms in by_stack.items():
        ms.sort(key=lambda m: (m.times[0], m.ref.granule_id))
        groups = [EpochGroup(key, g[0].times[0], g) for g in _cluster(ms, lambda m: m.times[0], merge)]
        keep = groups[-ctx.cfg.window.max_gcov_epochs:]
        if len(groups) > len(keep):
            ctx.flag("epochs_truncated", "info", "P01", f"stack {key}: kept newest {len(keep)} of {len(groups)} epochs", stack=key)
        res.gcov[key] = keep


def _group_pairs(ctx, res: Resolved, metas: list, prod: str, merge: float, as_of) -> None:
    target = res.gunw if prod == "GUNW" else res.goff
    by_stack: dict = {}
    for m in metas:
        t = m.times
        if len(t) >= 4:
            ref_t, sec_t, ok = t[0], t[2], True
        else:
            ref_t = sec_t = None
            ok = False
            ctx.flag("unresolved_pair_dates", "warn", "P01",
                     f"{prod} id carries {len(t)} timestamp(s), a pair needs 4; dates left unresolved",
                     granule=m.ref.granule_id)
        flipped = False
        if ok and sec_t < ref_t:
            ref_t, sec_t, flipped = sec_t, ref_t, True
        if ok and sec_t > as_of:
            continue
        dt = (sec_t - ref_t).total_seconds() / 86400.0 if ok else None
        by_stack.setdefault(m.stack_key, []).append((m, PairGroup(m.stack_key, ref_t, sec_t, dt, flipped, ok, [m])))
    for key, items in by_stack.items():
        resolved = sorted((x for x in items if x[1].resolved), key=lambda x: (x[1].sec_time, x[1].ref_time, x[0].ref.granule_id))
        unresolved = [x for x in items if not x[1].resolved]
        merged: list = []
        for grp in _cluster(resolved, lambda x: x[1].sec_time, merge):
            # same secondary pass and same reference pass -> adjacent frames of one pair
            sub: list = []
            for x in grp:
                for s in sub:
                    if abs((s[0][1].ref_time - x[1].ref_time).total_seconds()) <= merge * 60:
                        s.append(x)
                        break
                else:
                    sub.append([x])
            for s in sub:
                g0 = s[0][1]
                merged.append(PairGroup(key, g0.ref_time, g0.sec_time, g0.dt_days, g0.flipped, True, [x[0] for x in s]))
        merged.sort(key=lambda g: (g.sec_time, g.ref_time))
        keep = merged[-ctx.cfg.window.max_gunw_pairs:]
        if len(merged) > len(keep):
            ctx.flag("pairs_truncated", "info", "P01", f"stack {key}: kept newest {len(keep)} of {len(merged)} pairs", stack=key)
        target[key] = keep + [x[1] for x in unresolved]


STEP_NAME = "granule_resolution"
