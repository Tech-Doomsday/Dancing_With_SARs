"""Atomic file/dir replacement that survives Windows (PermissionError when another process has the
destination open) and concurrent writers of identical, immutable content."""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path


def replace_file(src, dst, retries: int = 12, delay: float = 0.05, keep_existing: bool = False) -> bool:
    """os.replace with retries. keep_existing=True: if dst already exists, drop src and succeed
    (content-addressed files are identical, so whoever got there first wins)."""
    src, dst = str(src), str(dst)
    for i in range(retries):
        try:
            os.replace(src, dst)
            return True
        except OSError:
            if keep_existing and os.path.exists(dst):
                break
            time.sleep(delay * (i + 1))
    try:
        os.remove(src)
    except OSError:
        pass
    return keep_existing and os.path.exists(dst)


def replace_dir(src, dst, retries: int = 8, delay: float = 0.1) -> bool:
    """Move a finished temp directory into place. If a concurrent writer finished first, keep theirs."""
    src, dst = Path(src), Path(dst)
    for i in range(retries):
        try:
            if dst.exists():
                shutil.rmtree(dst, ignore_errors=True)
            os.replace(src, dst)
            return True
        except OSError:
            if dst.exists() and (dst / "meta.json").exists():
                break
            time.sleep(delay * (i + 1))
    shutil.rmtree(src, ignore_errors=True)
    return dst.exists()
