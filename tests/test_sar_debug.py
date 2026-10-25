"""tools/sar_debug is a standalone HTML debug report for retrieval and files. Smoke test: its own self-test passes, the demo
report is written, and it never imports the engine (it only parses it as text)."""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"


def _run(*args, timeout=240):
    return subprocess.run([sys.executable, "-m", "sar_debug", *args], cwd=TOOLS, capture_output=True, text=True, timeout=timeout)


def test_selftest_passes():
    r = _run("--selftest")
    assert r.returncode == 0 and "selftest: OK" in r.stdout, r.stdout[-800:] + r.stderr[-800:]


def test_demo_report_is_written(tmp_path):
    out = tmp_path / "demo.html"
    r = _run("--demo", "--out", str(out))
    assert out.exists() and out.stat().st_size > 10_000, r.stdout[-800:] + r.stderr[-800:]
    assert "<html" in out.read_text(errors="ignore")[:2000].lower()


def test_package_never_imports_the_engine():
    bad = []
    for f in (TOOLS / "sar_debug").glob("*.py"):
        for n in ast.walk(ast.parse(f.read_text())):
            mods = [a.name for a in n.names] if isinstance(n, ast.Import) else [n.module or ""] if isinstance(n, ast.ImportFrom) else []
            bad += [f"{f.name}: {m}" for m in mods if m == "engine" or m.startswith("engine.")]
    assert not bad, bad


def test_engine_view_runs_on_this_checkout(tmp_path):
    r = _run("--engine", str(TOOLS.parent), "--out", str(tmp_path / "e.html"))
    assert r.returncode == 0 and " 0 fail" in r.stdout, r.stdout[-800:] + r.stderr[-800:]
