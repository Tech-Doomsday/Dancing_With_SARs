"""The packaging scripts must never ship secrets: .env / .env.* (NISAR_BACKGROUND_TOKEN, Earthdata)
stay out of every zip; the .env.example templates stay in. Built on a throw-away tree, with NO
.gitignore, so the scripts' own exclusion is what is under test (not git's)."""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


SENTINEL = "SENTINEL-NOT-A-REAL-TOKEN"
FILES = {
    ".env": f"NISAR_BACKGROUND_TOKEN={SENTINEL}\n",
    ".env.local": f"NISAR_BACKGROUND_TOKEN={SENTINEL}\n",
    "app-ui/.env": f"NISAR_BACKGROUND_TOKEN={SENTINEL}\n",
    "app-ui/.env.production": f"NISAR_BACKGROUND_TOKEN={SENTINEL}\n",
    ".env.example": "NISAR_BACKGROUND_TOKEN=\n",
    "app-ui/.env.example": "NISAR_BACKGROUND_TOKEN=\n",
    "README.md": "x\n",
    "engine/a.py": "x = 1\n",
}


def _tree(base: Path) -> Path:
    for rel, txt in FILES.items():
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(txt)
    return base


def _only_examples(paths) -> None:
    names = sorted(str(p).replace("\\", "/") for p in paths)
    envs = [n for n in names if n.rsplit("/", 1)[-1].startswith(".env")]
    assert envs == [".env.example", "app-ui/.env.example"], envs


def test_zip_up_walk_skips_env_files(tmp_path):
    zip_up = _load("zip_up_under_test", ROOT / "dev_scripts" / "zip_up.py")
    tree = _tree(tmp_path / "t")
    found = [p.relative_to(tree).as_posix() for p in zip_up.walk(tree)]
    _only_examples(found)
    assert "README.md" in found and "engine/a.py" in found      # nothing else was lost


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_zip_repo_file_list_skips_env_files(tmp_path):
    zip_repo = _load("zip_repo_under_test", ROOT / "zip_repo.py")
    tree = _tree(tmp_path / "t")
    subprocess.run(["git", "init", "-q"], cwd=tree, check=True)        # no .gitignore on purpose
    files = zip_repo.get_included_files(tree)
    _only_examples(files)
    assert "README.md" in files and "engine/a.py" in files


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_zip_repo_archive_never_contains_the_sentinel(tmp_path):
    import zipfile
    zip_repo = _load("zip_repo_under_test2", ROOT / "zip_repo.py")
    tree = _tree(tmp_path / "t")
    subprocess.run(["git", "init", "-q"], cwd=tree, check=True)
    out = tmp_path / "out.zip"
    zip_repo.zip_repo(tree, out)
    with zipfile.ZipFile(out) as z:
        assert not [n for n in z.namelist() if SENTINEL.encode() in z.read(n)]


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_root_gitignore_ignores_env_but_not_examples(tmp_path):
    tree = _tree(tmp_path / "t")
    shutil.copy(ROOT / ".gitignore", tree / ".gitignore")
    subprocess.run(["git", "init", "-q"], cwd=tree, check=True)
    out = subprocess.run(["git", "ls-files", "-z", "--others", "--exclude-standard"], cwd=tree,
                         capture_output=True, check=True).stdout.decode().split("\0")
    _only_examples([f for f in out if f])
