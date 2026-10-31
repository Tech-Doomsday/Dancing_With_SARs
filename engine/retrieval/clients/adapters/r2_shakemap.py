"""R2 — USGS ShakeMap expected-extent (REAL implementation, evidence-only use).

For the primary event: fetch the ShakeMap product's extent.json (bounding box
of the felt/strong-motion area) via the ComCat product contents. Used ONLY as
corroboration evidence ("expected extent covers / does not cover the tile").
Absence of a ShakeMap product is a note, never a failure.

NORMALISED RESULT SHAPE (final-prompt fix 4 — every field always present, so a
caller can never render "R2 ShakeMap extent for None"):

    {"event_id":   str,                # always the requested id
     "available":  bool,               # a usable extent bbox was retrieved
     "bbox":       [W, S, E, N] | None,
     "url":        str | None,         # the extent.json url, when fetched
     "note":       str,                # human-readable outcome
     "degraded":   bool,
     "error":      str | None,
     "coverage":   {                   # ONLY when tile_bbox was passed
         "tile_bbox":    [W, S, E, N],
         "covers_tile":  bool,         # tile fully inside the ShakeMap extent
         "overlaps_tile": bool,        # any geographic intersection
     }} | None
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from . import r1_comcat

DEFAULT_TIMEOUT_S = 30.0


def _http_get_json(url: str, timeout_s: float) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "nisar-hazard-engine/0.1 (R2 ShakeMap adapter)"})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _normalise(event_id: str, *, available: bool, bbox=None, url=None, note: str = "",
               degraded: bool = False, error: str | None = None,
               tile_bbox=None, coverage=None) -> dict:
    out = {"event_id": event_id, "available": available, "bbox": bbox, "url": url,
           "note": note, "degraded": degraded, "error": error, "coverage": coverage}
    if tile_bbox is not None and available and bbox:
        out["coverage"] = coverage_check(tile_bbox, bbox)
    return out


def coverage_check(tile_bbox, extent_bbox) -> dict:
    """Tile-vs-ShakeMap-extent coverage (plain lon/lat rectangle math).

    Simple lon/lat intersection — correct everywhere except within a few degrees of
    the antimeridian; no catalogued 10 km tile straddles it and the check is
    corroboration evidence, not a gate.
    """
    tw, ts, te, tn = (float(v) for v in tile_bbox)
    ew, es, ee, en = (float(v) for v in extent_bbox)
    covers = (ew <= tw and es <= ts and ee >= te and en >= tn)
    overlaps = (tw <= ee and te >= ew and ts <= en and tn >= es)
    return {"tile_bbox": [tw, ts, te, tn], "covers_tile": covers, "overlaps_tile": overlaps}


def fetch_extent(event_id: str, timeout_s: float | None = None, tile_bbox=None) -> dict:
    """Normalised ShakeMap extent for `event_id` (shape documented in the module docstring).

    bbox order matches the bundle's tile.bbox_lonlat convention (W, S, E, N).
    Pass `tile_bbox` to get the tile-coverage check embedded in the result.
    """
    detail = r1_comcat.fetch_event_detail(event_id, timeout_s=timeout_s)
    if detail.get("error"):
        return _normalise(event_id, available=False, note="event detail unavailable",
                          degraded=True, error=detail.get("error"), tile_bbox=tile_bbox)
    event = detail.get("event")
    if not event:
        return _normalise(event_id, available=False,
                          note="event not resolvable in ComCat (no detail record)",
                          degraded=bool(detail.get("degraded")), tile_bbox=tile_bbox)

    products = event.get("products") or {}
    shakemaps = products.get("shakemap") or []
    if not shakemaps:
        return _normalise(event_id, available=False,
                          note="no ShakeMap product published for this event (a note, not a failure)",
                          tile_bbox=tile_bbox)

    contents = (shakemaps[0].get("contents") or {})
    extent_ref = contents.get("download/extent.json") or {}
    extent_url = extent_ref.get("url")
    if not extent_url:
        return _normalise(event_id, available=False,
                          note="ShakeMap product exists but exposes no extent.json",
                          tile_bbox=tile_bbox)
    try:
        extent = _http_get_json(extent_url, timeout_s or DEFAULT_TIMEOUT_S)
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as ex:
        return _normalise(event_id, available=False, url=extent_url,
                          note="ShakeMap extent fetch failed", degraded=True, error=str(ex),
                          tile_bbox=tile_bbox)

    bbox = extent.get("bbox")
    if not (isinstance(bbox, list) and len(bbox) == 4):
        return _normalise(event_id, available=False, url=extent_url,
                          note="ShakeMap extent.json has no usable bbox", tile_bbox=tile_bbox)
    try:
        lon0, lat0, lon1, lat1 = (float(v) for v in bbox)
    except (TypeError, ValueError):
        return _normalise(event_id, available=False, url=extent_url,
                          note="ShakeMap extent bbox malformed", tile_bbox=tile_bbox)
    return _normalise(event_id, available=True,
                      bbox=[min(lon0, lon1), min(lat0, lat1), max(lon0, lon1), max(lat0, lat1)],
                      url=extent_url, note="ShakeMap expected-extent retrieved",
                      tile_bbox=tile_bbox)
