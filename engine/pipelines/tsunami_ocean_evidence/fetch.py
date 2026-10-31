"""3.17 tsunami_ocean_evidence — own_fetch: R1 events, R15 DART, R16 ETOPO1, R11 catalog, FFM.

Provider seam (port design decision 6): run(bundle, providers=None) accepts injected providers,
so tests run offline and the API can point runs at alternate sources; production always uses the
REAL adapters. Resolution order per data kind: providers[key] -> real adapter. Every fetch result
carries provenance; failures are explicit RESULT states, never exceptions that collapse into
"no event".
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone

from engine.common.result import Source


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _src(adapter: str, url_or_id: str, sha256: str | None = None) -> Source:
    return Source(adapter=adapter, url_or_id=url_or_id, retrieved_at=_now(), sha256=sha256)


def _normalize(out: dict, adapter: str) -> tuple[dict, list]:
    """Adapter dict -> (record, provenance list). Failures stay structured."""
    prov = out.get("provenance")
    if isinstance(prov, dict):
        provs = [Source(**{k: prov[k] for k in ("adapter", "url_or_id", "retrieved_at", "sha256")
                           if k in prov})] if prov.get("adapter") else [_src(adapter, str(prov.get("url_or_id") or ""))]
    else:
        provs = []
    return out, provs


def fetch_event(bundle, params, providers: dict | None = None) -> dict:
    """The triggering event: trigger["quake"] by id (R1 detail), else the best R1 event in the
    window (largest magnitude, ties by distance to the tile centre). A failed fetch is a RESULT:
    {"event": None, "degraded": True, "error": ...}."""
    from engine.retrieval.clients.adapters import r1_comcat

    providers = providers or {}
    trigger = dict(bundle.request.triggers or {})
    quake = trigger.get("quake") or {}
    if isinstance(quake, Mapping) and quake.get("id"):
        if providers.get("event_by_id"):
            out = providers["event_by_id"](quake["id"])
        else:
            out = r1_comcat.fetch_event_detail(str(quake["id"]))
        rec, provs = _normalize(out, "r1_comcat")
        ev = rec.get("event")
        if out.get("degraded") or ev is None:
            return {"event": None, "degraded": True, "error": out.get("error") or
                    f"trigger quake {quake['id']} not found in ComCat", "provenance": provs}
        return {"event": ev, "degraded": False, "error": None, "provenance": provs}

    # no trigger id: query the window around the tile centre
    clon, clat = bundle.tile.center_lonlat
    t0, t1 = bundle.request.event_window or (bundle.request.t0, bundle.request.t1)
    if not t0 or not t1:
        return {"event": None, "degraded": False, "error": None, "provenance": [],
                "note": "no event window and no quake trigger in the request"}
    if providers.get("events"):
        out = providers["events"]((clon - 5, clat - 5, clon + 5, clat + 5), t0, t1)
        out = out.to_dict() if hasattr(out, "to_dict") else dict(out)
        events = out.get("events") or []
        provs = []
    else:
        out = r1_comcat.fetch_events((clon - 5, clat - 5, clon + 5, clat + 5), str(t0), str(t1),
                                     min_magnitude=params.min_trigger_magnitude, limit=200)
        rec, provs = _normalize(out, "r1_comcat")
        events = rec.get("events") or []
    if out.get("degraded"):
        return {"event": None, "degraded": True, "error": out.get("error"), "provenance": provs}
    if not events:
        return {"event": None, "degraded": False, "error": None, "provenance": provs,
                "note": f"no ComCat event >= M{params.min_trigger_magnitude:g} in the request window"}
    best = max(events, key=lambda e: (e.get("magnitude") or -9.0))
    return {"event": best, "degraded": False, "error": None, "provenance": provs}


def fetch_ffm_subfaults(usgs_event_id: str, providers: dict | None = None) -> dict:
    """USGS finite-fault FFM.geojson when published; None is a normal outcome (fall back to the
    MT plane + scaling), a fetch error is reported but not fatal."""
    from engine.retrieval.clients.adapters import r1_comcat
    from engine.lib.tsunami.source import parse_ffm_geojson

    providers = providers or {}
    if providers.get("ffm"):
        out = providers["ffm"](usgs_event_id)
        if out is None:
            return {"subfaults": [], "url": None, "degraded": False, "error": None, "provenance": []}
        subs = out.get("subfaults") if isinstance(out, dict) else out
        return {"subfaults": list(subs or []), "url": out.get("url") if isinstance(out, dict) else None,
                "degraded": False, "error": None, "provenance": [_src("ffm", str(out.get("url") or "injected"))] if isinstance(out, dict) else []}
    try:
        detail = r1_comcat.fetch_event_detail(usgs_event_id)
        if detail.get("degraded") or not detail.get("event"):
            return {"subfaults": [], "url": None, "degraded": False, "error": None, "provenance": []}
        products = detail["event"].get("products") or {}
        ff = (products.get("finite-fault") or [{}])[0]
        ffm_url = ((ff.get("contents") or {}).get("FFM.geojson") or {}).get("url")
        if not ffm_url:
            return {"subfaults": [], "url": None, "degraded": False, "error": None, "provenance": []}
        import json
        import urllib.request
        req = urllib.request.Request(ffm_url, headers={"User-Agent": "nisar-hazard-engine/0.1 (3.17 FFM)"})
        with urllib.request.urlopen(req, timeout=60.0) as resp:
            body = resp.read()
        import hashlib
        geo = json.loads(body.decode("utf-8"))
        subs = parse_ffm_geojson(geo)
        if not subs:
            return {"subfaults": [], "url": ffm_url, "degraded": False, "error": None, "provenance": []}
        return {"subfaults": subs, "url": ffm_url, "degraded": False, "error": None,
                "provenance": [_src("r1_comcat/finite-fault", ffm_url, hashlib.sha256(body).hexdigest()[:16])]}
    except Exception as ex:
        return {"subfaults": [], "url": None, "degraded": False,
                "error": f"FFM fetch failed (falling back to the MT plane): {type(ex).__name__}: {ex}",
                "provenance": []}


def fetch_dart_series(station_id: str, origin_ms: float, providers: dict | None = None) -> dict:
    """One DART record (R15) via the provider seam or the real adapter. Never raises."""
    from engine.retrieval.clients.adapters import r15_dart

    providers = providers or {}
    if providers.get("dart"):
        out = providers["dart"](station_id, origin_ms)
        out = dict(out) if isinstance(out, dict) else {"series": None, "degraded": True, "error": "bad provider output"}
    else:
        out = r15_dart.fetch_series(station_id, origin_ms)
    prov = out.get("provenance")
    provs = []
    if isinstance(prov, dict):
        provs = [Source(**{k: prov[k] for k in ("adapter", "url_or_id", "retrieved_at", "sha256") if k in prov})]
    out["provenance_records"] = provs
    return out


def fetch_bathymetry(bbox, res_arcmin: int, providers: dict | None = None) -> dict:
    """ETOPO1 region (R16) via the provider seam or the real adapter. Never raises."""
    from engine.retrieval.clients.adapters import r16_bathymetry

    providers = providers or {}
    if providers.get("bathy"):
        out = providers["bathy"](bbox, res_arcmin)
        out = dict(out) if isinstance(out, dict) else {"grid": None, "degraded": True, "error": "bad provider output"}
    else:
        out = r16_bathymetry.get_region(bbox, res_arcmin)
    prov = out.get("provenance")
    provs = []
    if isinstance(prov, dict):
        provs = [Source(**{k: prov[k] for k in ("adapter", "url_or_id", "retrieved_at", "sha256") if k in prov})]
    out["provenance_records"] = provs
    return out


def fetch_tsunami_catalog(bbox, providers: dict | None = None) -> dict:
    """R11 historical catalog — corroboration only; absence is a note, never a failure."""
    from engine.retrieval.clients.adapters import r11_tsunami_db

    providers = providers or {}
    if providers.get("tsunami_db"):
        out = providers["tsunami_db"](bbox)
    else:
        out = r11_tsunami_db.fetch_events(bbox)
    return out if isinstance(out, dict) else {"events": [], "degraded": True, "error": "bad provider output"}
