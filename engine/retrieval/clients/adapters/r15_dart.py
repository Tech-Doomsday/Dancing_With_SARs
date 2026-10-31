"""R15 — NOAA DART deep-ocean bottom-pressure records (REAL implementation).

Two real access paths (both verified live 2026-10-27):
  - Historical archive: https://www.ndbc.noaa.gov/data/historical/dart/{ID}t{YEAR}.txt.gz
    (gzip, plain "#YY MM DD hh mm ss T HEIGHT" rows, HEIGHT = bottom pressure expressed as
    equivalent sea-surface height in metres; 9999.0 = missing)
  - Realtime: https://www.ndbc.noaa.gov/data/realtime2/{ID}.dart — same format, ~45-day lookback.

Station inventory: curated from the NDBC station pages (coordinates are the CURRENT deployment
positions; DART buoys are redeployed over the years — a documented limitation for historical
events). Nothing here fabricates data: parse failures, gaps and HTTP errors surface as explicit
degraded results, never as empty "no tsunami" records.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

NDBC_BASE = "https://www.ndbc.noaa.gov/data"
DEFAULT_TIMEOUT_S = 60.0
REALTIME_TTL_S = 3 * 3600.0

# id, lat, lon, name, region — NDBC DART deployment positions (curated 2026-10)
DART_STATIONS = (
    {"id": "21401", "lat": 42.617, "lon": 152.583, "name": "SE of Iturup Island", "region": "NW Pacific"},
    {"id": "21413", "lat": 30.487, "lon": 152.124, "name": "SE of Tokyo", "region": "NW Pacific"},
    {"id": "21414", "lat": 48.977, "lon": 178.199, "name": "S of Amchitka", "region": "N Pacific"},
    {"id": "21415", "lat": 50.120, "lon": 171.867, "name": "S of Attu", "region": "N Pacific"},
    {"id": "21416", "lat": 48.126, "lon": 163.355, "name": "SE of Kamchatka", "region": "N Pacific"},
    {"id": "21418", "lat": 38.840, "lon": 148.753, "name": "NE of Tokyo", "region": "NW Pacific"},
    {"id": "21419", "lat": 44.401, "lon": 155.653, "name": "SE of Kuril Islands", "region": "NW Pacific"},
    {"id": "21420", "lat": 28.980, "lon": 134.973, "name": "SE of Miyazaki", "region": "NW Pacific"},
    {"id": "32401", "lat": -20.611, "lon": -73.394, "name": "W of Iquique, Chile", "region": "SE Pacific"},
    {"id": "32402", "lat": -26.743, "lon": -73.983, "name": "W of Caldera, Chile", "region": "SE Pacific"},
    {"id": "32403", "lat": -23.163, "lon": -72.037, "name": "W of Antofagasta, Chile", "region": "SE Pacific"},
    {"id": "32404", "lat": -32.130, "lon": -73.797, "name": "NW of Valparaíso, Chile", "region": "SE Pacific"},
    {"id": "32411", "lat": 4.979, "lon": -90.793, "name": "WSW of Panama City", "region": "E Pacific"},
    {"id": "32412", "lat": -17.984, "lon": -86.374, "name": "SW of Lima", "region": "SE Pacific"},
    {"id": "32413", "lat": -7.432, "lon": -93.464, "name": "WNW of Lima", "region": "SE Pacific"},
    {"id": "43412", "lat": 16.020, "lon": -107.000, "name": "SW of Manzanillo", "region": "E Pacific"},
    {"id": "43413", "lat": 10.927, "lon": -100.012, "name": "S of Acapulco", "region": "E Pacific"},
    {"id": "46402", "lat": 50.913, "lon": -164.147, "name": "SSE of Dutch Harbor", "region": "Gulf of Alaska"},
    {"id": "46404", "lat": 45.852, "lon": -128.745, "name": "W of Astoria", "region": "US West Coast"},
    {"id": "51407", "lat": 19.530, "lon": -156.601, "name": "W of Kailua-Kona", "region": "Hawaii"},
    {"id": "52402", "lat": 11.928, "lon": 153.876, "name": "ESE of Saipan", "region": "W Pacific"},
    {"id": "52406", "lat": -5.374, "lon": 164.990, "name": "NE of Guadalcanal", "region": "W Pacific"},
)


def station_by_id(station_id: str) -> dict | None:
    for s in DART_STATIONS:
        if s["id"] == station_id:
            return dict(s)
    return None


def stations_within(lat: float, lon: float, max_km: float) -> list:
    """Stations within max_km of a point, nearest first (haversine, R = 6371.0088)."""
    from math import asin, cos, radians, sin, sqrt
    R = 6371.0088

    def dist(s):
        dlat = radians(s["lat"] - lat)
        dlon = radians(s["lon"] - lon)
        h = sin(dlat / 2) ** 2 + cos(radians(lat)) * cos(radians(s["lat"])) * sin(dlon / 2) ** 2
        return 2 * R * asin(sqrt(h))

    out = [dict(s, dist_km=round(dist(s), 1)) for s in DART_STATIONS]
    return sorted((s for s in out if s["dist_km"] <= max_km), key=lambda s: s["dist_km"])


def parse_dart_text(text: str) -> dict:
    """NDBC DART text (archive + realtime) -> {"t": [epoch ms], "v": [m]} ascending in time.

    Rows: "YYYY MM DD hh mm ss T HEIGHT"; missing HEIGHT = 9999.0. Realtime files arrive
    NEWEST-FIRST (verified live); the historical archive is oldest-first — sort ascending either way.
    """
    t_out, v_out = [], []
    for raw in text.split("\n"):
        line = raw.strip()
        if not line[:4].isdigit():
            continue
        p = line.split()
        if len(p) < 8:
            continue
        try:
            yy, mm, dd, hh, mi, ss = (int(p[0]), int(p[1]), int(p[2]), int(p[3]), int(p[4]), int(p[5]))
            v = float(p[7])
        except ValueError:
            continue
        if v >= 9000:                       # 9999 = missing
            continue
        try:
            t = datetime(yy, mm, dd, hh, mi, ss, tzinfo=timezone.utc).timestamp() * 1000.0
        except ValueError:
            continue
        t_out.append(t)
        v_out.append(v)
    order = sorted(range(len(t_out)), key=lambda i: t_out[i])
    return {"t": [t_out[i] for i in order], "v": [v_out[i] for i in order]}


def _cache_dir() -> Path:
    try:
        from engine.retrieval.settings import load_settings
        root = Path(load_settings().data_dir)
    except Exception:
        root = Path("data")
    d = root / "cache" / "dart"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_get(name: str, max_age_s: float) -> dict | None:
    p = _cache_dir() / f"{name}.json"
    try:
        if p.exists() and (max_age_s == float("inf") or time.time() - p.stat().st_mtime < max_age_s):
            return json.loads(p.read_text())
    except Exception:
        return None
    return None


def _cache_put(name: str, payload: dict) -> None:
    try:
        (_cache_dir() / f"{name}.json").write_text(json.dumps(payload))
    except Exception:
        pass  # cache write failures never break the fetch


def _fetch_bytes(url: str, timeout_s: float) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "nisar-hazard-engine/0.1 (R15 DART adapter)"})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return resp.read()


def _provenance(station: dict, kind: str, url: str, series: dict, sha256: str | None) -> dict:
    first = (datetime.fromtimestamp(series["t"][0] / 1000.0, tz=timezone.utc).isoformat(timespec="seconds")
             if series["t"] else "n/a")
    last = (datetime.fromtimestamp(series["t"][-1] / 1000.0, tz=timezone.utc).isoformat(timespec="seconds")
            if series["t"] else "n/a")
    return {
        "adapter": "r15_dart",
        "url_or_id": url,
        "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sha256": sha256,
        "station": station["id"], "kind": kind,
        "observation_start": first, "observation_end": last,
        "units": "bottom pressure as equivalent sea-surface height, metres",
        "crs": f"station position {station['lat']:.3f}, {station['lon']:.3f} (WGS84)",
        "quality_notes": [
            "station coordinates are the CURRENT deployment position (buoys are redeployed over the years)",
            "the pressure record can carry a seismic-phase excursion minutes after a large nearby "
            "earthquake — detection treats pre-origin excursions separately",
        ],
    }


def fetch_series(station_id: str, event_epoch_ms: float | None = None,
                 timeout_s: float | None = None) -> dict:
    """DART record for one station covering the event time.

    The realtime file (~45-day lookback) when the event is recent (or no event time given),
    otherwise the historical yearly archive; the other path is the fallback. Cached on disk:
    historical permanently, realtime for 3 hours. Failures are degraded RESULTS with the verbatim
    reason — never exceptions, never silently empty.
    """
    station = station_by_id(station_id)
    if station is None:
        return {"source": "ndbc-dart", "station": station_id, "kind": None, "series": None,
                "degraded": True, "error": f"unknown DART station {station_id}", "provenance": None}
    year = (datetime.fromtimestamp(event_epoch_ms / 1000.0, tz=timezone.utc).year
            if event_epoch_ms else datetime.now(timezone.utc).year)
    realtime_covers = event_epoch_ms is None or (time.time() * 1000.0 - event_epoch_ms < 50 * 86_400_000)

    rt_url = f"{NDBC_BASE}/realtime2/{station_id}.dart"
    hist_url = f"{NDBC_BASE}/historical/dart/{station_id}t{year}.txt.gz"

    def result(kind: str, series: dict, url: str, sha: str | None) -> dict:
        return {"source": "ndbc-dart", "station": station_id, "kind": kind, "series": series,
                "degraded": False, "error": None,
                "provenance": _provenance(station, kind, url, series, sha)}

    if realtime_covers:
        cached = _cache_get(f"{station_id}.realtime", REALTIME_TTL_S)
        if cached is not None:
            return result("realtime", cached["series"], cached.get("url", rt_url), cached.get("sha256"))
        try:
            buf = _fetch_bytes(rt_url, timeout_s or DEFAULT_TIMEOUT_S)
            text = buf.decode("utf-8", errors="replace")
            series = parse_dart_text(text)
            if not series["t"]:
                raise ValueError("realtime file parsed to 0 samples")
            sha = hashlib.sha256(buf).hexdigest()
            out = result("realtime", series, rt_url, sha)
            _cache_put(f"{station_id}.realtime", {"series": series, "url": rt_url, "sha256": sha})
            return out
        except Exception as ex:
            last_error = f"realtime fetch failed: {type(ex).__name__}: {ex}"
    else:
        last_error = "event older than the realtime window"

    cached = _cache_get(f"{station_id}.{year}.historical", float("inf"))
    if cached is not None:
        return result("historical", cached["series"], cached.get("url", hist_url), cached.get("sha256"))
    try:
        buf = _fetch_bytes(hist_url, timeout_s or DEFAULT_TIMEOUT_S)
        text = gzip.decompress(buf).decode("utf-8", errors="replace")
        series = parse_dart_text(text)
        if not series["t"]:
            raise ValueError(f"archive file parsed to 0 samples")
        sha = hashlib.sha256(buf).hexdigest()
        out = result("historical", series, hist_url, sha)
        _cache_put(f"{station_id}.{year}.historical", {"series": series, "url": hist_url, "sha256": sha})
        return out
    except Exception as ex:
        return {"source": "ndbc-dart", "station": station_id, "kind": None, "series": None,
                "degraded": True,
                "error": f"{last_error}; historical fallback failed: {type(ex).__name__}: {ex}",
                "provenance": _provenance(station, "unavailable", hist_url, {"t": [], "v": []}, None)}


__all__ = ["DART_STATIONS", "station_by_id", "stations_within", "parse_dart_text", "fetch_series"]
