"""Background reads that have no time limit.

A read from the provider (granule detail, preview, probe, stack-pair) can take as long as NASA takes. Nothing here
gives up on it:

  * `submit` runs the read on the metadata pool and waits `api.meta_wait_s` (config/retrieval.yaml). If it is done,
    the caller gets the answer in the same request. If not, the caller gets a job id (the route answers 202) and
    the read keeps going; the UI polls `poll(job_id)` for as long as it likes.
  * A network failure (timeout, refused/reset connection, DNS, provider 5xx) is retried for ever, waiting
    `api.retry_backoff_s` between attempts (the last wait repeats). Every poll reports the attempt number and the last
    error, so the page can say what is happening. A definite answer (404 granule unknown, 400, 422, ...) is final.
  * The only ways a read stops early are an explicit `cancel` (the page navigated away) and the idle lease: a read
    nobody has asked about for `api.job_idle_s` is cancelled, so closed tabs do not leave retry loops running.

Framework-free on purpose (no FastAPI import): the routes in routes_background.py map the results to HTTP, and
tests/test_bg_jobs.py drives this module directly.
"""
from __future__ import annotations

import socket
import threading
import time
import uuid
from concurrent.futures import TimeoutError as FuturesTimeout

_MAX_JOBS = 50
_DEFAULTS = {"meta_wait_s": 20.0, "preview_wait_s": 2.0, "job_idle_s": 120.0, "poll_interval_s": 1.0,
             "retry_backoff_s": [2.0, 5.0, 10.0, 30.0]}
_NETWORK_MODULES = ("aiohttp", "requests", "urllib3", "httpx", "http.client", "ssl")
_NETWORK_NAME_BITS = ("timeout", "connection", "5xx", "servererror", "unavailable")


class Cancelled(RuntimeError):
    """The read was cancelled (page left, or nobody asked about it for the idle lease)."""


def api_config() -> dict:
    """config/retrieval.yaml `api` (env NISAR_API_*), with safe fallbacks for an old or partial file."""
    try:
        from engine.retrieval.settings import load_settings
        raw = dict(load_settings().api or {})
    except Exception:                                           # a broken config must not take the routes down
        raw = {}
    out = dict(_DEFAULTS)
    for k in ("meta_wait_s", "preview_wait_s", "job_idle_s", "poll_interval_s"):
        try:
            v = float(raw.get(k, out[k]))
            out[k] = v if v > 0 else _DEFAULTS[k]
        except (TypeError, ValueError):
            pass
    try:
        steps = [float(x) for x in (raw.get("retry_backoff_s") or [])]
        if steps and all(x > 0 for x in steps):
            out["retry_backoff_s"] = steps
    except (TypeError, ValueError):
        pass
    out["job_idle_s"] = max(out["job_idle_s"], 2 * out["meta_wait_s"])   # the lease must outlast the first wait
    return out


def is_transient(ex: BaseException) -> bool:
    """True for failures worth trying again: the network or the provider, never a definite answer."""
    if isinstance(ex, Cancelled):
        return False
    if isinstance(ex, (TimeoutError, ConnectionError, socket.gaierror, socket.timeout)):
        return True
    mod = type(ex).__module__ or ""
    name = type(ex).__name__.lower()
    if mod.startswith(("aiohttp", "requests", "urllib3", "httpx", "http.client", "urllib.error")):
        return True
    if mod.startswith("asf_search"):
        return any(bit in name for bit in _NETWORK_NAME_BITS)
    return False


_TLS = threading.local()          # the job the current worker thread is running (None outside a job)


class _CancelEvent(threading.Event):
    """A cancel flag that also trips whatever the running read registered with `link_cancel` (the retrieval client's own
    cancel_event, say). Without that link a cancelled read kept pulling chunks from NASA to the very end."""

    def __init__(self):
        super().__init__()
        self._linked: list = []
        self._llock = threading.Lock()

    def link(self, ev: threading.Event) -> None:
        with self._llock:
            self._linked.append(ev)
        if self.is_set():
            ev.set()

    def set(self) -> None:
        super().set()
        with self._llock:
            linked = list(self._linked)
        for ev in linked:
            ev.set()


def in_job() -> bool:
    return getattr(_TLS, "job", None) is not None


def current_cancel():
    """The cancel flag of the job this thread runs, or None outside a job."""
    job = getattr(_TLS, "job", None)
    return job.cancel if job is not None else None


def link_cancel(event) -> None:
    """From inside a worker: also set `event` when this job is cancelled. No-op outside a job or for None."""
    job = getattr(_TLS, "job", None)
    if job is not None and event is not None:
        job.cancel.link(event)


def check_cancelled() -> None:
    """Raise Cancelled when the job this thread runs was cancelled, or nobody has asked about it for the idle lease.
    No-op outside a job."""
    job = getattr(_TLS, "job", None)
    if job is None:
        return
    if job.expired():
        job.cancel.set()
    if job.cancel.is_set():
        raise Cancelled("the read was cancelled (nobody was waiting for it any more)")


class _Job:
    def __init__(self, fn, args, kwargs, finalize, cfg):
        self.id = f"bg-{uuid.uuid4().hex[:12]}"
        self.fn, self.args, self.kwargs, self.finalize = fn, args, kwargs, finalize
        self.cfg = cfg
        self.t0 = time.monotonic()
        self.last_seen = self.t0
        self.cancel = _CancelEvent()
        self.attempt = 0
        self.last_error: str | None = None
        self.retry_in: float | None = None
        self.future = None

    def touch(self) -> None:
        self.last_seen = time.monotonic()

    def expired(self) -> bool:
        return time.monotonic() - self.last_seen > self.cfg["job_idle_s"]

    def run(self):
        _TLS.job = self
        try:
            return self._run()
        finally:
            _TLS.job = None

    def _run(self):
        steps = self.cfg["retry_backoff_s"]
        while True:
            if self.cancel.is_set() or self.expired():
                raise Cancelled("the read was cancelled (nobody was waiting for it any more)")
            self.attempt += 1
            try:
                out = self.fn(*self.args, **self.kwargs)
                self.last_error = self.retry_in = None
                return out
            except BaseException as ex:                         # noqa: BLE001 — classified right below
                if not is_transient(ex):
                    raise
                self.last_error = f"{type(ex).__name__}: {ex}"
                self.retry_in = steps[min(self.attempt - 1, len(steps) - 1)]
                if self.cancel.wait(self.retry_in):
                    raise Cancelled("the read was cancelled while waiting to retry") from ex

    def info(self) -> dict:
        return {"waited_s": round(time.monotonic() - self.t0, 1), "attempt": self.attempt,
                "last_error": self.last_error, "retry_in_s": self.retry_in,
                "poll_interval_s": self.cfg["poll_interval_s"], "retry_backoff_s": list(self.cfg["retry_backoff_s"])}


class BackgroundJobs:
    def __init__(self):
        self._lock = threading.Lock()
        self._jobs: dict[str, _Job] = {}

    # ------------------------------------------------------------------ submit / poll / cancel
    def submit(self, pool, fn, *args, finalize=None, wait_s: float | None = None, **kwargs):
        """-> ("done", finalize(result)) when the read finished within `wait_s`, else ("pending", job_id, info).
        A read that raised a definite error raises it here, exactly as a direct call would."""
        cfg = api_config()
        wait = cfg["meta_wait_s"] if wait_s is None else wait_s
        job = _Job(fn, args, kwargs, finalize or (lambda x: x), cfg)
        with self._lock:
            self._evict_locked()
            self._jobs[job.id] = job
        job.future = pool.submit(job.run)
        try:
            out = job.future.result(timeout=wait)
        except FuturesTimeout:
            job.touch()                                         # the answer to THIS request is the 202: the lease starts now
            return ("pending", job.id, job.info())
        except BaseException:
            self._drop(job.id)
            raise
        self._drop(job.id)
        return ("done", job.finalize(out))

    def poll(self, job_id: str):
        """-> ("unknown",) | ("pending", info) | ("done", finalized) | ("error", exception)."""
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            return ("unknown",)
        job.touch()
        if not job.future.done():
            return ("pending", job.info())
        ex = job.future.exception()
        if ex is not None:
            return ("error", ex)
        return ("done", job.finalize(job.future.result()))

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            return False
        job.cancel.set()
        return True

    # ------------------------------------------------------------------ housekeeping
    def _drop(self, job_id: str) -> None:
        with self._lock:
            self._jobs.pop(job_id, None)

    def _evict_locked(self) -> None:
        """Bounded: forget finished jobs nobody collected for the idle lease, then the oldest beyond the cap."""
        for jid in [j for j, job in self._jobs.items() if job.future is not None and job.future.done() and job.expired()]:
            self._jobs.pop(jid, None)
        for jid in sorted(self._jobs, key=lambda j: self._jobs[j].t0)[:-_MAX_JOBS]:
            self._jobs[jid].cancel.set()
            self._jobs.pop(jid, None)

    def __len__(self) -> int:
        with self._lock:
            return len(self._jobs)


JOBS = BackgroundJobs()
