"""config/explain.yaml + engine/api/explain.py: copy is complete, short, honest; live facts are never stored."""
from __future__ import annotations

import pytest
import yaml

from engine.api.explain import (ExplainConfigError, explain_payload, layer_explanation, load_explain_config)
from engine.common.chain import STEPS
from engine.common.layers import ROLES


def test_every_step_present_in_chain_order_with_short_plain_text():
    p = explain_payload()
    assert [r["code"] for r in p["steps"]] == [s.code for s in STEPS]  # names/order come from chain.STEPS
    assert [r["name"] for r in p["steps"]] == [s.name for s in STEPS]
    assert [r["code"] for r in p["steps"]] == [f"P{i:02d}" for i in range(1, 14)]
    for row in p["steps"]:
        assert 0 < len(row["plain"].split()) <= 25, row["code"]
        assert row["technical"], row["code"]


def test_every_declared_pipeline_has_copy_and_a_live_modelled_flag():
    p = explain_payload()
    assert set(p["pipelines"]) == {m.name for m in __import__("engine.registry", fromlist=["Registry"]).Registry().manifests}
    assert all(v["plain"] for v in p["pipelines"].values())
    assert sorted(n for n, v in p["pipelines"].items() if v["modelled"]) == ["earthquake_coseismic", "tsunami_ocean_evidence"]
    assert p["counts"]["modelled"] == 2
    raw = open(__import__("engine.api.explain", fromlist=["x"]).explain_config_path()).read()
    assert "modelled" not in yaml.safe_load(raw)["pipelines"]         # never stored in YAML
    for v in p["pipelines"].values():                                  # none claims a working detector
        assert not any(w in v["plain"].lower() for w in ("detects", "detector", "alerts"))


def test_layer_copy_covers_every_role_and_unknown_is_null():
    cfg = load_explain_config()
    assert set(ROLES) <= set(cfg["layers"])
    got = layer_explanation("science__LSAR__GCOV__grids__frequencyA__HHHH")
    assert got["role"] == "cov" and got["what_it_is"] and "decibel" in got["how_to_read"]
    none = layer_explanation("something__unknown")
    assert none == {"role": None, "kind": None, "what_it_is": None, "unit": None, "how_to_read": None}
    assert layer_explanation("science__LSAR__GCOV__grids__frequencyA__numberOfLooks")["unit"] is None  # unknown unit stays null
    assert layer_explanation("something__unknown")["kind"] is None


def test_no_hazard_conclusions_in_layer_copy():
    cfg = load_explain_config()
    for role, row in cfg["layers"].items():
        text = " ".join(str(v) for v in row.values() if v).lower()
        assert not any(w in text for w in ("hazard", "danger", "risk", "earthquake", "damage")), role


def test_missing_or_bad_config_is_a_clear_error(tmp_path):
    with pytest.raises(ExplainConfigError, match="not found"):
        load_explain_config(env={"NISAR_CONFIG_DIR": str(tmp_path)})
    (tmp_path / "explain.yaml").write_text("steps: 1\n")
    with pytest.raises(ExplainConfigError, match="must have mappings"):
        load_explain_config(env={"NISAR_CONFIG_DIR": str(tmp_path)})


def test_overlong_or_missing_step_copy_is_rejected():
    cfg = load_explain_config()
    cfg["steps"]["P01"]["plain"] = "word " * 26
    with pytest.raises(ExplainConfigError, match="over 25 words"):
        explain_payload(cfg=cfg)
    cfg = load_explain_config()
    del cfg["steps"]["P05"]
    with pytest.raises(ExplainConfigError, match="P05"):
        explain_payload(cfg=cfg)



# ------------------------------------------------------------------ added in the unified version
def test_pipeline_rows_carry_the_manifest_applies_text_and_live_flag():
    class Reg:
        flag = False
        def describe(self): return [{"name": "land_subsidence", "code": "9.99", "applies": "land only"}]
        def is_modelled(self, name): return self.flag
        def counts(self): return {"declared": 1, "modelled": int(self.flag), "modelled_names": []}
    reg = Reg()
    row = explain_payload(registry=reg)["pipelines"]["land_subsidence"]
    assert row["applies"] == "land only" and row["modelled"] is False
    reg.flag = True
    assert explain_payload(registry=reg)["pipelines"]["land_subsidence"]["modelled"] is True


def test_overview_nodes_are_served_and_short_and_name_no_pipeline_count():
    import re
    from pathlib import Path
    from engine.api.explain import explain_config_path
    p = explain_payload()
    assert [k for k in p["nodes"]][:3] == ["find", "plan", "download"]
    for key, node in p["nodes"].items():
        assert len(node["plain"].split()) <= 25, key
    assert not re.search(r"\b(16|17|sixteen|seventeen)\s+pipelines", Path(explain_config_path()).read_text().lower())


def test_config_dir_env_selects_the_file_and_a_missing_nodes_section_is_rejected(tmp_path):
    (tmp_path / "explain.yaml").write_text("steps: {}\nlayers: {}\npipelines: {}\n")
    with pytest.raises(ExplainConfigError, match="nodes"):
        load_explain_config(env={"NISAR_CONFIG_DIR": str(tmp_path)})
