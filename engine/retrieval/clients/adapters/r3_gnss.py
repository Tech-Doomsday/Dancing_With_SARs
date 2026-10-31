"""R3 — Nevada Geodetic Lab GNSS (coseismic offset validation; OPTIONAL evidence).

What is actually reachable today (verified live 2026-10-27):
  - the NGL station holdings list https://geodesy.unr.edu/NGLStationPages/DataHoldings.txt
    (station id, lat/lon/height, solution span);
  - per-station IGS14 tenv3 displacement series under
    https://geodesy.unr.edu/gps_timeseries/tenv3/IGS14/<STA>/<STA>.tenv3
    (paths move occasionally; a 404 surfaces as a degraded result, never as invented offsets).

Contract (docs/IMPLEMENTATION_PLAN.md 9.3): the earthquake pipeline treats R3's absence as a
WARNING — GNSS comparison is optional validation evidence, never a gate. A coseismic step is
computed as the median offset of the residual series across the event day (the NGL series already
have steps applied at published times; a step AT the event day is expected to be absent there,
so the raw day-difference is used and labelled honestly).
"""
from __future__ import annotations

import hashlib
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

NGL_BASE = "https://geodesy.unr.edu"
HOLDINGS_URL = f"{NGL_BASE}/NGLStationPages/DataHoldings.txt"
TENV3_URL = NGL_BASE + "/gps_timeseries/tenv3/IGS14/{sta}/{sta}.tenv3"
DEFAULT_TIMEOUT_S = 60.0


def _cache_dir() -> Path:
    try:
        from engine.retrieval.settings import load_settings
        root = Path(load_settings().data_dir)
    except Exception:
        root = Path("data")
    d = root / "cache" / "r3_gnss"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _provenance(url_or_id: str, sha256: str | None = None) -> dict:
    return {
        "adapter": "r3_gnss",
        "url_or_id": url_or_id,
        "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sha256": sha256,
        "provider": "Nevada Geodetic Laboratory (mid-Atlantic states USA + global IGS14)",
        "units": "east/north/up offsets in metres (site frame IGS14)",
        "crs": "ITRF/IGS14, ellipsoidal heights",
        "quality_notes": [
            "GNSS comparison is optional validation evidence — its absence is a warning, never a gate",
            "NGL series already have published steps removed; a coseismic offset here is estimated "
            "from the day-level difference across the event and carries that caveat",
        ],
    }


def _fetch(url: str, timeout_s: float) -> tuple[bytes, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "nisar-hazard-engine/0.1 (R3 GNSS adapter)"})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        body = resp.read()
    return body, hashlib.sha256(body).hexdigest()


def _cache_get(name: str, max_age_s: float) -> bytes | None:
    p = _cache_dir() / name
    try:
        if p.exists() and time.time() - p.stat().st_mtime < max_age_s:
            return p.read_bytes()
    except Exception:
        return None
    return None


def _cache_put(name: str, body: bytes) -> None:
    try:
        (_cache_dir() / name).write_bytes(body)
    except Exception:
        pass


def nearby_stations(lat: float, lon: float, max_km: float = 200.0,
                    timeout_s: float | None = None) -> dict:
    """NGL stations within max_km of a point, nearest first (from the DataHoldings list)."""
    cached = _cache_get("holdings.txt", 7 * 86_400.0)
    sha = None
    if cached is not None:
        text = cached.decode("utf-8", errors="replace")
    else:
        try:
            body, sha = _fetch(HOLDINGS_URL, timeout_s or DEFAULT_TIMEOUT_S)
            text = body.decode("utf-8", errors="replace")
            _cache_put("holdings.txt", body)
        except Exception as ex:
            return {"source": "ngl-holdings", "stations": [], "degraded": True,
                    "error": f"station holdings unreachable: {type(ex).__name__}: {ex}",
                    "provenance": _provenance(HOLDINGS_URL)}

    from math import asin, cos, radians, sin, sqrt
    R = 6371.0088
    stations = []
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 11:
            continue
        try:
            sla, slo = float(parts[1]), float(parts[2])
        except ValueError:
            continue
        dlat = radians(sla - lat)
        dlon = radians(slo - lon)
        h = sin(dlat / 2) ** 2 + cos(radians(lat)) * cos(radians(sla)) * sin(dlon / 2) ** 2
        d = 2 * R * asin(sqrt(h))
        if d <= max_km:
            stations.append({"station": parts[0], "lat": sla, "lon": slo, "dist_km": round(d, 1)})
    stations.sort(key=lambda s: s["dist_km"])
    return {"source": "ngl-holdings", "stations": stations[:20], "degraded": False, "error": None,
            "provenance": _provenance(HOLDINGS_URL, sha)}


def coseismic_offsets(station_id: str, event_iso: str, half_window_days: float = 2.0,
                      timeout_s: float | None = None) -> dict:
    """Day-level east/north/up offset across an event, from the NGL tenv3 series.

    Returns {"offsets_m": {"east": .., "north": .., "up": ..}, "pre_samples", "post_samples", ...}
    or a degraded result. The estimate is honestly labelled: median of each component in the
    half-window after the event minus the median before it.
    """
    sta = station_id.strip().upper()
    url = TENV3_URL.format(sta=sta)
    cache_name = f"{sta}.tenv3"
    cached = _cache_get(cache_name, 86_400.0)
    if cached is not None:
        text = cached.decode("utf-8", errors="replace")
        sha = None
    else:
        try:
            body, sha = _fetch(url, timeout_s or DEFAULT_TIMEOUT_S)
            text = body.decode("utf-8", errors="replace")
            _cache_put(cache_name, body)
        except Exception as ex:
            return {"source": "ngl-tenv3", "station": sta, "offsets_m": None, "degraded": True,
                    "error": f"tenv3 series unreachable ({url}): {type(ex).__name__}: {ex}",
                    "provenance": _provenance(url)}

    try:
        event_t = datetime.fromisoformat(str(event_iso).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return {"source": "ngl-tenv3", "station": sta, "offsets_m": None, "degraded": True,
                "error": f"event time not parseable: {event_iso!r}", "provenance": _provenance(url)}

    pre, post = {"e": [], "n": [], "u": []}, {"e": [], "n": [], "u": []}
    for line in text.splitlines():
        p = line.split()
        if len(p) < 7 or p[0].startswith("#"):
            continue
        try:
            # tenv3 rows: Sta * Date(YYYYMMDD) DeciYear dE dN dU ...
            ymd = p[2]
            t = datetime.strptime(ymd, "%Y%m%d").replace(tzinfo=timezone.utc).timestamp()
            de, dn, du = float(p[4]), float(p[5]), float(p[6])
        except (ValueError, IndexError):
            continue
        if abs(t - event_t) > half_window_days * 86_400.0:
            continue
        bucket = pre if t < event_t else post
        bucket["e"].append(de)
        bucket["n"].append(dn)
        bucket["u"].append(du)

    def med(v):
        v = sorted(v)
        return v[len(v) // 2] if v else None

    if len(pre["e"]) < 5 or len(post["e"]) < 5:
        return {"source": "ngl-tenv3", "station": sta, "offsets_m": None, "degraded": True,
                "error": (f"insufficient coverage across the event ({len(pre['e'])} pre / "
                          f"{len(post['e'])} post samples in ±{half_window_days:g} d)"),
                "provenance": _provenance(url, sha)}
    offsets = {"east": round(med(post["e"]) - med(pre["e"]), 4),
               "north": round(med(post["n"]) - med(pre["n"]), 4),
               "up": round(med(post["u"]) - med(pre["u"]), 4)}
    return {"source": "ngl-tenv3", "station": sta, "offsets_m": offsets,
            "pre_samples": len(pre["e"]), "post_samples": len(post["e"]),
            "degraded": False, "error": None, "provenance": _provenance(url, sha)}


__all__ = ["nearby_stations", "coseismic_offsets", "HOLDINGS_URL"]
