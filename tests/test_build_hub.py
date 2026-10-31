"""colab/build_hub.py: the one-file Colab module. It must be slim, never carry a secret unless asked, unpack by itself,
and what it unpacks must be enough to run the hub app (no tests/, docs/, app-ui/ needed)."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import textwrap
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("build_hub_under_test", ROOT / "colab" / "build_hub.py")
bh = importlib.util.module_from_spec(_spec)
sys.modules["build_hub_under_test"] = bh
_spec.loader.exec_module(bh)

SENTINEL = "SENTINEL-NOT-A-REAL-TOKEN"


def _names(pyz):
    with zipfile.ZipFile(pyz) as z:
        return z.namelist()


def test_the_real_repo_builds_slim_and_complete(tmp_path):
    out = bh.build(tmp_path / "h.pyz")
    names = set(_names(out))
    for must in ("__main__.py", "BUILD_ID", "engine/api/main.py", "engine/tiling/mgrs.py", "config/tiling.yaml",
                 "config/retrieval.yaml", "colab/hub.py", "colab/hub_page.html", "colab/nisar_colab.py", "pyproject.toml",
                 "requirements-engine.txt", "requirements-retrieval.txt"):
        assert must in names, must
    top = {n.split("/")[0] for n in names}
    assert top <= {"__main__.py", "BUILD_ID", "engine", "config", "colab", "tools", "pyproject.toml",
                   "requirements-engine.txt", "requirements-retrieval.txt"}, top
    assert not [n for n in names if "/tests/" in n or "__pycache__" in n or n.endswith(".pyc")]
    assert not [n for n in names if n.startswith("tools/") and not n.endswith(".py")]      # no UI assets
    assert not [n for n in names if os.path.basename(n).startswith(".env")]
    assert out.stat().st_size < 4_000_000


def _fake_root(base: Path) -> Path:
    for rel, txt in {".env": f"EARTHDATA_TOKEN={SENTINEL}\n", ".env.production": f"X={SENTINEL}\n", ".env.example": "EARTHDATA_TOKEN=\n",
                     "engine/a.py": "x = 1\n", "engine/.env": f"X={SENTINEL}\n", "engine/tests/t.py": "x\n", "config/c.yaml": "a: 1\n",
                     "colab/nisar_colab.py": "", "colab/hub.py": "", "colab/hub_page.html": "", "pyproject.toml": "",
                     "requirements-engine.txt": "", "requirements-retrieval.txt": ""}.items():
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(txt)
    return base


def test_no_secret_by_default_and_only_the_root_env_when_asked(tmp_path):
    root = _fake_root(tmp_path / "r")
    plain = bh.build(tmp_path / "plain.pyz", root=root)
    with zipfile.ZipFile(plain) as z:
        assert not [n for n in z.namelist() if SENTINEL.encode() in z.read(n)]
        assert ".env" not in z.namelist()
    keyed = bh.build(tmp_path / "keyed.pyz", root=root, include_env=True)
    with zipfile.ZipFile(keyed) as z:
        leaking = [n for n in z.namelist() if SENTINEL.encode() in z.read(n)]
    assert leaking == [".env"]                                                   # not .env.production, not engine/.env


def test_include_env_without_a_file_warns_and_packs_nothing(tmp_path, capsys):
    root = _fake_root(tmp_path / "r")
    (root / ".env").unlink()
    out = bh.build(tmp_path / "x.pyz", root=root, include_env=True)
    assert ".env" not in _names(out) and "no .env" in capsys.readouterr().out


def test_a_missing_required_file_stops_the_build(tmp_path):
    root = _fake_root(tmp_path / "r")
    (root / "colab" / "hub_page.html").unlink()
    with pytest.raises(FileNotFoundError, match="hub_page.html"):
        bh.build(tmp_path / "x.pyz", root=root)


def test_build_id_follows_the_content(tmp_path):
    root = _fake_root(tmp_path / "r")
    a = bh.build(tmp_path / "a.pyz", root=root)
    b = bh.build(tmp_path / "b.pyz", root=root)
    (root / "engine" / "a.py").write_text("x = 2\n")
    c = bh.build(tmp_path / "c.pyz", root=root)
    ids = [zipfile.ZipFile(p).read("BUILD_ID") for p in (a, b, c)]
    assert ids[0] == ids[1] != ids[2]


def test_wheels_option_packs_a_wheelhouse(tmp_path, monkeypatch):
    root = _fake_root(tmp_path / "r")

    def fake(dest, root_, py):
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "numpy-9.9-cp312-manylinux.whl").write_bytes(b"whl")
        assert py == "3.12"
        return 1
    monkeypatch.setattr(bh, "download_wheels", fake)
    assert "wheelhouse/numpy-9.9-cp312-manylinux.whl" in _names(bh.build(tmp_path / "w.pyz", root=root, wheels=True))


def test_the_wheelhouse_is_used_by_the_colab_installer(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("nc_under_test", ROOT / "colab" / "nisar_colab.py")
    nc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(nc)
    seen = []
    monkeypatch.setattr(nc.subprocess, "run", lambda cmd, check: seen.append(cmd))
    nc.install(tmp_path)
    assert "--find-links" not in seen[0]
    (tmp_path / "wheelhouse").mkdir()
    nc.install(tmp_path)
    assert seen[1][seen[1].index("--find-links") + 1] == str(tmp_path / "wheelhouse")


# ------------------------------------------------------------------ the entry point, run for real
def _env(home: Path, **extra) -> dict:
    return {**os.environ, "NISAR_HUB_HOME": str(home), **extra}


def test_pyz_unpacks_once_and_again_only_for_a_new_build(tmp_path):
    root = _fake_root(tmp_path / "r")
    pyz, home = bh.build(tmp_path / "h.pyz", root=root), tmp_path / "home"
    run = lambda p: subprocess.run([sys.executable, str(p), "--extract-only"], env=_env(home), capture_output=True, text=True)
    first = run(pyz)
    assert first.returncode == 0 and "unpacked into" in first.stdout and (home / "engine" / "a.py").is_file()
    assert not (home / "__main__.py").exists() and not (home / "tests").exists()
    (home / ".env").write_text("KEEP=1\n")                                        # the user's own file survives an update
    assert "already unpacked" in run(pyz).stdout
    (root / "engine" / "a.py").write_text("x = 2\n")
    again = run(bh.build(tmp_path / "h2.pyz", root=root))
    assert "unpacked into" in again.stdout and (home / "engine" / "a.py").read_text() == "x = 2\n"
    assert (home / ".env").read_text() == "KEEP=1\n"


def test_runpy_in_a_notebook_kernel_does_not_choke_on_the_kernels_argv_or_exit(tmp_path):
    """Colab runs `runpy.run_path(pyz, run_name="__main__")` with the kernel's argv (-f kernel.json) and a SystemExit
    would show up as an error in the cell."""
    root = _fake_root(tmp_path / "r")
    pyz = bh.build(tmp_path / "h.pyz", root=root)
    code = textwrap.dedent(f"""
        import runpy, sys, types
        sys.modules["ipykernel"] = types.ModuleType("ipykernel")
        sys.argv = ["ipykernel_launcher.py", "-f", "/tmp/kernel-1.json"]
        import os; os.environ["NISAR_HUB_ARGS"] = "--extract-only"
        runpy.run_path({str(pyz)!r}, run_name="__main__")
        print("RETURNED")
    """)
    r = subprocess.run([sys.executable, "-c", code], env=_env(tmp_path / "home"), capture_output=True, text=True)
    assert r.returncode == 0 and "RETURNED" in r.stdout and "unpacked into" in r.stdout, r.stderr


def test_what_the_pyz_unpacks_is_enough_to_run_the_hub(tmp_path):
    """Only the unpacked slim tree is on the path (no tests/, docs/, app-ui/): the hub app builds, the gate holds, the
    page is served, the tile cover answers."""
    pyz, home = bh.build(tmp_path / "h.pyz"), tmp_path / "home"
    subprocess.run([sys.executable, str(pyz), "--extract-only"], env=_env(home), check=True, capture_output=True)
    code = textwrap.dedent(f"""
        import importlib.util, json, sys
        sys.path.insert(0, {str(home)!r})
        spec = importlib.util.spec_from_file_location("h", {str(home / 'colab' / 'hub.py')!r})
        hub = importlib.util.module_from_spec(spec); spec.loader.exec_module(hub)
        from fastapi.testclient import TestClient
        key = "k" * 30
        c = TestClient(hub.KeyGate(hub.build_app(key), key))
        assert c.get("/hub/api/disk").status_code == 401
        page = c.get("/?k=" + key)
        assert page.status_code == 200 and key in page.text
        cov = c.get("/hub/api/cover", params={{"lon": 90.41849, "lat": 23.748, "size_km": 30}}).json()
        health = c.get("/api/health").json()
        print(json.dumps({{"tiles": len(cov["tiles"]), "first": cov["tiles"][0]["id"], "ok": health["ok"],
                           "engine_from": hub.__file__}}))
    """)
    env = _env(home, NISAR_DATA_DIR=str(tmp_path / "data"), NISAR_DB_DIR=str(tmp_path / "data" / "db"))
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, cwd=str(home))
    assert r.returncode == 0, r.stderr[-2000:]
    got = json.loads(r.stdout.strip().splitlines()[-1])
    assert 9 <= got["tiles"] <= 16 and got["first"] == "46QBM32" and got["ok"] is True
    assert got["engine_from"].startswith(str(home))
