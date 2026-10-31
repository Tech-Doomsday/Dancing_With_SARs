"""6-hourly ASF diff: find NISAR granules we have not seen, work out which MGRS tiles each one
touches, and queue those tiles for the hazard pipelines. Newer processing versions of a scene
supersede (and de-queue) the older one.

    poller = GranulePoller(client)
    result = poller.poll()                 # one pass
    for item in poller.state.claim(20):    # pipeline workers
        ...; poller.state.complete(item["tile"], item["granule_id"])
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from ...tiling.mgrs import tiles_for_polygon
from ..clients.nisar_client import NisarClient, scene_key
from ..clients.types import GranuleRef
from ..state import State


@dataclass
class PollResult:
    new: list[GranuleRef] = field(default_factory=list)
    superseded: list[tuple[str, str]] = field(default_factory=list)      # (old granule id, new granule id)
    dirty: dict[str, list[str]] = field(default_factory=dict)            # tile id -> granule ids queued
    errors: dict[str, str] = field(default_factory=dict)                 # product -> reason
    degraded: bool = False                                               # ASF unreachable: stale listing used
    no_footprint: list[str] = field(default_factory=list)                # granules we could not map to tiles


class GranulePoller:
    def __init__(self, client: NisarClient, state: State | None = None, searcher=None):
        self.client = client
        self.cfg = client.cfg
        self.state = state or State(self.cfg.state_db)
        self._search = searcher or (lambda bbox, t0, t1, product: client.search(
            bbox, t0, t1, product, fresh=True, max_results=int(self.cfg.poll["max_results"])))

    # ------------------------------------------------------------------
    def tiles_for(self, ref: GranuleRef) -> list[str] | None:
        if not ref.footprint:
            return None
        ring = ref.footprint
        region = self.cfg.poll.get("region")
        if region:
            from shapely.geometry import Polygon, box
            part = Polygon(ring).buffer(0).intersection(box(*region))
            if part.is_empty:
                return []
            ring = list(part.exterior.coords) if part.geom_type == "Polygon" else \
                list(max(part.geoms, key=lambda g: g.area).exterior.coords)
        cell, margin = int(self.cfg.tiling["cell_size_m"]), 0.0
        return [t.id for t in tiles_for_polygon(ring, cell, margin)]

    def poll(self, now: datetime | None = None) -> PollResult:
        now = now or datetime.now(timezone.utc)
        t0 = (now - timedelta(days=float(self.cfg.poll["lookback_days"]))).strftime("%Y-%m-%dT%H:%M:%SZ")
        t1 = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        region = self.cfg.poll.get("region")
        res = PollResult()
        for product in self.cfg.poll["products"]:
            try:
                refs, degraded = self._search(tuple(region) if region else None, t0, t1, product)
            except Exception as ex:                                       # reported, never silent
                res.errors[product] = f"{type(ex).__name__}: {ex}"
                self.state.log_poll(product, 0, 0, res.errors[product])
                continue
            res.degraded |= degraded
            n_new = 0
            for ref in sorted(refs, key=lambda r: (r.start or "", r.granule_id)):
                if self.state.seen(ref.granule_id):
                    continue
                tiles = self.tiles_for(ref)
                if tiles is None:
                    res.no_footprint.append(ref.granule_id)
                    tiles = []
                key, ver = scene_key(ref.granule_id)
                older, already_superseded = self.state.add_granule(ref, key, ver, tiles, time.time())
                n_new += 1
                res.new.append(ref)
                res.superseded += [(o, ref.granule_id) for o in older]
                if not already_superseded:
                    for t in tiles:
                        res.dirty.setdefault(t, []).append(ref.granule_id)
            if not degraded:                                              # a stale listing is not a successful poll
                self.state.log_poll(product, len(refs), n_new)
        return res

    def run_forever(self, report=print):
        interval = float(self.cfg.poll["interval_hours"]) * 3600
        while True:
            r = self.poll()
            report(f"[{datetime.now():%Y-%m-%d %H:%M}] new={len(r.new)} superseded={len(r.superseded)} "
                   f"dirty_tiles={len(r.dirty)} errors={r.errors or 'none'} degraded={r.degraded} queue={self.state.counts()}")
            time.sleep(interval)
