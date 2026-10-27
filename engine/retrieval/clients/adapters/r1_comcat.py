"""R1 — USGS ComCat FDSN events (REAL implementation; no credentials needed).

Implied adapter signature (docs/READINESS.md): fetch(bbox, t0, t1) -> typed table.
Returns a plain dict so retrieval stays pipeline-agnostic:

    {"source": "usgs-comcat", "events": [...], "degraded": bool, "error": str|None}

Event dicts carry exactly the fields pipeline EventRecord.from_mapping expects.
Failures are results, never exceptions. A short-TTL disk cache absorbs repeated
UI/scan queries (request-keyed, sha1 of the canonical query).
"""
from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

FDSN_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"
DEFAULT_TIMEOUT_S = 30.0
CACHE_TTL_S = 900.0          # 15 minutes
_CacheEntry = tuple[float, dict]


def _cache_dir() -> Path:
    try:
        from engine.retrieval.settings import load_settings
        root = Path(load_settings().data_dir)
    except Exception:
        root = Path("data")
    d = root / "cache" / "r1_comcat"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_get(key: str) -> dict | None:
    path = _cache_dir() / f"{key}.json"
    try:
        if path.exists() and time.time() - path.stat().st_mtime < CACHE_TTL_S:
            return json.loads(path.read_text())
    except Exception:
        return None
    return None


def _cache_put(key: str, payload: dict) -> None:
    try:
        (_cache_dir() / f"{key}.json").write_text(json.dumps(payload))
    except Exception:
        pass  # cache write failures never break the fetch


def _http_get_json(url: str, timeout_s: float) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "nisar-hazard-engine/0.1 (R1 ComCat adapter)"})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _parse_nodal_planes(properties: dict) -> list:
    """Nodal planes from focal-mechanism (preferred) or moment-tensor products."""
    planes: list = []
    products = properties.get("products") or {}
    for product_key in ("focal-mechanism", "moment-tensor"):
        for product in products.get(product_key) or []:
            props = product.get("properties") or {}
            for np_key in ("nodal-plane-1", "nodal-plane-2"):
                strike = props.get(f"{np_key}-strike")
                dip = props.get(f"{np_key}-dip")
                rake = props.get(f"{np_key}-rake")
                if None not in (strike, dip, rake):
                    try:
                        plane = (float(strike), float(dip), float(rake))
                        if plane not in planes:
                            planes.append(plane)
                    except (TypeError, ValueError):
                        continue
            if planes:
                break
        if planes:
            break
    return planes


def parse_event_feature(feature: dict) -> dict | None:
    """GeoJSON feature -> event dict (None when the feature is unusable)."""
    try:
        props = feature.get("properties") or {}
        geom = feature.get("geometry") or {}
        coords = geom.get("coordinates") or [None, None, None]
        lon, lat, depth_km = coords[0], coords[1], coords[2]
        if lat is None or lon is None:
            return None
        mag = props.get("mag")
        time_ms = props.get("time")
        time_iso = None
        if time_ms is not None:
            from datetime import datetime, timezone
            time_iso = datetime.fromtimestamp(time_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {
            "id": str(feature.get("id") or props.get("eventSource") or f"unnamed-{time_ms}"),
            "magnitude": (float(mag) if mag is not None else None),
            "place": str(props.get("place") or ""),
            "time": time_iso or "",
            "lat": float(lat),
            "lon": float(lon),
            "depth_km": (float(depth_km) if depth_km is not None else None),
            "url": props.get("url"),
            "nodal_planes": _parse_nodal_planes(props),
        }
    except (TypeError, ValueError, KeyError):
        return None


def fetch_events(bbox, t0: str, t1: str, *, min_magnitude: float = 4.0, limit: int = 200,
                 timeout_s: float | None = None) -> dict:
    """ComCat event query around the bbox centre.

    bbox = (min_lon, min_lat, max_lon, max_lat). t0/t1 are ISO-8601 UTC strings.
    """
    min_lon, min_lat, max_lon, max_lat = bbox
    lat = (min_lat + max_lat) / 2.0
    lon = (min_lon + max_lon) / 2.0
    params = {
        "format": "geojson",
        "starttime": t0,
        "endtime": t1,
        "minmagnitude": str(min_magnitude),
        "latitude": f"{lat:.4f}",
        "longitude": f"{lon:.4f}",
        "maxradiuskm": "400",
        "orderby": "magnitude",
        "limit": str(limit),
    }
    url = f"{FDSN_URL}?{urllib.parse.urlencode(params)}"
    key = hashlib.sha1(url.encode("utf-8")).hexdigest()

    cached = _cache_get(key)
    if cached is not None:
        return {**cached, "source": "usgs-comcat (cache)"}

    try:
        payload = _http_get_json(url, timeout_s or DEFAULT_TIMEOUT_S)
    except urllib.error.HTTPError as ex:
        return {"source": "usgs-comcat", "events": [], "degraded": True, "error": f"HTTP {ex.code} from ComCat ({url})"}
    except (urllib.error.URLError, TimeoutError, OSError) as ex:
        return {"source": "usgs-comcat", "events": [], "degraded": True, "error": f"ComCat unreachable: {ex}"}
    except json.JSONDecodeError as ex:
        return {"source": "usgs-comcat", "events": [], "degraded": True, "error": f"ComCat returned malformed JSON: {ex}"}

    features = payload.get("features") or []
    events = []
    for feature in features:
        ev = parse_event_feature(feature)
        if ev is not None:
            events.append(ev)
    out = {"source": "usgs-comcat", "events": events, "degraded": False, "error": None}
    _cache_put(key, out)
    return out


def fetch_event_detail(event_id: str, timeout_s: float | None = None) -> dict:
    """One event incl. products (used by the R2 adapter and the API context route).

    Result shape: {"source", "event": dict|None, "degraded", "error"}.
    """
    params = {"format": "geojson", "eventid": event_id}
    url = f"{FDSN_URL}?{urllib.parse.urlencode(params)}"
    key = hashlib.sha1(("detail:" + url).encode("utf-8")).hexdigest()

    cached = _cache_get(key)
    if cached is not None:
        return {**cached, "source": "usgs-comcat (cache)"}
    try:
        payload = _http_get_json(url, timeout_s or DEFAULT_TIMEOUT_S)
    except urllib.error.HTTPError as ex:
        return {"source": "usgs-comcat", "event": None, "degraded": True, "error": f"HTTP {ex.code} from ComCat"}
    except (urllib.error.URLError, TimeoutError, OSError) as ex:
        return {"source": "usgs-comcat", "event": None, "degraded": True, "error": f"ComCat unreachable: {ex}"}
    except json.JSONDecodeError as ex:
        return {"source": "usgs-comcat", "event": None, "degraded": True, "error": f"ComCat returned malformed JSON: {ex}"}

    props = payload.get("properties") or {}
    geometry = payload.get("geometry") or {}
    coords = (geometry.get("coordinates") or [None, None, None])
    event = None
    if coords[1] is not None and coords[0] is not None:
        mag = props.get("mag")
        time_ms = props.get("time")
        from datetime import datetime, timezone
        event = {
            "id": str(payload.get("id") or event_id),
            "magnitude": (float(mag) if mag is not None else None),
            "place": str(props.get("place") or ""),
            "time": (datetime.fromtimestamp(time_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                     if time_ms is not None else ""),
            "lat": float(coords[1]), "lon": float(coords[0]),
            "depth_km": (float(coords[2]) if coords[2] is not None else None),
            "url": props.get("url"),
            "nodal_planes": _parse_nodal_planes(props),
            "products": {k: v for k, v in (props.get("products") or {}).items()
                         if k in ("shakemap", "finite-fault", "moment-tensor", "focal-mechanism")},
        }
    out = {"source": "usgs-comcat", "event": event, "degraded": False, "error": None}
    _cache_put(key, out)
    return out
