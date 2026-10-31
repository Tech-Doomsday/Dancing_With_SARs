#!/usr/bin/env python3
"""Developer tool: write MANIFEST.json (sha256 of every shipped file). final_check.py uses it to detect a
stale or partial install before running anything."""
import hashlib, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Bare directory names skipped at ANY depth (build/test artefacts, dependency trees, VCS, the runtime
# data dir). NOTE: a bare "catalog" must NOT be here — it also matches the shipped config/catalog/.
SKIP_DIRS = {"__pycache__", ".pytest_cache", "data", ".git",
             # build/test artefacts and dependency trees are never shipped (regenerated locally)
             "node_modules", ".next", "test-results", "verify-screens", "playwright-report"}
# Path-specific skips, relative to the repo root: only the RUNTIME data catalog (<root>/data/catalog,
# learned at run time), never config/catalog/ (shipped, committed by final_check.py).
SKIP_PATHS = {("data", "catalog")}
SKIP_NAMES = {"MANIFEST.json", ".env"}


def is_skipped(rel: Path) -> bool:
    """The one skip rule — tools/final_check.py imports this instead of keeping its own."""
    parts = rel.parts
    return (bool(set(parts) & SKIP_DIRS)
            or any(parts[:len(sp)] == sp for sp in SKIP_PATHS)
            or rel.suffix in (".pyc",)
            or rel.name in SKIP_NAMES)


def files():
    for p in sorted(ROOT.rglob("*")):
        rel = p.relative_to(ROOT)
        if p.is_file() and not is_skipped(rel):
            yield rel


def digest(p: Path) -> str:
    return hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


if __name__ == "__main__":
    m = {rel.as_posix(): digest(ROOT / rel) for rel in files()}
    (ROOT / "MANIFEST.json").write_text(json.dumps(m, indent=0, sort_keys=True))
    print(f"MANIFEST.json: {len(m)} files")
