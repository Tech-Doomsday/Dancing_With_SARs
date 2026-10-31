#!/usr/bin/env python
"""Build the one zip you upload to Colab (run on your own machine, from anywhere):

    python colab/build_colab_zip.py                 ->  dist/nisar-hazard-engine-colab.zip

The zip is the whole repo (engine, config, tests, app-ui SOURCE, colab/ scripts). By DEFAULT it does NOT contain
your `.env`: credentials come from Colab Secrets (EARTHDATA_TOKEN) or the hidden prompt, which nisar_colab.bootstrap()
already supports. Packing the real `.env` is an explicit opt-in:

    python colab/build_colab_zip.py --include-env

then the zip contains your Earthdata login / Supabase key — keep it off public drives, repos and chats, and rotate
the credentials if it ever leaves your machine. Never included: node_modules, .next, data/, caches, *.zip,
app-ui/.env.local."""
from __future__ import annotations

import fnmatch
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "dist" / "nisar-hazard-engine-colab.zip"
SKIP_DIRS = {".git", "node_modules", ".next", "data", "__pycache__", ".pytest_cache", "dist", "test-results",
             "playwright-report", "verify-screens", ".venv", "venv"}
SKIP_GLOBS = ("*.zip", "*.pyc", ".env.local", ".env.*.local", "tsconfig.tsbuildinfo", "_backup_before_install_*", ".DS_Store")


def _wanted(p: Path) -> bool:
    rel = p.relative_to(ROOT)
    if any(part in SKIP_DIRS or fnmatch.fnmatch(part, "_backup_before_install_*") for part in rel.parts[:-1]):
        return False
    return not any(fnmatch.fnmatch(rel.name, g) for g in SKIP_GLOBS)


def _has_credentials(env: Path) -> bool:
    vals = {}
    for line in env.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            vals[k.strip().removeprefix("export ").strip()] = v.strip().strip("\"'")
    return bool(vals.get("EARTHDATA_TOKEN") or (vals.get("EARTHDATA_USERNAME") and vals.get("EARTHDATA_PASSWORD")))


def main() -> int:
    include_env = "--include-env" in sys.argv[1:]
    env_src = None
    if include_env:
        env = ROOT / ".env"
        if not env.is_file():
            print("WARNING: --include-env but no .env at the repo root; nothing credential-like is packed.")
        else:
            env_src = env
            if not _has_credentials(env):
                print("WARNING: .env has no EARTHDATA_TOKEN (or username+password). Colab will fall back to Secrets / a prompt.")
    else:
        print("No .env packed (default). In Colab: add EARTHDATA_TOKEN under Secrets, or type it at the hidden prompt.")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in sorted(ROOT.rglob("*")):
            if p.is_file() and p.name != ".env" and _wanted(p):
                z.write(p, p.relative_to(ROOT).as_posix())
                n += 1
        if env_src is not None:
            z.write(env_src, ".env")
            n += 1
    print(f"{OUT}  ({n} files, {OUT.stat().st_size / 1e6:.1f} MB)")
    print("Upload it to Colab, then run the first notebook cell (or: !unzip -q -o <zip> -d /content/nisar-hazard-engine).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
