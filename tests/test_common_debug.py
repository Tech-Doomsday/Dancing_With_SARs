"""P05's look-side check, the step tracer, the HTML report, and (when the sample is on disk) a real GUNW.

The real-data tests need dancing-with-sars-eq next to this repo (or EQ_SAMPLE_DIR); they skip otherwise.
"""
import dataclasses
import json
import re
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from common_fixtures import FakeRetriever, GeomSpec, default_tile, make_scene
from engine.common import build_scene_bundle
from engine.common.config import load_common_config
from engine.common.debug.report import report_data, render, write_report
from engine.common.debug.trace import traced_build

AS_OF = "2026-09-20T00:00:00Z"
TILE = default_tile()


def _cfg(**radar):
    c = load_common_config()
    return dataclasses.replace(c, radar=dataclasses.replace(c.radar, **radar))


def _build(cfg=None, geom=None):
    r = FakeRetriever(make_scene(TILE, pairs=((0, 1), (1, 2), (4, 5)), geom=geom))
    return build_scene_bundle(TILE, r, as_of=AS_OF, cfg=cfg), r


def _side(b):
    return next(f for f in b.flags if f.code == "look_side_implied")


# ---------------------------------------------------------------------------------------------
# P05: the sign of the LOS vector is tested, not just its length
# ---------------------------------------------------------------------------------------------
def test_look_side_is_always_reported():
    b, _ = _build()
    f = _side(b)
    assert f.severity == "info" and f.detail["side"] == "left"      # GeomSpec default: left-looking, ground->sensor
    assert f.detail["expected"] is None and not any(x.code == "los_convention_suspect" for x in b.flags)


def test_matching_look_side_raises_nothing():
    b, _ = _build(_cfg(look_side="left"))
    assert not any(x.code == "los_convention_suspect" for x in b.flags)


def test_wrong_look_side_is_an_error():
    b, _ = _build(_cfg(look_side="right"))
    bad = [x for x in b.flags if x.code == "los_convention_suspect"]
    assert bad and bad[0].severity == "error" and "look_side" in bad[0].message


def test_flipped_vector_convention_changes_the_implied_side_while_the_magnitude_check_stays_blind():
    """Same data read as sensor->ground: |x|,|y| (and so consistency_deg) cannot change, the implied side must."""
    b0, _ = _build()
    b1, _ = _build(_cfg(los_vector_points="sensor_to_ground"))
    assert _side(b0).detail["side"] != _side(b1).detail["side"]
    assert b0.stack().geometry.consistency_deg == pytest.approx(b1.stack().geometry.consistency_deg, abs=1e-4)


# ---------------------------------------------------------------------------------------------
# tracer: observes, never changes
# ---------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def traced():
    r = FakeRetriever(make_scene(TILE, pairs=((0, 1), (1, 2), (4, 5))))
    return traced_build(TILE, r, as_of=AS_OF), r


def test_tracing_does_not_change_the_bundle(traced):
    t, _ = traced
    plain, _ = _build()
    assert t.bundle.bundle_id == plain.bundle_id and t.bundle.status == plain.status
    assert [s.code for s in t.steps] == [f"P{i:02d}" for i in range(1, 14)]
    assert t.tracer_errors == []


def test_tracer_sees_arrays_appear_step_by_step(traced):
    t, _ = traced
    first = {a.first_step for a in t.arrays.values()}
    assert {"P02", "P03", "P04", "P05", "P06"} <= first
    los = next(a for a in t.arrays.values() if a.path.startswith("ifg/") and a.label.endswith("los_m/[0]"))
    assert los.step == "P06" and los.extent is not None and los.stats["valid"] > 0.5
    cc = next(a for a in t.arrays.values() if a.group == "clips" and a.label.endswith("connectedComponents"))
    assert cc.kind == "int" and cc.fill == 65535 and 0 in cc.categories      # the fake has a bad patch of component 0


def test_a_broken_observer_cannot_break_the_build():
    from engine.common.debug.trace import Tracer
    tr = Tracer()
    tr._observe = lambda *a: 1 / 0
    r = FakeRetriever(make_scene(TILE, pairs=((0, 1),)))
    b = build_scene_bundle(TILE, r, as_of=AS_OF, observer=tr)
    assert b.status in ("ok", "partial") and len(tr.trace.tracer_errors) == 13


# ---------------------------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------------------------
def test_report_is_self_contained_and_round_trips(traced, tmp_path):
    t, r = traced
    p = write_report(tmp_path / "r.html", t, reality=[{"topic": "x</script>", "engine": "a", "real": "b", "source": "s",
                                                       "verdict": "info", "note": ""}])
    html = p.read_text(encoding="utf-8")
    assert "http://" not in html.replace("http://www.w3.org", "") and "https://" not in html      # no network at all
    blob = re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.S).group(1)
    d = json.loads(blob.replace("<\\/", "</"))
    assert d["reality"][0]["topic"] == "x</script>"                 # '</script>' in data must not close the tag
    assert len(d["arrays"]) == len(t.arrays) and d["bundle"]["status"] == t.bundle.status
    a = d["arrays"][0]
    assert len(__import__("base64").b64decode(a["b64"])) == 4 * a["w"] * a["h"]
    assert [s["code"] for s in d["steps"]][0] == "P01" and d["lineage"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_report_javascript_parses(traced, tmp_path):
    t, _ = traced
    html = render(report_data(t))
    js = html.split("</script>\n<script>")[1].rsplit("</script>", 1)[0] if "</script>\n<script>" in html else \
        re.findall(r"<script>(.*?)</script>", html, re.S)[-1]
    f = tmp_path / "r.js"
    f.write_text(js, encoding="utf-8")
    out = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr


# ---------------------------------------------------------------------------------------------
# a real product, when it is on disk
# ---------------------------------------------------------------------------------------------
def _sample_dir():
    import os
    d = Path(os.environ.get("EQ_SAMPLE_DIR", Path(__file__).resolve().parents[2] / "dancing-with-sars-eq" / "data" / "derived" / "nisar-la-2008"))
    return d if (d / "meta.json").is_file() and (d / "phase.f32").is_file() else None


real = pytest.mark.skipif(_sample_dir() is None, reason="dancing-with-sars-eq sample not found (set EQ_SAMPLE_DIR)")


@pytest.fixture(scope="module")
def real_run():
    from engine.common.debug.eq_sample import EqSampleRetriever
    r = EqSampleRetriever(_sample_dir())
    return r, traced_build(r.best_tile(mode="mixed"), r, as_of=r.as_of, cfg=r.config())


@real
def test_real_product_runs_all_thirteen_steps(real_run):
    r, t = real_run
    assert t.bundle.status == "ok" and t.tracer_errors == []
    assert {s.code: s.status for s in t.steps}["P06"] == "ok"
    p = t.bundle.stack().interferometric.pairs[0]
    assert (p.ref_time, p.sec_time) == ("2008-10-12T06:09:11Z", "2008-11-27T06:10:00Z")
    assert 45.9 < p.dt_days < 46.1


@real
def test_real_product_gate_removes_fill_and_component_zero(real_run):
    r, t = real_run
    raw = next(a for a in t.arrays.values() if a.group == "clips" and a.label.endswith("unwrappedPhase"))
    los = next(a for a in t.arrays.values() if a.path.startswith("ifg/") and a.label.endswith("los_m/[0]"))
    cc = next(a for a in t.arrays.values() if a.group == "clips" and a.label.endswith("connectedComponents"))
    assert set(cc.categories) <= {0, 1, 65535} and 65535 in cc.categories and 0 in cc.categories
    assert los.stats["valid"] < raw.stats["valid"] < 1.0           # NaN fill AND component 0 both cost pixels


@real
def test_real_product_agrees_with_the_look_side_and_a_flipped_convention_is_caught(real_run):
    r, t = real_run
    assert _side(t.bundle).detail["side"] == "right"
    assert not any(f.code == "los_convention_suspect" for f in t.bundle.flags)
    flipped = dataclasses.replace(r.config(), radar=dataclasses.replace(r.config().radar, los_vector_points="sensor_to_ground"))
    b = build_scene_bundle(r.best_tile(mode="mixed"), r, as_of=r.as_of, cfg=flipped)
    assert any(f.code == "los_convention_suspect" and f.severity == "error" for f in b.flags)


@real
def test_real_product_reality_check_has_no_surprises(real_run):
    from engine.common.debug.compare import reality_check
    r, t = real_run
    rows = {x["topic"]: x["verdict"] for x in reality_check(r, t.bundle)}
    assert rows["wavelength"] == "differs" and rows["effective looks (80 m pixel)"] == "differs"   # defaults are not this product's
    assert rows["LOS vector direction (losUnitVectorX/Y)"] == "confirmed"
    assert rows["look side implied by P05"] == "confirmed"
    assert rows["connectedComponents fill"] == "confirmed" and rows["pair dates parsed from the granule id (P01)"] == "confirmed"
