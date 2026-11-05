"""Each preprocessing step must show the exact images it received and the exact images it wrote.

Regression: build_trace_payload built every step's `arrays` from an empty dict, so the Background page said
"wrote 0 arrays" for all of P01..P13 even when the tracer had recorded hundreds. There was also no input side."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.test_background_api import AS_OF, _auth, client, data_dir  # noqa: E402,F401  (fixtures + helpers)


def _traced(client):
    from engine.api import background as bg
    from engine.api.state import get_runner
    from engine.common.chain import build_scene_bundle
    from engine.common.debug.trace import Tracer
    from tests.common_fixtures import FakeRetriever as FR, default_tile, make_scene as ms
    tile, tracer = default_tile(), Tracer()
    bundle = build_scene_bundle(tile, FR(ms(tile, n_epochs=3, pairs=((0, 1),))), as_of=AS_OF, observer=tracer)
    runner = get_runner()
    runner._states["run-io"] = {"status": "ok", "progress": {}, "error": None, "results": [], "kind": "scan"}
    runner.attach_trace("run-io", bg.build_trace_payload(tracer, bundle))
    return tracer, runner.get_trace("run-io")


def test_every_step_lists_what_the_tracer_recorded(client):
    tracer, trace = _traced(client)
    for snap in tracer.trace.steps:
        step = next(s for s in trace["steps"] if s["code"] == snap.code)
        assert len(step["arrays"]) == len(snap.arrays), snap.code          # was always 0
    assert sum(len(s["arrays"]) for s in trace["steps"]) > 100


def test_inputs_are_the_declared_groups_as_they_stood_before_the_step(client):
    tracer, trace = _traced(client)
    by = {s["code"]: s for s in trace["steps"]}
    p02_out = {a["path"] for a in by["P02"]["arrays"]}
    p03_in = {a["path"] for a in by["P03"]["inputs"]}
    assert p03_in and p03_in == {p for p in p02_out if p.split("/")[0] == "clips"}      # P03 needs `clips`
    assert by["P01"]["inputs"] == [] and by["P01"]["arrays"] == []                       # nothing array-shaped
    for s in trace["steps"]:                                                             # inputs only from declared groups
        groups = set(s["reads"])
        for a in s["inputs"]:
            assert a["group"] in groups or any(o["path"] == a["path"] for o in s["arrays"]), (s["code"], a["path"])


def test_a_changed_array_keeps_its_exact_before_and_after():
    """Controlled context: step P07 rewrites an array that P06 produced. The record of P07 must hold BOTH versions."""
    from types import SimpleNamespace as NS
    import numpy as np
    from engine.common.debug.trace import Tracer
    ctx = NS(clips=None, decoded=None, harm_pairs=None, harm_epochs=None, harm_offsets=None, harm_geometry=None,
             geometry=None, ifg={"unw": np.arange(64, dtype="f4").reshape(8, 8)}, cov=None, stable=None,
             ancillary=None, _terrain=None, flags=[], assumptions=[],
             grids=NS(g80=NS(shape=(1, 1)), g20=NS(shape=(1, 1))))
    rec = lambda code: NS(code=code, name="x", status="ok", seconds=0.0, message="")
    tr = Tracer()
    tr(NS(code="P06", needs=("harm_pairs",)), ctx, rec("P06"))
    first = tr.trace.arrays["ifg/unw"]
    ctx.ifg["unw"] = ctx.ifg["unw"] * 2 + 1                       # P07 changes it in place
    tr(NS(code="P07", needs=("ifg",)), ctx, rec("P07"))
    ins, outs = tr.trace.step_io["P07"]
    assert len(outs) == 1 and outs[0] is tr.trace.arrays["ifg/unw"] and outs[0] is not first
    assert len(ins) == 1 and ins[0] is first                      # the version P07 RECEIVED, not the one it wrote
    assert float(np.nanmax(ins[0].thumb)) == 63.0 and float(np.nanmax(outs[0].thumb)) == 127.0
    assert tr.trace.step_io["P06"] == ([], [first])               # nothing before P06; it wrote the first version


def test_an_unchanged_array_is_an_input_but_not_an_output():
    from types import SimpleNamespace as NS
    import numpy as np
    from engine.common.debug.trace import Tracer
    ctx = NS(clips=None, decoded=None, harm_pairs=None, harm_epochs=None, harm_offsets=None, harm_geometry=None,
             geometry=None, ifg={"unw": np.ones((8, 8), "f4")}, cov=None, stable=None, ancillary=None, _terrain=None,
             flags=[], assumptions=[], grids=NS(g80=NS(shape=(1, 1)), g20=NS(shape=(1, 1))))
    rec = lambda code: NS(code=code, name="x", status="ok", seconds=0.0, message="")
    tr = Tracer()
    tr(NS(code="P06", needs=()), ctx, rec("P06"))
    tr(NS(code="P10", needs=("ifg",)), ctx, rec("P10"))           # reads ifg, changes nothing
    ins, outs = tr.trace.step_io["P10"]
    assert [r.path for r in ins] == ["ifg/unw"] and outs == []


def test_every_listed_image_is_served(client):
    c, _t, _b = client
    _tracer, trace = _traced(c)
    seen = 0
    for s in trace["steps"]:
        for a in (s["inputs"][:2] + s["arrays"][:2]):
            r = c.get(f"/api/background/runs/run-io/arrays/{a['aid']}", headers=_auth())
            assert r.status_code in (200, 422), (s["code"], a["aid"], r.status_code)     # 422 = no valid pixels, stated
            if r.status_code == 200:
                assert r.content[:8] == b"\x89PNG\r\n\x1a\n"
                seen += 1
    assert seen >= 10
