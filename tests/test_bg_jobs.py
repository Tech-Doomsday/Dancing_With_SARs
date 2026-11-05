"""Background reads have no time limit: a slow read becomes a job the UI polls, a network failure is retried
for ever, a definite answer is final, and a read nobody waits for is cancelled. No FastAPI needed."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from engine.api import bg_jobs
from engine.api.bg_jobs import BackgroundJobs, Cancelled, is_transient

POOL = ThreadPoolExecutor(max_workers=8)


def _always_times_out():
    raise TimeoutError("t")


def _cfg(monkeypatch_target=None, **over):
    base = {"meta_wait_s": 0.05, "job_idle_s": 5.0, "poll_interval_s": 0.01, "retry_backoff_s": [0.01]}
    base.update(over)
    bg_jobs.api_config = lambda: dict(base)               # replaced for the test run; restored by _restore()


_ORIG = bg_jobs.api_config


def _restore():
    bg_jobs.api_config = _ORIG


def _wait_done(jobs, jid, limit=3.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < limit:
        r = jobs.poll(jid)
        if r[0] != "pending":
            return r
        time.sleep(0.01)
    raise AssertionError("job did not finish")


def test_a_fast_read_answers_in_the_same_request():
    _cfg()
    try:
        jobs = BackgroundJobs()
        assert jobs.submit(POOL, lambda: 41 + 1, finalize=lambda x: {"v": x}) == ("done", {"v": 42})
        assert len(jobs) == 0
    finally:
        _restore()


def test_a_slow_read_is_not_cut_off_it_becomes_a_job():
    _cfg(meta_wait_s=0.05)
    try:
        jobs = BackgroundJobs()
        r = jobs.submit(POOL, lambda: (time.sleep(0.4), "late")[1])        # 8x the wait: the old code answered 504 here
        assert r[0] == "pending" and r[1].startswith("bg-")
        assert jobs.poll(r[1])[0] == "pending"
        assert _wait_done(jobs, r[1]) == ("done", "late")
    finally:
        _restore()


def test_network_failures_are_retried_until_they_work():
    _cfg()
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 4:
            raise TimeoutError("ASF search did not answer within 90s")
        return "ok"
    try:
        jobs = BackgroundJobs()
        r = jobs.submit(POOL, flaky)
        out = r if r[0] == "done" else _wait_done(jobs, r[1])
        assert out == ("done", "ok") and len(calls) == 4
    finally:
        _restore()


def test_progress_is_reported_while_retrying():
    _cfg(retry_backoff_s=[0.2])
    stop = threading.Event()

    def never():
        if stop.is_set():
            return "ok"
        raise ConnectionResetError("reset by peer")
    try:
        jobs = BackgroundJobs()
        r = jobs.submit(POOL, never)
        assert r[0] == "pending"
        time.sleep(0.1)
        st, info = jobs.poll(r[1])
        assert st == "pending" and info["attempt"] >= 1 and "ConnectionResetError" in info["last_error"]
        assert info["retry_in_s"] == 0.2
        stop.set()
        assert _wait_done(jobs, r[1]) == ("done", "ok")
    finally:
        _restore()


def test_a_definite_answer_is_final_and_not_retried():
    _cfg()
    calls = []

    class NotFound(Exception):
        status = 404

    def missing():
        calls.append(1)
        raise NotFound("granule not found")
    try:
        jobs = BackgroundJobs()
        try:
            jobs.submit(POOL, missing)
            raise AssertionError("must raise")
        except NotFound:
            pass
        assert len(calls) == 1 and len(jobs) == 0
    finally:
        _restore()


def test_cancel_stops_a_retry_loop():
    _cfg(retry_backoff_s=[30.0])                           # would wait 30 s between attempts
    try:
        jobs = BackgroundJobs()
        r = jobs.submit(POOL, _always_times_out)
        assert r[0] == "pending"
        assert jobs.cancel(r[1]) is True
        st = _wait_done(jobs, r[1])
        assert st[0] == "error" and isinstance(st[1], Cancelled)
    finally:
        _restore()


def test_a_read_nobody_asks_about_is_cancelled_by_the_idle_lease():
    _cfg(meta_wait_s=0.01, job_idle_s=0.2, retry_backoff_s=[0.05])
    bg_jobs.api_config = lambda: {"meta_wait_s": 0.01, "job_idle_s": 0.2, "poll_interval_s": 0.01,
                                  "retry_backoff_s": [0.05]}   # no 2x guard here
    try:
        jobs = BackgroundJobs()
        r = jobs.submit(POOL, _always_times_out)
        assert r[0] == "pending"
        time.sleep(0.6)                                    # nobody polls
        job = jobs._jobs[r[1]]
        assert job.future.done() and isinstance(job.future.exception(), Cancelled)
    finally:
        _restore()


def test_unknown_job_and_classification():
    jobs = BackgroundJobs()
    assert jobs.poll("bg-nope") == ("unknown",) and jobs.cancel("bg-nope") is False
    assert is_transient(TimeoutError()) and is_transient(ConnectionRefusedError())
    assert not is_transient(ValueError("bad")) and not is_transient(KeyError("k")) and not is_transient(Cancelled("c"))
    assert not is_transient(FileNotFoundError("a file"))


def test_no_hard_25_second_cap_is_left_in_the_background_routes():
    src = (Path(bg_jobs.__file__).parent / "routes_background.py").read_text(encoding="utf-8")
    assert "timeout=25" not in src and "did not finish within" not in src
