"""E8 — bbox granule availability for the Granule Explorer (/granules).

Metadata-only: runs the retriever's SEARCH half for a geographic box (never a fetch/clip) and
enriches every granule with (a) the filename fields VERIFIED in engine/common/granule_id.py and
(b) whatever the search provider states about the granule (GranuleRef.extra — absent = "the
provider did not say", never guessed). Stacks group exactly the way P01 groups (direction +
relative orbit, the p01.stack_key convention), so the Explorer and the scan chain cannot
disagree about what a stack is.

Day gaps, epochs and baselines are engine-computed here (globe task DEC13); the UI only draws
them. `nominal_repeat_days` stays null: the engine has no verified source for it, and the task
forbids showing one unless the engine supplies it.
"""
from __future__ import annotations

import statistics
from datetime import datetime, timezone

from engine.common.granule_id import decode_granule_id

_DIR_NAMES = {"ASCENDING": "A", "A": "A", "DESCENDING": "D", "D": "D"}


def _norm_direction(d: str | None) -> str | None:
    if not d:
        return None
    return _DIR_NAMES.get(str(d).strip().upper())


def enrich_granule(ref) -> dict:
    """One GranuleRef -> the Explorer's per-granule record. Unknown = null, never guessed."""
    dec = decode_granule_id(ref.granule_id)
    extra = getattr(ref, "extra", None) or {}
    direction = _norm_direction(getattr(ref, "direction", None)) or dec["direction"]
    track = getattr(ref, "track", None)
    if track is None:
        track = dec["track"]
    frame = getattr(ref, "frame", None)
    if frame is None:
        frame = dec["frame"]
    flags = list(dec["undecoded"])

    # provider/id disagreements are flagged, not guessed away (the P01 rule)
    track_p = getattr(ref, "track", None)
    if track_p is not None and dec["track"] is not None and int(track_p) != dec["track"]:
        flags.append(f"track mismatch: provider {track_p} vs id {dec['track']} (provider used)")

    polarization = extra.get("mainBandPolarization") or dec["polarization"]
    crid = extra.get("crid") or dec["crid"]
    collection = extra.get("collectionName")

    bandwidth = dec["range_bandwidth_mhz"]
    rb_provider = extra.get("rangeBandwidth")
    if rb_provider is not None:
        try:
            bandwidth = float(rb_provider)          # CMR RANGE_BANDWIDTH is already MHz
        except (TypeError, ValueError):
            flags.append(f"provider range bandwidth {rb_provider!r} not a number (kept raw)")
            bandwidth = None

    orbit_accuracy = {
        "asf": extra.get("orbitType"),              # e.g. "MOE" — provider's own words
        "filename": dec["orbit_token"],             # raw single-letter token (undecoded; flagged)
    }
    return {
        "id": ref.granule_id,
        "product": ref.product or dec["product_decoded"],
        "stack": _stack_key(direction, track),
        "direction": direction,
        "track": int(track) if track is not None else None,
        "frame": int(frame) if frame is not None else None,
        "start": getattr(ref, "start", None),
        "id_start": dec["start"],   # §4.2: the FILENAME-decoded start (null when the id matches no layout)
        "stop": getattr(ref, "stop", None),
        "ref_time": dec["ref_time"],
        "sec_time": dec["sec_time"],
        "baseline_days": dec["baseline_days"],
        "cycle_ref": dec["cycle_ref"],
        "cycle_sec": dec["cycle_sec"],
        "polarization": polarization,
        "range_bandwidth_mhz": bandwidth,
        "collection": collection,
        "crid": crid,
        "version": dec["version"],
        "orbit_accuracy": orbit_accuracy,
        "size_bytes": getattr(ref, "size_bytes", None),
        "footprint": getattr(ref, "footprint", None),
        "flags": flags,
    }


def _stack_key(direction: str | None, track: int | None) -> str:
    """The P01 convention ('A034'); imported lazily so this module has no import-order coupling."""
    from engine.common.p01_granule_resolution import stack_key
    return stack_key(direction, track)


def _parse(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        from engine.common.context import parse_time
        return parse_time(s)
    except ValueError:
        return None


def _cluster_epochs(times: list[datetime], merge_minutes: float) -> list[datetime]:
    """Adjacent times within merge_minutes are ONE acquisition epoch (the P01 merge rule)."""
    out: list[datetime] = []
    for t in sorted(times):
        if out and abs((t - out[-1]).total_seconds()) <= merge_minutes * 60:
            continue
        out.append(t)
    return out


def group_stacks(granules: list[dict], merge_minutes: float = 10.0) -> list[dict]:
    """Per-stack summary for the Explorer: counts, distinct epochs, day gaps. Ascending first,
    then descending, then by track (the timeline's lane order)."""
    by_stack: dict[str, list[dict]] = {}
    for g in granules:
        by_stack.setdefault(g["stack"], []).append(g)

    stacks: list[dict] = []
    for key in sorted(by_stack, key=lambda k: (k.startswith("D"), k.startswith("X"), k)):
        members = by_stack[key]
        first = members[0]
        times: list[datetime] = []
        for g in members:
            for field in ("ref_time", "sec_time", "start"):
                t = _parse(g.get(field))
                if t is not None:
                    times.append(t)
        epochs = _cluster_epochs(times, merge_minutes)
        gaps = [round((b - a).total_seconds() / 86400.0, 3) for a, b in zip(epochs, epochs[1:])]
        stacks.append({
            "key": key,
            "direction": first.get("direction"),
            "track": first.get("track"),
            "n_granules": len(members),
            "n_epochs": len(epochs),
            "epochs": [e.strftime("%Y-%m-%dT%H:%M:%SZ") for e in epochs],
            "gaps_days": gaps,
            "min_gap_days": min(gaps) if gaps else None,
            "median_gap_days": round(statistics.median(gaps), 3) if gaps else None,
            "max_gap_days": max(gaps) if gaps else None,
            "nominal_repeat_days": None,   # no verified engine source for it — never invented
            "first": epochs[0].strftime("%Y-%m-%dT%H:%M:%SZ") if epochs else None,
            "last": epochs[-1].strftime("%Y-%m-%dT%H:%M:%SZ") if epochs else None,
        })
    return stacks


def search_bbox_sync(*, bbox: tuple[float, float, float, float], levels: list[str],
                     t0: str, t1: str, product_names: dict[str, str], max_results: int,
                     fresh: bool, retriever) -> dict:
    """The worker-side search: one retriever.search per level, then enrich + group. Metadata
    only — no fetch, no clip, no NISAR_ENABLE_SCANS gate (DEC9), but it does need credentials
    (JobRunner.granule_search_bbox answers 503 before this runs)."""
    generated_at = datetime.now(timezone.utc).replace(microsecond=0)
    t0_dt = _parse(t0)
    t1_dt = _parse(t1)
    granules: list[dict] = []
    degraded = False
    truncated = False
    for level in levels:
        refs, deg = retriever.search(list(bbox), t0, t1, product_names[level],
                                     fresh=fresh, max_results=max_results)
        degraded = degraded or bool(deg)
        # the budget applies PER search: a level that hit it is truncated; the union of several
        # levels exceeding the budget is not (the retriever's own rule: len(refs) >= budget)
        truncated = truncated or len(refs) >= max_results
        granules.extend(enrich_granule(r) for r in refs)

    # de-duplicate ids (a granule id is unique; re-listing the same id twice would double-count)
    seen: set[str] = set()
    unique: list[dict] = []
    for g in granules:
        if g["id"] in seen:
            continue
        seen.add(g["id"])
        unique.append(g)
    # deterministic order: stack lane order, then ref/start time, then id
    unique.sort(key=lambda g: (g["stack"], g["ref_time"] or g["start"] or "", g["id"]))

    from engine.common.config import load_common_config
    merge = float(load_common_config().window.epoch_merge_minutes)
    stacks = group_stacks(unique, merge)
    truncated = bool(truncated)
    return {
        "bbox": [float(v) for v in bbox],
        "window": {"t0": t0_dt.strftime("%Y-%m-%dT%H:%M:%SZ") if t0_dt else t0,
                   "t1": t1_dt.strftime("%Y-%m-%dT%H:%M:%SZ") if t1_dt else t1},
        "n_total": len(unique),
        "truncated": truncated,
        "degraded": degraded,
        "search_budget": int(max_results),
        "generated_at": generated_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "granules": unique,
        "stacks": stacks,
        "groups": group_acquisitions(unique, merge, truncated),
        "epoch_merge_minutes": merge,
    }


# --------------------------------------------------------------------------- #
# §4.2 acquisition groups (resolution variants) — computed by the engine; the UI only draws.
# The same acquisition can exist several times (different range bandwidth, CRID, version,
# collection, polarization set). Group keys follow the task exactly:
#   pair products (GUNW/GOFF/…): stack + reference epoch + secondary epoch
#   single-acquisition products (GCOV/…): stack + start epoch
# with epochs clustered within the engine's epoch_merge_minutes. A multi-frequency token such
# as 4005 is NOT decoded here (it stays raw on each variant); nothing is ever merged across
# distinct acquisitions and no variant is hidden. Granules with no verified time (including an
# id that matches no known layout) stay UNGROUPED — shown raw, never invented into a group.
# --------------------------------------------------------------------------- #
_SINGLE_PRODUCTS = {"GCOV", "GSLC", "RSLC"}
# GOFF is a pair product (task §4.1: "GOFF, if present, is treated as a pair"); the decoder's
# pair layout already covers GUNW/GOFF/RUNW/ROFF/RIFG.
_FACT_FIELDS = ("range_bandwidth_mhz", "crid", "version", "collection", "polarization")


def _epoch_dt(g: dict, field: str):
    return _parse(g.get(field))


def _epoch_iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _pair_times(g: dict) -> tuple[datetime, datetime] | None:
    ref, sec = _epoch_dt(g, "ref_time"), _epoch_dt(g, "sec_time")
    if ref is None or sec is None:
        return None
    return (min(ref, sec), max(ref, sec))     # true time order; the flag carries the storage order


def _single_time(g: dict) -> datetime | None:
    # §4.2: group keys come from the FILENAME-decoded start only — a provider-side start never
    # groups an id the decoder could not read ("id that matches no layout -> ungrouped and raw").
    return _epoch_dt(g, "id_start")


def group_acquisitions(granules: list[dict], merge_minutes: float = 10.0,
                       truncated: bool = False) -> list[dict]:
    """One group per distinct acquisition; each group lists its resolution variants. Times are
    clustered with epoch_merge_minutes (the P01 merge rule). Deterministic: groups sorted by
    (stack, first time, key), variants sorted by granule id."""
    merge_s = float(merge_minutes) * 60.0
    pairs: list[tuple[tuple, dict]] = []       # (sort key, granule)
    for g in granules:
        product = (g.get("product") or "").upper()
        if product in _SINGLE_PRODUCTS:
            t = _single_time(g)
            if t is not None:
                pairs.append(((g["stack"], t, t, g["id"]), g))
        else:
            ts = _pair_times(g)
            if ts is not None:
                pairs.append(((g["stack"], ts[0], ts[1], g["id"]), g))
        # granules without a verified time fall through -> ungrouped below

    # cluster: same stack, epochs within merge_s (adjacent-sorted, the P01 merge rule)
    pairs.sort(key=lambda t: (t[0][0], t[0][1], t[0][2], t[0][3]))
    clusters: list[list[dict]] = []
    cur: list[dict] = []
    cur_key = None
    for key, g in pairs:
        stack, t_ref, t_sec, _gid = key
        if cur and cur_key is not None:
            p_stack, p_ref, p_sec, _ = cur_key
            same = (stack == p_stack
                    and abs((t_ref - p_ref).total_seconds()) <= merge_s
                    and abs((t_sec - p_sec).total_seconds()) <= merge_s)
            if not same:
                clusters.append(cur)
                cur = []
        if not cur:
            cur_key = key
        cur.append(g)
    if cur:
        clusters.append(cur)

    groups: list[dict] = []
    for members in clusters:
        first = members[0]
        product = (first.get("product") or "").upper()
        is_pair = product not in _SINGLE_PRODUCTS
        t_ref = _pair_times(first) if is_pair else None
        start = _single_time(first)
        rep_ref = _epoch_iso(t_ref[0]) if t_ref else None
        rep_sec = _epoch_iso(t_ref[1]) if t_ref else None
        rep_start = _epoch_iso(start) if start else None
        key = (f"{first['stack']}|{rep_ref}|{rep_sec}" if is_pair
               else f"{first['stack']}|{rep_start}")
        differing = [f for f in _FACT_FIELDS if len({m.get(f) for m in members}) > 1]
        gflags: list[str] = []
        if truncated:
            gflags.append("search truncated — this group may be incomplete")
        if is_pair and any(any("flipped" in f for f in m.get("flags", [])) for m in members):
            gflags.append("pair stored secondary-first (flipped); ends labelled by true time order")
        groups.append({
            "key": key,
            "stack": first["stack"],
            "product": first.get("product"),
            "kind": "pair" if is_pair else "single",
            "ref_time": rep_ref,
            "sec_time": rep_sec,
            "start": rep_start,
            "baseline_days": first.get("baseline_days"),
            "n_variants": len(members),
            "grouped": True,
            "variant_facts": differing,
            "granule_ids": [m["id"] for m in sorted(members, key=lambda m: m["id"])],
            "flags": gflags,
            "variants": sorted(members, key=lambda m: m["id"]),
        })

    # ungroupable granules (no verified time — e.g. an id matching no known layout) stay raw
    grouped_ids = {gid for grp in groups for gid in grp["granule_ids"]}
    for g in granules:
        if g["id"] in grouped_ids:
            continue
        groups.append({
            "key": f"ungrouped|{g['id']}",
            "stack": g["stack"],
            "product": g.get("product"),
            "kind": "single" if (g.get("product") or "").upper() in _SINGLE_PRODUCTS else "unknown",
            # group times are FILENAME-decoded only (null here); the provider's own start stays
            # on the variant record — never promoted into a group key the id does not support
            "ref_time": g.get("ref_time"),
            "sec_time": g.get("sec_time"),
            "start": g.get("id_start"),
            "baseline_days": g.get("baseline_days"),
            "n_variants": 1,
            "grouped": False,
            "variant_facts": [],
            "granule_ids": [g["id"]],
            "flags": ["not grouped: no verified acquisition time (id matches no known layout or timestamps absent)"],
            "variants": [g],
        })
    groups.sort(key=lambda grp: (grp["stack"], grp["ref_time"] or grp["start"] or "", grp["key"]))
    return groups


__all__ = ["enrich_granule", "group_stacks", "group_acquisitions", "search_bbox_sync"]
