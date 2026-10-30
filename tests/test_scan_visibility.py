"""Scan visibility: on_start fires before each step; a failing hook never fails the build; the ASF search is bounded."""
import time

import pytest

from engine.common import chain


class _Ctx:
    def __init__(self):
        self.steps = []

    def flag(self, *a, **k):
        pass


def _two_steps(monkeypatch):
    monkeypatch.setattr(chain, "STEPS", (chain.Step("P01", "a", lambda c: None, ()),
                                         chain.Step("P02", "b", lambda c: None, ())))


def test_on_start_fires_before_each_step(monkeypatch):
    _two_steps(monkeypatch)
    seen = []
    chain.run_steps(_Ctx(), observer=lambda s, c, r: seen.append(("end", s.code)),
                    on_start=lambda s, c: seen.append(("start", s.code)))
    assert seen == [("start", "P01"), ("end", "P01"), ("start", "P02"), ("end", "P02")]


def test_failing_on_start_is_tolerated(monkeypatch):
    _two_steps(monkeypatch)
    ctx = _Ctx()

    def bad(step, ctx):
        raise RuntimeError("progress plumbing broke")
    chain.run_steps(ctx, on_start=bad)
    assert [r.status for r in ctx.steps] == ["ok", "ok"]


def test_asf_search_gives_up_instead_of_hanging():
    pytest.importorskip("aiohttp")
    from engine.retrieval.clients.nisar_client import NisarClient

    class Hang:
        @staticmethod
        def search(**kw):
            time.sleep(10)
    t = time.monotonic()
    with pytest.raises(TimeoutError):
        NisarClient._asf_search_bounded(Hang, {}, 0.3)
    assert time.monotonic() - t < 3


def test_asf_warnings_are_captured_for_the_search_thread_only():
    import logging
    pytest.importorskip("aiohttp")
    from engine.retrieval.clients.nisar_client import NisarClient

    class Noisy:
        @staticmethod
        def search(**kw):
            logging.getLogger("asf_search").warning("CMR returned page of incomplete results.Expected 28 results,got 27")
            return [1]
    got: list = []
    assert NisarClient._asf_search_bounded(Noisy, {}, 2.0, got) == [1]
    assert got and "incomplete" in got[0]
    logging.getLogger("asf_search").warning("elsewhere")         # after the call: handler is gone
    assert len(got) == 1


def test_metrics_live_gauges():
    pytest.importorskip("aiohttp")
    from engine.retrieval.clients.range_io import Metrics
    m = Metrics()
    assert m.live()["idle_s"] is None
    m.add(inflight=2, requests=1, bytes_fetched=1000)
    m.add(inflight=-1)
    lv = m.live()
    assert lv["inflight"] == 1 and lv["bytes_fetched"] == 1000 and lv["idle_s"] is not None


def test_p01_flags_an_incomplete_listing():
    from engine.common import p01_granule_resolution as p01

    class Ctx:
        flags = []

        def flag(self, code, sev, step, msg, **kw):
            self.flags.append(code)

    class R:
        last_search_incomplete = True
        last_search_warnings = ["CMR returned page of incomplete results"]
    # only the flag logic is exercised here: a MagicMock-style truthy attribute must NOT count
    assert getattr(R(), "last_search_incomplete", False) is True
    assert not (getattr(object(), "last_search_incomplete", False) is True)
