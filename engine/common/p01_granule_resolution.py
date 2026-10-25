"""P01 - granule resolution: pair graph, track/frame/direction.

Turns the catalog's candidate granules into (current pair, bounded history,
matching GCOV granules) for ONE tile. Pure metadata work, no pixel I/O.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Sequence, Tuple

from ._types import BBox, GranuleRecord, GranuleResolutionError, TileSpec
from .config import PreprocConfig
from .state import F_DISCONNECTED, F_SINGLE_PAIR, BundleBuilder

STAGE = "P01"


@dataclass(frozen=True)
class GranuleResolution:
    group_key: Tuple
    current: GranuleRecord
    history: Tuple[GranuleRecord, ...]
    gcov_current: Optional[GranuleRecord]
    gcov_history: Tuple[GranuleRecord, ...]
    graph: Dict
    dropped: Tuple[Tuple[str, str], ...] = field(default_factory=tuple)

    @property
    def all_pairs(self) -> Tuple[GranuleRecord, ...]:
        return self.history + (self.current,)


def _ver_key(v: str) -> Tuple[int, ...]:
    nums = re.findall(r"\d+", v or "")
    return tuple(int(n) for n in nums) if nums else (0,)


def _intersects(a: Optional[BBox], b: BBox) -> bool:
    if a is None:
        return True
    return not (a[2] <= b[0] or a[0] >= b[2] or a[3] <= b[1] or a[1] >= b[3])


def pair_graph(pairs: Sequence[GranuleRecord]) -> Dict:
    """Connectivity of the acquisition-date network defined by GUNW pairs."""
    dates = sorted({d for p in pairs for d in (p.ref_time.date(), p.epoch_date)})
    parent = {d: d for d in dates}

    def find(x: date) -> date:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for p in pairs:
        a, b = find(p.ref_time.date()), find(p.epoch_date)
        if a != b:
            parent[a] = b
    comps: Dict[date, List[date]] = {}
    for d in dates:
        comps.setdefault(find(d), []).append(d)
    gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
    return {
        "n_epochs": len(dates), "n_pairs": len(pairs), "n_components": len(comps),
        "largest_component_epochs": max((len(v) for v in comps.values()), default=0),
        "span_days": float((dates[-1] - dates[0]).days) if dates else 0.0,
        "max_gap_days": max(gaps) if gaps else 0,
        "first_date": dates[0].isoformat() if dates else None,
        "last_date": dates[-1].isoformat() if dates else None,
    }


def _dedupe(records: Sequence[GranuleRecord], dropped: List[Tuple[str, str]]) -> List[GranuleRecord]:
    best: Dict[Tuple, GranuleRecord] = {}
    for r in records:
        key = (r.product, r.group_key, r.ref_time.date(), r.sec_time.date() if r.sec_time else None)
        cur = best.get(key)
        if cur is None or _ver_key(r.processing_version) > _ver_key(cur.processing_version):
            if cur is not None:
                dropped.append((cur.granule_id, "superseded_by_newer_processing_version"))
            best[key] = r
        else:
            dropped.append((r.granule_id, "superseded_by_newer_processing_version"))
    return list(best.values())


def resolve(candidates: Sequence[GranuleRecord], tile: TileSpec, cfg: PreprocConfig,
            target: Optional[GranuleRecord] = None) -> GranuleResolution:
    dropped: List[Tuple[str, str]] = []
    cands = []
    for r in candidates:
        if not _intersects(r.bbox, tile.bbox):
            dropped.append((r.granule_id, "outside_tile"))
        elif r.product == "GUNW" and (r.sec_time is None or r.sec_time <= r.ref_time):
            dropped.append((r.granule_id, "invalid_pair_order"))
        else:
            cands.append(r)
    gunw = _dedupe([r for r in cands if r.product == "GUNW"], dropped)
    gcov = _dedupe([r for r in cands if r.product == "GCOV"], dropped)

    if target is None:
        if not gunw:
            raise GranuleResolutionError(f"no GUNW pair intersects tile {tile.tile_id}")
        target = max(gunw, key=lambda r: (r.sec_time, _ver_key(r.processing_version)))
    elif target.product != "GUNW" or not target.is_pair:
        raise GranuleResolutionError("target must be a GUNW pair")
    else:  # make sure we use the freshest processing version of the requested pair
        same = [g for g in gunw if g.group_key == target.group_key
                and g.ref_time.date() == target.ref_time.date() and g.epoch_date == target.epoch_date]
        target = max(same, key=lambda r: _ver_key(r.processing_version)) if same else target

    key = target.group_key
    cur_date = target.epoch_date
    hist = []
    for g in gunw:
        if g.group_key != key or g.granule_id == target.granule_id:
            continue
        if g.epoch_date >= cur_date:
            dropped.append((g.granule_id, "not_older_than_current"))
        elif g.temporal_baseline_days is not None and g.temporal_baseline_days > cfg.max_temporal_baseline_days:
            dropped.append((g.granule_id, "baseline_too_long"))
        else:
            hist.append(g)
    hist.sort(key=lambda r: (r.epoch_date, r.ref_time), reverse=True)
    for g in hist[cfg.max_history_pairs:]:
        dropped.append((g.granule_id, "history_limit"))
    hist = sorted(hist[:cfg.max_history_pairs], key=lambda r: (r.epoch_date, r.ref_time))

    by_date: Dict[date, GranuleRecord] = {}
    for g in gcov:
        if g.group_key == key:
            d = g.ref_time.date()
            if d not in by_date or _ver_key(g.processing_version) > _ver_key(by_date[d].processing_version):
                by_date[d] = g
    gcov_cur = by_date.get(cur_date) or by_date.get(target.ref_time.date())
    older = sorted((d for d in by_date if d < cur_date and by_date[d] is not gcov_cur), reverse=True)
    gcov_hist = tuple(by_date[d] for d in sorted(older[:cfg.max_history_gcov]))

    graph = pair_graph(list(hist) + [target])
    return GranuleResolution(key, target, tuple(hist), gcov_cur, gcov_hist, graph, tuple(dropped))


def run(state: BundleBuilder) -> Dict:
    res = resolve(state.inp.candidates, state.inp.tile, state.cfg, state.inp.target)
    state.resolution = res
    if not res.history:
        state.flag(F_SINGLE_PAIR)
    if res.graph["n_components"] > 1:
        state.flag(F_DISCONNECTED)
    return {"history_pairs": len(res.history), "gcov_current": res.gcov_current is not None,
            "gcov_history": len(res.gcov_history), "dropped": len(res.dropped), **res.graph}
