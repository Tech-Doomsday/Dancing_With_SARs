"""Pipeline counts are read live, never typed: declared (manifests) vs modelled (defines run()).
Against the real repo layout: declared == the manifest folders on disk, modelled == the two pipelines
that define run() today. A fixture pipeline that gains a run() must change the count."""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

from engine.registry import Registry

ROOT = Path(__file__).resolve().parents[1]
PIPES = ROOT / "engine" / "pipelines"


def _manifest_folders() -> list[str]:
    return sorted(d.name for d in PIPES.iterdir()
                  if d.is_dir() and not d.name.startswith(("_", ".")) and (d / "manifest.yaml").exists())


def test_real_layout_declared_equals_manifests_and_modelled_is_two():
    reg = Registry()
    c = reg.counts()
    folders = _manifest_folders()
    assert c["declared"] == len(reg.manifests) == len(folders)
    assert "_template" not in reg.names()                       # the template is not a declared pipeline
    assert c["modelled"] == 2
    assert sorted(c["modelled_names"]) == ["earthquake_coseismic", "tsunami_ocean_evidence"]
    assert reg.is_modelled("tropical_cyclone") is False          # featured in the hub, but no run()


def _fixture_pipelines(base: Path, pkg: str) -> Path:
    root = base / pkg
    root.mkdir()
    (root / "__init__.py").write_text("")
    for i, name in enumerate(("alpha", "beta")):
        d = root / name
        d.mkdir()
        (d / "__init__.py").write_text("")
        (d / "manifest.yaml").write_text(f"name: {name}\ncode: 'F{i}'\napplies: fixture\n")
        (d / "verdict.py").write_text("# no run() yet\n")
    (root / "_template").mkdir()
    (root / "_template" / "manifest.yaml").write_text("name: _template\ncode: T\n")
    return root


def test_adding_run_to_a_fixture_pipeline_changes_the_count(tmp_path):
    pkg = f"fixpipes_{abs(hash(str(tmp_path)))}"
    root = _fixture_pipelines(tmp_path, pkg)
    reg = Registry(pipelines_dir=root, package=pkg, sys_path=[str(tmp_path)])
    assert reg.counts()["declared"] == 2                         # _template excluded
    assert reg.counts()["modelled"] == 0
    (root / "alpha" / "verdict.py").write_text(textwrap.dedent("""
        def run(bundle):
            return {}
    """))
    for m in [k for k in sys.modules if k.startswith(pkg)]:      # a fresh import, as a new process would do
        del sys.modules[m]
    c = Registry(pipelines_dir=root, package=pkg, sys_path=[str(tmp_path)]).counts()
    assert (c["declared"], c["modelled"], c["modelled_names"]) == (2, 1, ["alpha"])


def test_no_hardcoded_pipeline_count_in_owned_sources():
    import re
    bad = re.compile(r"\b(16|17|sixteen|seventeen)\s+(independent\s+)?pipelines\b", re.I)
    hits = []
    for base in (ROOT / "app-ui" / "src", ROOT / "engine" / "api", ROOT / "config"):
        for p in base.rglob("*"):
            if not p.is_file() or "speckle" in p.parts or p.suffix not in {".ts", ".tsx", ".py", ".yaml", ".yml"}:
                continue
            for n, line in enumerate(p.read_text(errors="ignore").splitlines(), 1):
                if bad.search(line):
                    hits.append(f"{p.relative_to(ROOT)}:{n}")
    assert not hits, hits
