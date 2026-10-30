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
        "epoch_merge_minutes": merge,
    }


__all__ = ["enrich_granule", "group_stacks", "search_bbox_sync"]
