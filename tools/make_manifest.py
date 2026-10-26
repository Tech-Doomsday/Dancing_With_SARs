#!/usr/bin/env python3
"""Developer tool: write MANIFEST.json (sha256 of every shipped file). final_check.py uses it to detect a
stale or partial install before running anything."""
import hashlib, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
<<<<<<< HEAD
SKIP_DIRS = {"__pycache__", ".pytest_cache", "data", ".git", "catalog"}
=======
# "app" is excluded: the web application manages its own files (and its
# node_modules / .next build tree) — hashing it here would balloon the
# manifest and go stale on every install.
SKIP_DIRS = {"__pycache__", ".pytest_cache", "data", ".git", "catalog", "app", ".github"}
>>>>>>> 8200fa0 (Integrate MOTUS earthquake analysis into Dancing With SARs)


def files():
    for p in sorted(ROOT.rglob("*")):
        rel = p.relative_to(ROOT)
        if not p.is_file() or set(rel.parts) & SKIP_DIRS or p.suffix in (".pyc",) or rel.name in ("MANIFEST.json", ".env"):
            continue
        yield rel


def digest(p: Path) -> str:
    return hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


if __name__ == "__main__":
    m = {rel.as_posix(): digest(ROOT / rel) for rel in files()}
    (ROOT / "MANIFEST.json").write_text(json.dumps(m, indent=0, sort_keys=True))
    print(f"MANIFEST.json: {len(m)} files")
