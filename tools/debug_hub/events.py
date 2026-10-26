"""Thread-safe, append-only event log for one job (feeds the SSE stream and the live status bar)."""
from __future__ import annotations

import threading
import time


class Job:
    def __init__(self, jid: str, params: dict):
        self.id = jid
        self.params = params
        self.state = "queued"            # queued | running | done | failed
        self.error: str | None = None
        self.t0 = time.time()
        self.t_end: float | None = None
        self.events: list = []
        self._cv = threading.Condition()
        # filled by the runner; every field is derived from what the engine actually returned
        self.tile: dict = {}
        self.source: dict = {}
        self.calls: list = []            # recorded retriever calls
        self.search_refs: dict = {}      # product -> [GranuleRef]
        self.fetch_results: dict = {}    # product -> FetchResult
        self.retrieval: dict | None = None
        self.steps: list = []            # processing step records (live)
        self.step_counts: dict = {}      # step code -> {group: n arrays} after that step
        self.final: dict | None = None   # bundle summary
        self.handoff: dict | None = None
        self.net_last: dict = {}
        self.store = None                # ArrayStore, set by the runner

    def emit(self, kind: str, msg: str, stage: str | None = None, **data) -> dict:
        with self._cv:
            ev = {"seq": len(self.events) + 1, "t": round(time.time() - self.t0, 3), "kind": kind,
                  "stage": stage, "msg": msg, "data": data}
            self.events.append(ev)
            self._cv.notify_all()
            return ev

    def wait(self, after: int, timeout: float = 15.0) -> list:
        with self._cv:
            if len(self.events) <= after and self.state in ("queued", "running"):
                self._cv.wait(timeout)
            return self.events[after:]

    @property
    def finished(self) -> bool:
        return self.state in ("done", "failed")

    def finish(self, state: str, error: str | None = None) -> None:
        with self._cv:
            self.state, self.error, self.t_end = state, error, time.time()
            self._cv.notify_all()

    def brief(self) -> dict:
        return {"id": self.id, "state": self.state, "error": self.error, "params": self.params,
                "tile": self.tile.get("id"), "seconds": round((self.t_end or time.time()) - self.t0, 2),
                "events": len(self.events), "source": self.source.get("kind")}
