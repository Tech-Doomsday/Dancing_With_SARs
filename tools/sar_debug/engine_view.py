"""
Read-only view of a Dancing_With_SARs checkout (or any folder with the same layout).

HARD RULE: this module never imports `engine.*` and never executes anything from the checkout.
It only lists files, parses YAML/text, parses Python with `ast` (never `exec`/`import`) and opens
SQLite in read-only mode. If the engine is broken, half-written or missing, this still works.
"""
from __future__ import annotations

import ast
import os
import re

import numpy as np

# what the README "codebase map" says should exist
EXPECTED_DIRS = ["engine/retrieval/scheduler", "engine/retrieval/clients", "engine/retrieval/sources", "engine/retrieval/auth",
                 "engine/common", "engine/lib", "engine/pipelines", "engine/verdict", "engine/api", "engine/validation",
                 "config", "docs", "tests", "data"]
COMMON_CHAIN = ["p01_granule_resolution", "p02_clip", "p03_decode_gate", "p04_grid_harmonization", "p05_geometry",
                "p06_phase_to_los", "p07_correction_audit", "p08_backscatter_prep", "p09_ancillary_align",
                "p10_temporal_stack", "p11_stable_mask", "p12_tile_routing", "p13_provenance", "scene_bundle"]
VERDICT_STEPS = ["collect", "calibrate", "link_events", "select", "persist"]
PIPELINE_FILES = ["manifest.yaml", "prep.py", "fetch.py", "model.py", "verdict.py", "lib_local.py", "tests", "README.md"]
STUB_HINTS = re.compile(r"NotImplementedError|TODO|FIXME|\bpass\s*$", re.M)


def _read(path, limit=200_000):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(limit)
    except OSError:
        return None


def _py_status(path):
    """(lines, stub?) - heuristic only: a file is 'stub' when it is tiny or says NotImplementedError/TODO."""
    src = _read(path)
    if src is None:
        return None, None
    lines = [l for l in src.splitlines() if l.strip() and not l.strip().startswith("#")]
    body = [l for l in lines if not l.strip().startswith(('"""', "'''"))]
    stub = len(body) < 12 or bool(STUB_HINTS.search(src))
    return len(lines), stub


def _manifest(path):
    txt = _read(path)
    if txt is None:
        return None
    try:
        import yaml
        d = yaml.safe_load(txt)
        return d if isinstance(d, dict) else {"_raw": txt[:400]}
    except ImportError:
        return {"_raw": txt[:400]}
    except Exception as ex:
        return {"_error": f"{type(ex).__name__}: {ex}"}


def _imports(path):
    """Module names imported by a python file, via ast (the file is never executed)."""
    src = _read(path)
    if src is None:
        return []
    try:
        tree = ast.parse(src)
    except SyntaxError as ex:
        return [f"<syntax error line {ex.lineno}>"]
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out += [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom):
            out.append(("." * n.level) + (n.module or ""))
    return out


def scan(root: str, max_rows: int = 12) -> dict:
    root = os.path.abspath(root)
    res = {"root": root, "exists": os.path.isdir(root), "layout": [], "pipelines": [], "common": [], "verdict": [],
           "independence": [], "data": {}, "verdicts_db": None, "config": {}, "errors": []}
    if not res["exists"]:
        return res
    for d in EXPECTED_DIRS:
        res["layout"].append((d, os.path.isdir(os.path.join(root, d))))

    # 16 pipelines + template
    pdir = os.path.join(root, "engine", "pipelines")
    names = sorted(n for n in (os.listdir(pdir) if os.path.isdir(pdir) else []) if os.path.isdir(os.path.join(pdir, n)) and not n.startswith((".", "__")))
    for n in names:
        p = os.path.join(pdir, n)
        present = {f: os.path.exists(os.path.join(p, f)) for f in PIPELINE_FILES}
        lines, stub = _py_status(os.path.join(p, "model.py"))
        man = _manifest(os.path.join(p, "manifest.yaml")) if present["manifest.yaml"] else None
        res["pipelines"].append({"name": n, "files": present, "model_lines": lines, "model_stub": stub,
                                 "manifest": {k: (v if not isinstance(v, (dict, list)) else str(v)[:90])
                                              for k, v in (man or {}).items()} if man else None})
        # independence: a pipeline may import engine.lib but never a sibling pipeline
        for fn in sorted(os.listdir(p)):
            if fn.endswith(".py"):
                for mod in _imports(os.path.join(p, fn)):
                    m = re.match(r"engine\.pipelines\.([A-Za-z0-9_]+)", mod)
                    if (m and m.group(1) != n) or re.match(r"\.\.[A-Za-z_]", mod):
                        res["independence"].append(f"engine/pipelines/{n}/{fn} imports {mod}")

    for n in COMMON_CHAIN:
        f = os.path.join(root, "engine", "common", n + ".py")
        lines, stub = _py_status(f)
        res["common"].append({"name": n, "exists": os.path.exists(f), "lines": lines, "stub": stub})
    for n in VERDICT_STEPS:
        f = os.path.join(root, "engine", "verdict", n + ".py")
        lines, stub = _py_status(f)
        res["verdict"].append({"name": n, "exists": os.path.exists(f), "lines": lines, "stub": stub})

    # config
    for fn in ("tiling.yaml",):
        txt = _read(os.path.join(root, "config", fn), 20_000)
        if txt is not None:
            res["config"][fn] = txt

    # data directory: clips, bundles, ancillary, reference
    droot = os.path.join(root, "data")
    for sub in ("clips", "ancillary", "reference", "bundles"):
        d = os.path.join(droot, sub)
        files = []
        if os.path.isdir(d):
            for dp, _, fns in os.walk(d):
                for fn in fns:
                    fp = os.path.join(dp, fn)
                    try:
                        files.append((os.path.relpath(fp, d), os.path.getsize(fp)))
                    except OSError:
                        pass
        res["data"][sub] = {"exists": os.path.isdir(d), "files": sorted(files)[:200], "count": len(files),
                            "bytes": sum(s for _, s in files)}
    res["clip_paths"] = [os.path.join(droot, "clips", f) for f, _ in res["data"]["clips"]["files"] if f.endswith(".npz")]

    # verdicts.db (read-only)
    db = os.path.join(droot, "verdicts.db")
    if os.path.exists(db):
        res["verdicts_db"] = _sqlite_summary(db, max_rows)
    return res


def _sqlite_summary(path, max_rows):
    import sqlite3
    out = {"path": path, "bytes": os.path.getsize(path), "tables": [], "error": None}
    try:
        con = sqlite3.connect("file:" + path.replace("\\", "/") + "?mode=ro", uri=True, timeout=2)
        try:
            for (name,) in con.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%' order by name"):
                q = '"' + name.replace('"', '""') + '"'
                cols = [r[1] for r in con.execute(f"pragma table_info({q})")]
                n = con.execute(f"select count(*) from {q}").fetchone()[0]
                rows = [[_cell(c) for c in r] for r in con.execute(f"select * from {q} order by rowid desc limit {int(max_rows)}")]
                out["tables"].append({"name": name, "columns": cols, "rows": n, "sample": rows})
        finally:
            con.close()
    except Exception as ex:
        out["error"] = f"{type(ex).__name__}: {ex}"
    return out


def _cell(v):
    if isinstance(v, bytes):
        return f"<{len(v)} bytes>"
    s = str(v)
    return s if len(s) <= 120 else s[:117] + "..."


def npz_inventory(path):
    """keys / shapes / dtypes of an .npz clip, read from the .npy headers only (no array data is loaded)."""
    import zipfile
    rows = []
    try:
        with zipfile.ZipFile(path) as zf:
            for n in zf.namelist():
                if not n.endswith(".npy"):
                    continue
                with zf.open(n) as f:
                    ver = np.lib.format.read_magic(f)
                    shape, _, dtype = (np.lib.format.read_array_header_1_0(f) if ver == (1, 0)
                                       else np.lib.format.read_array_header_2_0(f))
                rows.append((n[:-4], tuple(shape), str(dtype)))
    except Exception as ex:
        rows.append(("<unreadable>", (), f"{type(ex).__name__}: {ex}"))
    return rows
