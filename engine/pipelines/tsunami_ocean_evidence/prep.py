"""3.17 tsunami_ocean_evidence — own_prep: event selection, domain, stations.

Pure functions of the bundle and the fetched records (no fetching here, no bundle mutation).
"""
from __future__ import annotations

import math

import numpy as np

from engine.lib.geo import haversine_km

from .fetch import fetch_bathymetry, fetch_event, fetch_dart_series
from .types import StationDetection, TsunamiParams


def parse_iso_ms(t) -> float | None:
    if not t:
        return None
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(t).replace("Z", "+00:00")).timestamp() * 1000.0
    except ValueError:
        return None


def pick_event(bundle, params: TsunamiParams, providers: dict | None = None) -> dict:
    """-> fetch_event outcome (+ screening inputs ready)."""
    return fetch_event(bundle, params, providers=providers)


def build_domain(event: dict, params: TsunamiParams, providers: dict | None = None) -> dict:
    """ETOPO1 domain around the epicentre: ±domain_half_deg, snapped by the adapter, cell count
    capped by max_domain_cells (the resolution is degraded honestly rather than silently)."""
    lat, lon = float(event["lat"]), float(event["lon"])
    half = float(params.domain_half_deg)
    bbox = (lon - half, lat - half, lon + half, lat + half)
    res = int(params.res_arcmin)
    bathy = fetch_bathymetry(bbox, res, providers=providers)
    notes: list = []
    grid = bathy.get("grid")
    if grid and max(grid["nx"], grid["ny"]) > params.max_domain_cells:
        # degrade the resolution until the grid fits the cap (documented, never silent)
        while max(grid["nx"], grid["ny"]) > params.max_domain_cells and res < 8:
            res *= 2
            notes.append(f"domain exceeds {params.max_domain_cells} cells at {res // 2}' — degraded to {res}'")
        bathy = fetch_bathymetry(bbox, res, providers=providers)
        grid = bathy.get("grid")
    return {"bathy": bathy, "bbox": bbox, "res_arcmin": res, "notes": notes}


def pick_stations(event: dict, params: TsunamiParams, providers: dict | None = None) -> list:
    """DART stations within radius of the epicentre, nearest first (adapter inventory)."""
    from engine.retrieval.clients.adapters import r15_dart

    providers = providers or {}
    if providers.get("stations"):
        stations = list(providers["stations"](float(event["lat"]), float(event["lon"]),
                                              params.station_radius_km))
    else:
        stations = r15_dart.stations_within(float(event["lat"]), float(event["lon"]),
                                            params.station_radius_km)
    return stations[: params.max_stations]


def fetch_station_records(stations: list, origin_ms: float, providers: dict | None = None) -> list:
    """One record attempt per station; failures are explicit StationDetection.availability states."""
    out: list = []
    for st in stations:
        det = StationDetection(station_id=st["id"], lat=st["lat"], lon=st["lon"], dist_km=st["dist_km"])
        rec = fetch_dart_series(st["id"], origin_ms, providers=providers)
        if rec.get("degraded") or not rec.get("series"):
            det.availability = {"state": "missing", "reason": rec.get("error") or "record unavailable"}
        else:
            det.kind = rec.get("kind")
            det.availability = {"state": "available"}
            det._series = rec["series"]                      # type: ignore[attr-defined]
            det._provenance = rec.get("provenance_records", [])  # type: ignore[attr-defined]
        out.append(det)
    return out


def origin_of(event: dict, bundle) -> tuple[float, str]:
    """-> (epoch ms, ISO) of the event origin, falling back to the event window start."""
    ms = parse_iso_ms(event.get("time"))
    if ms is not None:
        return ms, str(event["time"])
    ew = bundle.request.event_window or (None, None)
    ms = parse_iso_ms(ew[1] or ew[0])
    return (ms or 0.0), str(ew[1] or ew[0] or "")


__all__ = ["pick_event", "build_domain", "pick_stations", "fetch_station_records", "origin_of",
           "parse_iso_ms"]
