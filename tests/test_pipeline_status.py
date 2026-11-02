"""B11 / A12: the pipeline counts in the docs, explain.yaml and the registry agree, and the truth about what is built is
checked, not asserted by hand. Also: what routing does to a volcano tile when its inputs are missing."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_explain_yaml_manifests_and_registry_agree_on_the_number_of_pipelines():
    from engine.registry import Registry
    reg = Registry()
    explain = yaml.safe_load((ROOT / "config" / "explain.yaml").read_text())["pipelines"]
    dirs = [d.name for d in (ROOT / "engine" / "pipelines").iterdir() if d.is_dir() and not d.name.startswith(("_", "__"))]
    assert len(reg.manifests) == len(explain) == len(dirs) == 17


def test_the_readiness_doc_states_the_real_counts():
    from engine.registry import Registry
    reg = Registry()
    built = sorted(m.name for m in reg.manifests if reg.is_modelled(m.name))
    assert built == ["earthquake_coseismic", "tsunami_ocean_evidence"]                 # A12: 2 of 17
    text = (ROOT / "docs" / "READINESS.md").read_text()
    assert f"**{len(reg.manifests)}** pipeline manifests" in text
    assert f"**{len(built)} of {len(reg.manifests)} define `run()`**" in text
    assert f"The other **{len(reg.manifests) - len(built)} return `NOT_MODELLED`**" in text
    for name in built:
        assert name not in re.search(r"The other \*\*15 return.*?\n> `volcanic", text, re.S).group(0)


def test_volcanic_deformation_cannot_give_a_verdict_and_says_so():
    from engine.registry import Registry
    reg = Registry()
    assert reg.is_modelled("volcanic_deformation") is False
    src = (ROOT / "engine" / "pipelines" / "volcanic_deformation" / "verdict.py").read_text()
    assert "def run" not in src                                                          # the stub is comments only


def test_routing_without_ancillary_layers_leaves_eight_pipelines_unknown_and_unlaunched(tmp_path):
    """A12: P12 really does block pipelines when the DEM / land cover / volcano-distance layers are missing."""
    import pytest
    pytest.importorskip("pyproj")
    from engine.common import DirectoryAncillary, build_scene_bundle
    from engine.common.config import load_common_config
    from tests.common_fixtures import FakeRetriever, default_tile, dt, make_scene, write_ancillary
    tile = default_tile()
    specs = make_scene(tile, n_epochs=4, pairs=((0, 1), (1, 2), (2, 3)), event=False)
    bare = build_scene_bundle(tile, FakeRetriever(specs), as_of=dt("2026-08-10")).routing
    full = build_scene_bundle(tile, FakeRetriever(specs), as_of=dt("2026-08-10"),
                              ancillary=DirectoryAncillary(write_ancillary(tmp_path, tile))).routing
    blocked = {"deforestation", "glacier_ice_flow", "landslide", "permafrost_thaw", "river_erosion", "urban_damage",
               "volcanic_deformation", "wildfire_burn_scar"}
    assert set(bare.unknown) == blocked and "volcanic_deformation" not in bare.applicable
    assert "volcanic_deformation" in full.applicable and not full.unknown
    assert load_common_config().registry.run_unknown is False               # unknown => reported, not launched
