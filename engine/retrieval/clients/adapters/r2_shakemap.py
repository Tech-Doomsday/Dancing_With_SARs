"""R2 — USGS ShakeMap expected-extent (REAL implementation, evidence-only use).

For the primary event: fetch the ShakeMap product's extent.json (bounding box
of the felt/strong-motion area) via the ComCat product contents. Used ONLY as
corroboration evidence ("expected extent covers / does not cover the tile").
Absence of a ShakeMap product is a note, never a failure.
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


def fetch_extent(event_id: str, timeout_s: float | None = None) -> dict:
    """-> {"available": bool, "bbox": [min_lon, min_lat, max_lon, max_lat]|None,
           "note": str, "degraded": bool, "error": str|None}

    bbox order matches the bundle's tile.bbox_lonlat convention (W, S, E, N).
    """
    detail = r1_comcat.fetch_event_detail(event_id, timeout_s=timeout_s)
    if detail.get("error") or not detail.get("event"):
        return {"available": False, "bbox": None, "note": "event detail unavailable",
                "degraded": True, "error": detail.get("error")}

    products = detail["event"].get("products") or {}
    shakemaps = products.get("shakemap") or []
    if not shakemaps:
        return {"available": False, "bbox": None,
                "note": "no ShakeMap product published for this event (a note, not a failure)",
                "degraded": False, "error": None}

    contents = (shakemaps[0].get("contents") or {})
    extent_ref = contents.get("download/extent.json") or {}
    extent_url = extent_ref.get("url")
    if not extent_url:
        return {"available": False, "bbox": None,
                "note": "ShakeMap product exists but exposes no extent.json",
                "degraded": False, "error": None}
    try:
        extent = _http_get_json(extent_url, timeout_s or DEFAULT_TIMEOUT_S)
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as ex:
        return {"available": False, "bbox": None, "note": "ShakeMap extent fetch failed",
                "degraded": True, "error": str(ex)}

    bbox = extent.get("bbox")
    if not (isinstance(bbox, list) and len(bbox) == 4):
        return {"available": False, "bbox": None, "note": "ShakeMap extent.json has no usable bbox",
                "degraded": False, "error": None}
    try:
        lon0, lat0, lon1, lat1 = (float(v) for v in bbox)
    except (TypeError, ValueError):
        return {"available": False, "bbox": None, "note": "ShakeMap extent bbox malformed",
                "degraded": False, "error": None}
    return {"available": True, "bbox": [min(lon0, lon1), min(lat0, lat1), max(lon0, lon1), max(lat0, lat1)],
            "note": "ShakeMap expected-extent retrieved", "degraded": False, "error": None}
