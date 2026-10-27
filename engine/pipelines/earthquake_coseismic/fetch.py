"""3.01 earthquake_coseismic — own_fetch: R1 ComCat events (+ R2 ShakeMap note).

`run(bundle)` has a fixed signature, so fetching happens inside the pipeline
behind a module-level provider seam (the pattern docs/READINESS.md prescribes):
tests run offline by injecting a provider; production always uses the REAL
R1 adapter — there is no synthetic fallback anywhere.

A failed or empty fetch is a RESULT (EventsResult.degraded / .error), never an
exception, and maps to INSUFFICIENT_EVIDENCE in verdict.py — never to
"no event found".
"""
from __future__ import annotations

from datetime import datetime, timedelta

from .types import EventRecord, EventsResult

_provider = None   # set by tests / the API to an offline or alternate source


def set_event_provider(fn) -> None:
    """Inject an event source: fn(bbox, t0, t1) -> EventsResult."""
    global _provider
    _provider = fn


def reset_event_provider() -> None:
    """Back to the real R1 adapter (also the state in a fresh worker process)."""
    global _provider
    _provider = None


def _event_window(ref_time: str | None, sec_time: str | None, buffer_days: float) -> tuple[str, str]:
    fmt = "%Y-%m-%dT%H:%M:%SZ"

    def _parse(t: str | None, default: datetime) -> datetime:
        if not t:
            return default
        try:
            return datetime.fromisoformat(str(t).replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return default

    end_default = datetime.utcnow()
    sec = _parse(sec_time, end_default)
    ref = _parse(ref_time, sec - timedelta(days=30))
    start = ref - timedelta(days=buffer_days)
    end = sec + timedelta(days=buffer_days)
    return start.strftime(fmt), end.strftime(fmt)


def fetch_events(bbox, ref_time: str | None, sec_time: str | None, params) -> EventsResult:
    """bbox = (min_lon, min_lat, max_lon, max_lat). Real R1 ComCat path by default."""
    from engine.retrieval.clients.adapters import r1_comcat

    t0, t1 = _event_window(ref_time, sec_time, params.event_buffer_days)
    if _provider is not None:
        return _provider(bbox, t0, t1)

    out = r1_comcat.fetch_events(
        bbox, t0, t1,
        min_magnitude=4.0, limit=200,
    )
    events = tuple(EventRecord.from_mapping(e) for e in out.get("events", []))
    return EventsResult(events=events, source=out.get("source", "usgs-comcat"),
                        degraded=bool(out.get("degraded")), error=out.get("error"))
