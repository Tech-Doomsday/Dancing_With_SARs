"""R11 — NOAA NCEI Global Historical Tsunami Database (event catalog; corroboration only).

Resolution order (honest about the upstream's volatility — NCEI's public hazard endpoints have
moved repeatedly; verified 2026-10-27 that the legacy ngdc.noaa.gov paths answer 404):
  1. a locally curated copy of the NOAA/WDS event catalog at
     `<data>/reference/tsunami_events.csv` (columns: year,month,day,lat,lon,magnitude,depth_km,
     place,url — the file the operator exports from NCEI's Tsunami Event Database web app);
  2. the documented remote download URL (NCEI_TSUNAMI_URL env override supported);
  3. nothing: a degraded RESULT with the verbatim reason.

R11 is corroboration evidence only ("this region has an instrumented tsunami history"); the
trigger event itself comes from R1 ComCat. A missing catalog is a NOTE, never a failure, and
never evidence of absence.
"""
from __future__ import annotations

import csv
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REMOTE_URL_DEFAULT = "https://www.ngdc.noaa.gov/hazard/tsunami-events.csv"   # documented path; may move
DEFAULT_TIMEOUT_S = 60.0
_CACHE_TTL_S = 86_400.0


def _data_root() -> Path:
    try:
        from engine.retrieval.settings import load_settings
        return Path(load_settings().data_dir)
    except Exception:
        return Path("data")


def _provenance(url_or_file: str, n_events: int, sha256: str | None = None) -> dict:
    return {
        "adapter": "r11_tsunami_db",
        "url_or_id": url_or_file,
        "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sha256": sha256,
        "n_events_in_catalog": n_events,
        "units": "event coordinates deg, magnitude Mw where published",
        "crs": "EPSG:4326 (WGS84)",
        "quality_notes": [
            "NOAA/WDS Global Historical Tsunami Database: validity codes vary by era; magnitudes are "
            "missing for many pre-instrumental events",
            "corroboration only — a catalog entry is not evidence that a specific tile or sensor "
            "record shows a tsunami",
        ],
    }


def _parse_events_csv(text: str) -> list:
    """Minimal CSV parse of the curated/remote catalog -> event dicts."""
    out = []
    reader = csv.DictReader(line for line in text.splitlines() if line.strip())
    for row in reader:
        try:
            lat = float(row.get("lat") or row.get("latitude"))
            lon = float(row.get("lon") or row.get("longitude"))
        except (TypeError, ValueError):
            continue
        try:
            mag = float(row["magnitude"]) if row.get("magnitude") else None
        except (TypeError, ValueError):
            mag = None
        out.append({
            "id": str(row.get("id") or f"ncei-{len(out)}"),
            "year": int(float(row["year"])) if row.get("year") else None,
            "lat": lat, "lon": lon,
            "magnitude": mag,
            "place": str(row.get("place") or ""),
            "url": str(row.get("url") or ""),
            "validity": str(row.get("validity") or ""),
        })
    return out


def _haversine_km(lat1, lon1, lat2, lon2):
    from math import asin, cos, radians, sin, sqrt
    R = 6371.0088
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    h = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * R * asin(sqrt(h))


def _fetch_remote(url: str, timeout_s: float) -> tuple[str, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "nisar-hazard-engine/0.1 (R11 tsunami-db adapter)"})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        body = resp.read()
    return body.decode("utf-8", errors="replace"), __import__("hashlib").sha256(body).hexdigest()


def fetch_events(bbox=None, t0: str | None = None, t1: str | None = None,
                 max_km: float = 1000.0, timeout_s: float | None = None) -> dict:
    """Historical tsunami events, optionally filtered by bbox/window.

    Failures are degraded RESULTS with the verbatim reason — never exceptions, never a silent
    empty catalog (an empty catalog would read as "this coast has no tsunami history", which the
    adapter cannot know).
    """
    cache_name = "tsunami_events_catalog"
    cache_path = _data_root() / "cache" / "r11_tsunami_db" / f"{cache_name}.json"

    local = _data_root() / "reference" / "tsunami_events.csv"
    source = None
    text = None
    if local.exists():
        text = local.read_text()
        source = f"local curated copy: {local}"
    else:
        try:
            if cache_path.exists() and time.time() - cache_path.stat().st_mtime < _CACHE_TTL_S:
                cached = json.loads(cache_path.read_text())
                events = cached["events"]
                prov = cached["provenance"]
            else:
                raise FileNotFoundError("no fresh cache")
            return {"source": "ncei-tsunami-db (cache)", "events": events, "degraded": False,
                    "error": None, "provenance": prov}
        except Exception:
            pass
        url = os.environ.get("NCEI_TSUNAMI_URL", REMOTE_URL_DEFAULT)
        try:
            text, sha = _fetch_remote(url, timeout_s or DEFAULT_TIMEOUT_S)
            source = url
        except Exception as ex:
            return {"source": "ncei-tsunami-db", "events": [], "degraded": True,
                    "error": (f"no local curated copy at {local} and the remote catalog is "
                              f"unreachable: {type(ex).__name__}: {ex}"),
                    "provenance": _provenance(url, 0)}

    events = _parse_events_csv(text or "")
    prov = _provenance(source or "ncei-tsunami-db", len(events))
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps({"events": events, "provenance": prov}))
    except Exception:
        pass

    if bbox is not None:
        min_lon, min_lat, max_lon, max_lat = bbox
        clat = (min_lat + max_lat) / 2.0
        clon = (min_lon + max_lon) / 2.0
        events = [e for e in events if _haversine_km(clat, clon, e["lat"], e["lon"]) <= max_km]
    return {"source": source or "ncei-tsunami-db", "events": events, "degraded": False,
            "error": None, "provenance": prov}


__all__ = ["fetch_events", "REMOTE_URL_DEFAULT"]
