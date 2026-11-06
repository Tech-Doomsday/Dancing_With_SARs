"""A preview behind a running one must WAIT its turn, not be refused.

Regression (screenshots 2026-11-06): the page sat on "drawing... 49 s ... the engine asked for a short pause" and kept
showing the previous layer. The engine answered 409 "a preview is already running"; the browser re-sent the request
every few seconds and every re-send used one of the 12 previews per minute, so the budget ran out (429) while the
read in front of it was still going. Also: a cancelled read kept pulling chunks from NASA and held the lock."""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.test_background_api import (  # noqa: E402,F401  (fixtures + helpers reused as-is)
    GCOV_A, NORTH, SOUTH, EAST, WEST, FakeSource, _auth, client, data_dir, default_specs)
from engine.api import background as bg  # noqa: E402
from engine.api import bg_jobs  # noqa: E402
from engine.api.bg_jobs import BackgroundJobs, Cancelled, _CancelEvent  # noqa: E402

LAYER = "science__LSAR__GCOV__grids__frequencyA__HHHH"
POOL = None


def _pool():
    from concurrent.futures import ThreadPoolExecutor
    global POOL
    POOL = POOL or ThreadPoolExecutor(max_workers=6)
    return POOL


@pytest.fixture()
def fast_jobs(monkeypatch):
    cfg = {"meta_wait_s": 0.05, "preview_wait_s": 0.05, "job_idle_s": 30.0, "poll_interval_s": 0.01, "retry_backoff_s": [0.01]}
    monkeypatch.setattr(bg_jobs, "api_config", lambda: dict(cfg))
    monkeypatch.setattr(bg, "_PREVIEW_QUEUE", [])
    yield


def _preview():
    return bg.preview(FakeSource(default_specs()), gid=GCOV_A, layer=LAYER, bbox=(WEST, SOUTH, EAST, NORTH),
                      max_px=32, cmap="viridis", use_cache=False)


def _poll(jobs, jid, limit=8.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < limit:
        r = jobs.poll(jid)
        if r[0] != "pending":
            return r
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def test_a_queued_preview_waits_for_the_running_one_instead_of_409(client, fast_jobs):
    jobs = BackgroundJobs()
    bg._PREVIEW_LOCK.acquire()                                   # another preview is reading from NASA
    try:
        res = jobs.submit(_pool(), _preview, finalize=lambda o: o["meta"]["layer"])
        assert res[0] == "pending"                               # answered 202 + job id, NOT a 409 error
        time.sleep(0.3)
        assert jobs.poll(res[1])[0] == "pending"                 # still waiting its turn
    finally:
        bg._PREVIEW_LOCK.release()                               # the read in front of it finishes
    out = _poll(jobs, res[1])
    assert out[0] == "done" and out[1] == LAYER                  # ...and then it runs, by itself


def test_waiting_in_the_queue_costs_no_preview_budget(client, fast_jobs):
    """Polling a queued job must not touch the 12-per-minute window: only the first submit counts."""
    from engine.api import routes_background as rb
    c, _t, _b = client
    rb._PREVIEW_HITS.clear()
    bg._PREVIEW_LOCK.acquire()
    try:
        r = c.get(f"/api/background/preview?id={GCOV_A}&layer={LAYER}&west={WEST}&south={SOUTH}&east={EAST}"
                  f"&north={NORTH}&max_px=32", headers=_auth())
        assert r.status_code == 202, r.text
        jid = r.json()["job_id"]
        for _ in range(40):                                      # the browser polls ~1x per second while it waits
            assert c.get(f"/api/background/jobs/{jid}", headers=_auth()).status_code == 202
        assert len(rb._PREVIEW_HITS) == 1                        # was: one per re-send, 12 per minute -> 429
    finally:
        bg._PREVIEW_LOCK.release()
    for _ in range(200):
        r = c.get(f"/api/background/jobs/{jid}", headers=_auth())
        if r.status_code != 202:
            break
        time.sleep(0.02)
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"


def test_a_cancelled_waiter_leaves_the_line_and_never_takes_the_lock(client, fast_jobs):
    jobs = BackgroundJobs()
    bg._PREVIEW_LOCK.acquire()
    try:
        res = jobs.submit(_pool(), _preview)
        assert res[0] == "pending"
        assert jobs.cancel(res[1]) is True                       # the person picked another layer
        out = _poll(jobs, res[1])
        assert out[0] == "error" and isinstance(out[1], Cancelled)
        assert bg._PREVIEW_QUEUE == []                           # it left the line
        assert bg._PREVIEW_LOCK.locked()                         # and did not steal or release the lock
    finally:
        bg._PREVIEW_LOCK.release()


def test_a_third_waiter_supersedes_the_oldest(client, fast_jobs):
    jobs = BackgroundJobs()
    bg._PREVIEW_LOCK.acquire()
    try:
        ids = [jobs.submit(_pool(), _preview)[1] for _ in range(3)]
        out = _poll(jobs, ids[0])                                # the oldest was cancelled by the newest
        assert out[0] == "error" and isinstance(out[1], Cancelled)
        assert jobs.poll(ids[1])[0] == "pending" and jobs.poll(ids[2])[0] == "pending"
    finally:
        bg._PREVIEW_LOCK.release()
    assert _poll(jobs, ids[1])[0] == "done" and _poll(jobs, ids[2])[0] == "done"


def test_direct_calls_keep_the_409(client):
    bg._PREVIEW_LOCK.acquire()
    try:
        with pytest.raises(bg.BackgroundError) as ei:
            _preview()
        assert ei.value.status == 409
    finally:
        bg._PREVIEW_LOCK.release()


def test_cancelling_a_job_trips_the_flag_of_its_running_read(fast_jobs):
    """link_cancel: the retrieval client's cancel_event is set when the job is cancelled (it stops between chunks)."""
    jobs = BackgroundJobs()
    read_flag, started, release = threading.Event(), threading.Event(), threading.Event()

    def worker():
        bg_jobs.link_cancel(read_flag)
        started.set()
        release.wait(5)
        return "done"

    res = jobs.submit(_pool(), worker)
    assert started.wait(2) and res[0] == "pending"
    assert not read_flag.is_set()
    jobs.cancel(res[1])
    assert read_flag.is_set()                                    # was: nothing connected the two, the read ran on
    release.set()


def test_link_after_cancel_trips_immediately_and_outside_a_job_is_a_noop():
    ev = _CancelEvent()
    ev.set()
    late = threading.Event()
    ev.link(late)
    assert late.is_set()
    bg_jobs.link_cancel(threading.Event())                       # no job on this thread: must not raise
    bg_jobs.check_cancelled()
    assert bg_jobs.current_cancel() is None and not bg_jobs.in_job()


def test_a_preview_hands_out_its_job_id_quickly_so_leaving_it_can_cancel_it(monkeypatch):
    """The page can cancel only a read it knows the id of; previews therefore use the short `preview_wait_s`."""
    cfg = bg_jobs.api_config()
    assert cfg["preview_wait_s"] <= 5 < cfg["meta_wait_s"]
    from engine.retrieval.settings import load_settings
    assert float((load_settings().api or {}).get("preview_wait_s", 0)) == cfg["preview_wait_s"]   # set in config/retrieval.yaml
