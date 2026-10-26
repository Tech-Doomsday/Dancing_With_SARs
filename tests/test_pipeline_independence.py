# static check: no module under engine/pipelines/<x>/ imports from engine/pipelines/<y>/
"""Pipelines are independent: each one may use `engine.common` types, `engine.lib` and its own package, nothing else
from `engine.pipelines`. They also must not reach into the chain (P01-P13) - they receive a finished, read-only
SceneBundle. The check is static (AST), so it cannot be fooled by import order or by a module that is never imported.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PIPELINES = ROOT / "engine" / "pipelines"

# engine.common modules a pipeline may import: the bundle types / contract / persistence, never the chain steps
ALLOWED_COMMON = {"scene_bundle", "contract", "frozen", "bundle_io", "layers", "ancillary", "config"}
# `engine.common` itself is allowed (lazy facade); `build_scene_bundle` is the chain's entry point and is forbidden there
FORBIDDEN_COMMON_NAMES = {"build_scene_bundle", "run_steps", "assemble", "STEPS"}


def _pipeline_dirs() -> list:
    return sorted(p for p in PIPELINES.iterdir() if p.is_dir() and not p.name.startswith("__"))


def _imports(path: Path):
    """Yield (lineno, absolute dotted module, imported names) for every import statement, resolving relative imports."""
    try:
        rel = path.relative_to(ROOT)
    except ValueError:                                                         # a scratch file outside the repo
        rel = Path(path.name)
    pkg = ".".join(rel.parts[:-1])                                             # package of the file
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield node.lineno, a.name, ()
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = pkg.split(".")
                base = base[: len(base) - (node.level - 1)]
                mod = ".".join(base + ([node.module] if node.module else []))
            else:
                mod = node.module or ""
            yield node.lineno, mod, tuple(a.name for a in node.names)


def _dynamic_imports(path: Path):
    """importlib.import_module("engine.pipelines.x") / __import__ with a literal string."""
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name in ("import_module", "__import__") and node.args and isinstance(node.args[0], ast.Constant) \
                    and isinstance(node.args[0].value, str):
                yield node.lineno, node.args[0].value


def _files(pipeline_dir: Path):
    return sorted(p for p in pipeline_dir.rglob("*.py") if "__pycache__" not in p.parts)


def test_there_are_pipelines_to_check():
    names = {p.name for p in _pipeline_dirs()}
    assert len(names) >= 16 and {"landslide", "deforestation", "_template"} <= names


@pytest.mark.parametrize("pdir", _pipeline_dirs(), ids=lambda p: p.name)
def test_pipeline_does_not_import_another_pipeline(pdir):
    own = f"engine.pipelines.{pdir.name}"
    bad = []
    for f in _files(pdir):
        for ln, mod, names in list(_imports(f)) + [(l, m, ()) for l, m in _dynamic_imports(f)]:
            if mod == "engine.pipelines" or mod.startswith("engine.pipelines."):
                if mod == "engine.pipelines":
                    others = [n for n in names if n != pdir.name]
                    if others or not names:
                        bad.append(f"{f.relative_to(ROOT)}:{ln} imports {mod} {names}")
                elif not (mod == own or mod.startswith(own + ".")):
                    bad.append(f"{f.relative_to(ROOT)}:{ln} imports {mod}")
    assert not bad, "\n".join(bad)


@pytest.mark.parametrize("pdir", _pipeline_dirs(), ids=lambda p: p.name)
def test_pipeline_does_not_reach_into_the_preprocessing_chain(pdir):
    bad = []
    for f in _files(pdir):
        if "tests" in f.relative_to(pdir).parts:        # a pipeline's own tests may build a bundle to test against
            continue
        for ln, mod, names in _imports(f):
            if mod == "engine.common":
                hit = FORBIDDEN_COMMON_NAMES & set(names)
                if hit:
                    bad.append(f"{f.relative_to(ROOT)}:{ln} imports {sorted(hit)} from engine.common")
            elif mod.startswith("engine.common."):
                leaf = mod.split(".")[2]
                if leaf not in ALLOWED_COMMON:
                    bad.append(f"{f.relative_to(ROOT)}:{ln} imports {mod} (the chain is not a pipeline dependency)")
    assert not bad, "\n".join(bad)


def test_the_checker_itself_detects_a_violation(tmp_path):
    """Guard against a vacuous test: feed the scanner a file that does violate the rule."""
    f = tmp_path / "x.py"
    f.write_text("from engine.pipelines.landslide import model\nimport engine.pipelines.oil_spill.prep\n"
                 "from engine.pipelines import flood_inundation\nimport importlib\nimportlib.import_module('engine.pipelines.urban_damage')\n"
                 "from engine.common.p05_geometry import run\n")
    found = [m for _l, m, _n in _imports(f)]
    assert "engine.pipelines.landslide" in found and "engine.pipelines.oil_spill.prep" in found
    assert "engine.pipelines" in found and "engine.common.p05_geometry" in found
    assert [m for _l, m in _dynamic_imports(f)] == ["engine.pipelines.urban_damage"]


def test_relative_imports_are_resolved(tmp_path):
    root = ROOT / "engine" / "pipelines" / "_template"
    for f in _files(root):
        for _ln, mod, _n in _imports(f):
            assert not mod.startswith("."), (f, mod)
<<<<<<< HEAD


# ---------------------------------------------------------------------------------------------------------------------
# retrieval from a pipeline: measurements come from the bundle; own_fetch context data may only come through adapters
# ---------------------------------------------------------------------------------------------------------------------
RETRIEVAL_ALLOWED = "engine.retrieval.clients.adapters"


def _retrieval_imports(f: Path):
    for ln, mod, names in _imports(f):
        if mod == "engine.retrieval" or mod.startswith("engine.retrieval."):
            yield ln, mod, names
    for ln, mod in _dynamic_imports(f):
        if mod.startswith("engine.retrieval"):
            yield ln, mod, ()


def _retrieval_violations(f: Path) -> list:
    bad = []
    for ln, mod, names in _retrieval_imports(f):
        full = [mod] + [f"{mod}.{n}" for n in names]
        if any(m == RETRIEVAL_ALLOWED or m.startswith(RETRIEVAL_ALLOWED + ".") for m in full):
            continue
        if mod in ("engine.retrieval", "engine.retrieval.clients") and names and all(n in ("adapters",) for n in names):
            continue
        bad.append(f"{f.name}:{ln} imports {mod} {names}")
    return bad


@pytest.mark.parametrize("pdir", _pipeline_dirs(), ids=lambda p: p.name)
def test_pipeline_uses_retrieval_only_through_reference_adapters(pdir):
    bad = []
    for f in _files(pdir):
        if "tests" in f.relative_to(pdir).parts:
            continue
        bad += [f"{f.relative_to(ROOT)}: {b}" for b in _retrieval_violations(f)]
    assert not bad, "\n".join(bad) + "\n(measurements come from the bundle; only engine.retrieval.clients.adapters.* may be imported)"


def test_the_retrieval_checker_itself_detects_a_violation(tmp_path):
    f = tmp_path / "x.py"
    f.write_text("from engine.retrieval.clients.nisar_client import NisarClient\n"
                 "from engine.retrieval.clients.adapters import r1_comcat\n"
                 "import engine.retrieval.clients.adapters.r2_shakemap\n")
    bad = _retrieval_violations(f)
    assert len(bad) == 1 and "nisar_client" in bad[0]
=======
>>>>>>> 8200fa0 (Integrate MOTUS earthquake analysis into Dancing With SARs)
