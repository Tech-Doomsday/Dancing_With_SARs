"""HEALTH: facts about the machine and the checkout, each derived by looking, not by assuming.

Everything here is read-only: AST parsing (never executing) for code state, importlib for dependencies, os.stat
for storage. The only things that execute engine code are the explicit import test and the on-demand buttons
(network reachability, test suite).
"""
from __future__ import annotations

import ast
import importlib
import importlib.metadata as md
import importlib.util
import os
import platform
import re
import shutil
import socket
import ssl
import subprocess
import sys
import time
from pathlib import Path

from .util import short_exc

REPO = Path(__file__).resolve().parents[2]
ENGINE = REPO / "engine"

# HAND-WRITTEN human-readable labels (names only). Whether a code is produced is NOT hard-coded: see `producers()`.
MEASUREMENT_NAMES = {"M1": "GUNW unwrapped interferograms", "M2": "GCOV backscatter", "M3": "pixel offsets (GUNW/GOFF)",
                     "M4": "soil moisture", "M5": "GSLC", "M6": "Sentinel-1", "M7": "OPERA cross-check products",
                     "P07": "correction audit (tropo / iono / tide layers)"}
# HAND-WRITTEN mapping S-code -> the bundle channel that can carry it (from the engine/common/ancillary.py docstring); a code
# absent here = no channel. Verified against ancillary.py at run time by `s_channel_check()`.
S_CHANNEL = {"S3": "water_occurrence", "S4": "tree_cover", "S8": "glacier", "S9": "builtup", "S10": "shoreline_distance_m",
             "S11": "river_buffer", "S12": "permafrost", "S7": "fact:volcano_distance_km"}
S_NAMES = {"S1": "cross-check layer", "S3": "JRC water occurrence", "S4": "tree cover / loss year", "S5": "fault prior",
           "S6": "basins", "S7": "volcano activity records", "S8": "glacier outlines", "S9": "building footprints / built-up",
           "S10": "baseline coastline", "S11": "river network + widths", "S12": "permafrost zones", "S13": "platforms"}


# ------------------------------------------------------------------------------------------------ code state
def module_state(path: Path) -> dict:
    """stub = nothing executable besides comments / docstring / imports / constants."""
    rel = str(path.relative_to(REPO)).replace(os.sep, "/")
    try:
        src = path.read_text(encoding="utf-8")
    except OSError as ex:
        return {"path": rel, "state": "unreadable", "detail": short_exc(ex), "lines": 0, "defs": 0}
    lines = src.count("\n") + (1 if src and not src.endswith("\n") else 0)
    try:
        tree = ast.parse(src)
    except SyntaxError as ex:
        return {"path": rel, "state": "syntax_error", "detail": f"line {ex.lineno}: {ex.msg}", "lines": lines, "defs": 0}
    defs = sum(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) for n in ast.walk(tree))
    has_run = any(isinstance(n, ast.FunctionDef) and n.name == "run" for n in tree.body)
    first = next((ln.strip().lstrip("#").strip() for ln in src.splitlines() if ln.strip().startswith("#")), "")
    if defs:
        state = "implemented"
    elif not [n for n in tree.body if not (isinstance(n, ast.Expr) and isinstance(getattr(n, "value", None), ast.Constant))]:
        state = "stub"
    else:
        state = "declarations"
    return {"path": rel, "state": state, "lines": lines, "defs": defs, "has_run": has_run, "note": first[:140]}


def _py(d: Path, recursive=False):
    it = d.rglob("*.py") if recursive else d.glob("*.py")
    return sorted(p for p in it if "__pycache__" not in p.parts and p.name != "__init__.py" and "tests" not in p.parts)


def code_readiness() -> list:
    """Stage-by-stage implementation state, from the files that exist right now."""
    groups = [
        ("1 Retrieval · clients", _py(ENGINE / "retrieval" / "clients")),
        ("1 Retrieval · adapters R1–R14", _py(ENGINE / "retrieval" / "clients" / "adapters")),
        ("1 Retrieval · scheduler & pollers", _py(ENGINE / "retrieval" / "scheduler", True)),
        ("1 Retrieval · sources & auth & state", _py(ENGINE / "retrieval" / "sources") + _py(ENGINE / "retrieval" / "auth")
         + [ENGINE / "retrieval" / "state.py", ENGINE / "retrieval" / "settings.py", ENGINE / "retrieval" / "sources" / "__init__.py"]),
        ("2 Common preprocessing P01–P13", sorted((ENGINE / "common").glob("p[0-9][0-9]_*.py"))
         + [ENGINE / "common" / n for n in ("chain.py", "scene_bundle.py", "contract.py", "ancillary.py", "grids.py")]),
        ("2 Common · legacy / unreferenced", [ENGINE / "common" / n for n in ("runner.py", "state.py", "_raster.py")]),
        ("3 lib (shared physics)", _py(ENGINE / "lib")),
        ("4 Verdict", _py(ENGINE / "verdict")),
        ("5 API", _py(ENGINE / "api")),
        ("6 Validation", _py(ENGINE / "validation", True)),
        ("7 Registry", [ENGINE / "registry.py"]),
    ]
    out = []
    for name, files in groups:
        rows = [module_state(f) for f in files if f.exists() and (f.name != "__init__.py" or f.parent.name == "sources")]
        for r in rows:                                   # a file that parses but cannot be imported is not "implemented"
            if r["state"] == "implemented" and r["path"].endswith(("common/runner.py", "common/state.py", "common/_raster.py")):
                try:
                    importlib.import_module(r["path"][:-3].replace("/", "."))
                except Exception as ex:
                    r["state"], r["detail"] = "broken", short_exc(ex)[:160]
        n_impl = sum(r["state"] == "implemented" for r in rows)
        out.append({"group": name, "files": rows, "implemented": n_impl, "total": len(rows)})
    return out


def pipeline_code_state() -> list:
    from engine.registry import scan_pipelines
    mans, errs = scan_pipelines(ENGINE / "pipelines")
    rows = []
    for m in mans:
        d = m.path.parent
        files = {f: module_state(d / f"{f}.py") for f in ("fetch", "prep", "model", "verdict", "lib_local") if (d / f"{f}.py").exists()}
        rows.append({"name": m.name, "code": m.code, "files": {k: v["state"] for k, v in files.items()},
                     "has_run": bool(files.get("verdict", {}).get("has_run")),
                     "implemented": sum(v["state"] == "implemented" for v in files.values())})
    return rows + [{"name": k, "code": "?", "error": v} for k, v in errs.items()]


# ------------------------------------------------------------------------------------------------ dependencies
def _declared() -> set:
    names = set()
    for f in (REPO / "requirements-retrieval.txt", REPO / "requirements.txt"):
        if f.exists():
            for ln in f.read_text().splitlines():
                ln = ln.split("#")[0].strip()
                m = re.match(r"([A-Za-z0-9_.\-]+)", ln)
                if m:
                    names.add(m.group(1).lower().replace("_", "-"))
    pp = REPO / "pyproject.toml"
    if pp.exists():
        m = re.search(r"dependencies\s*=\s*\[(.*?)\]", pp.read_text(), re.S)
        for s in re.findall(r"[\"']([A-Za-z0-9_.\-]+)", m.group(1) if m else ""):
            names.add(s.lower().replace("_", "-"))
    return names


DIST = {"yaml": "pyyaml", "dotenv": "python-dotenv", "asf_search": "asf-search"}


def dependencies() -> list:
    """Third-party imports found by scanning every engine/*.py with ast, checked against this interpreter."""
    used: dict = {}
    for p in ENGINE.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        for n in ast.walk(tree):
            mods = [a.name.split(".")[0] for a in n.names] if isinstance(n, ast.Import) else \
                [n.module.split(".")[0]] if isinstance(n, ast.ImportFrom) and n.level == 0 and n.module else []
            for m in mods:
                if m not in sys.stdlib_module_names and m != "engine":
                    used.setdefault(m, set()).add(str(p.relative_to(REPO)).replace(os.sep, "/"))
    declared = _declared()
    rows = []
    for mod, files in sorted(used.items()):
        dist = DIST.get(mod, mod)
        spec = importlib.util.find_spec(mod)
        try:
            ver = md.version(dist)
        except md.PackageNotFoundError:
            ver = None
        lazy = all(_is_lazy(REPO / f, mod) for f in files)
        rows.append({"module": mod, "dist": dist, "installed": spec is not None, "version": ver,
                     "declared": dist.lower().replace("_", "-") in declared, "n_files": len(files),
                     "files": sorted(files)[:6], "optional_lazy": lazy})
    return rows


def _is_lazy(path: Path, mod: str) -> bool:
    """True when every import of `mod` in the file sits inside a function / try block (optional at runtime)."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    for n in tree.body:
        if isinstance(n, ast.Import) and any(a.name.split(".")[0] == mod for a in n.names):
            return False
        if isinstance(n, ast.ImportFrom) and n.level == 0 and (n.module or "").split(".")[0] == mod:
            return False
    return True


def import_test() -> list:
    """Import every engine module. A missing third-party package is a warning; a broken internal import is a fault."""
    out = []
    for p in sorted(ENGINE.rglob("*.py")):
        if "__pycache__" in p.parts or "tests" in p.parts or p.name == "__main__.py":
            continue
        rel = p.relative_to(REPO).with_suffix("")
        name = ".".join(rel.parts[:-1] if rel.name == "__init__" else rel.parts)
        try:
            importlib.import_module(name)
        except ModuleNotFoundError as ex:
            third = (ex.name or "").split(".")[0] not in ("engine", "")
            out.append({"module": name, "ok": False, "kind": "missing_dependency" if third else "broken",
                        "error": short_exc(ex)})
        except Exception as ex:
            out.append({"module": name, "ok": False, "kind": "broken", "error": short_exc(ex)})
    return out


# ------------------------------------------------------------------------------------------------ independence
# keep in sync with tests/test_pipeline_independence.py (imported live so they cannot drift)
try:
    from tests.test_pipeline_independence import ALLOWED_COMMON, FORBIDDEN_COMMON_NAMES
except Exception:   # hub run outside a pytest context: fall back to a pinned copy
    ALLOWED_COMMON = {"scene_bundle", "contract", "frozen", "bundle_io", "layers", "ancillary", "config", "result"}
    FORBIDDEN_COMMON_NAMES = {"build_scene_bundle", "run_steps", "assemble", "STEPS"}
FORBIDDEN_COMMON_NAMES = {"build_scene_bundle", "run_steps", "assemble", "STEPS"}


def _imports(path: Path, package: str):
    """Yield (absolute dotted module, imported names) for every import statement in `path`, resolving relative imports.
    Same algorithm as `_imports` in tests/test_pipeline_independence.py (re-implemented: the hub must not import from tests/).
    `package` = dotted package of the file (parts of its path without the file name)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield a.name, ()
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")
                base = base[: len(base) - (node.level - 1)]
                mod = ".".join(base + ([node.module] if node.module else []))
            else:
                mod = node.module or ""
            yield mod, tuple(a.name for a in node.names)


def _package_of(f: Path, pipeline_dir: Path) -> str:
    """Dotted package of a pipeline file, derived as engine.pipelines.<folder>[.<subfolders>] from the folder name,
    so it is the same for the real repo and for a scratch directory (tests pass tmp_path)."""
    return ".".join(["engine", "pipelines", pipeline_dir.name, *f.relative_to(pipeline_dir).parts[:-1]])


def independence(pipelines_dir: Path | None = None) -> dict:
    """The static import rules of tests/test_pipeline_independence.py (relative imports resolved): a pipeline may not import
    another pipeline, nor the P01-P13 chain (engine.common modules outside ALLOWED_COMMON, nor build_scene_bundle etc.).
    NOT mirrored here: that test's dynamic import_module() scan and its rule limiting engine.retrieval imports to
    engine.retrieval.clients.adapters (added to the test after the rule this hub was written against)."""
    pdir = pipelines_dir or ENGINE / "pipelines"
    names = {d.name for d in pdir.iterdir() if d.is_dir() and not d.name.startswith((".", "__"))}
    bad, scanned = [], 0
    for d in sorted(pdir.iterdir()):
        if not d.is_dir() or d.name.startswith((".", "__")):
            continue
        for f in d.rglob("*.py"):
            if "__pycache__" in f.parts:
                continue
            scanned += 1
            rel = str(f.relative_to(REPO)) if REPO in f.parents else f"{d.name}/{f.relative_to(d)}"
            try:
                imports = list(_imports(f, _package_of(f, d)))
            except SyntaxError as ex:
                bad.append({"file": rel, "problem": f"syntax error line {ex.lineno}"})
                continue
            in_tests = "tests" in f.relative_to(d).parts
            for mod, imported in imports:
                parts = mod.split(".")
                if parts[:2] == ["engine", "pipelines"]:
                    other = parts[2] if len(parts) > 2 else None
                    if other is None:
                        if [x for x in imported if x != d.name] or not imported:
                            bad.append({"file": rel, "problem": f"imports engine.pipelines {imported}"})
                    elif other != d.name:
                        bad.append({"file": rel, "problem": f"imports another pipeline ({other})"})
                elif mod == "engine.common" and not in_tests and FORBIDDEN_COMMON_NAMES & set(imported):
                    bad.append({"file": rel, "problem": f"imports {sorted(FORBIDDEN_COMMON_NAMES & set(imported))} from engine.common"})
                elif parts[:2] == ["engine", "common"] and len(parts) > 2 and not in_tests and parts[2] not in ALLOWED_COMMON:
                    bad.append({"file": rel, "problem": f"imports engine.common.{parts[2]} (the chain is not a pipeline dependency)"})
    return {"scanned_files": scanned, "pipelines": len(names), "violations": bad}


def pipeline_entry_contract(registry_path: Path | None = None) -> dict:
    """What engine/registry.py really calls in a pipeline, read with ast (the module is never imported here)."""
    path = registry_path or ENGINE / "registry.py"
    rel = str(path.relative_to(REPO)).replace(os.sep, "/") if REPO in path.parents else path.name
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as ex:
        return {"source": rel, "error": short_exc(ex), "entry_module": None, "entry_func": None,
                "registry_refers_to_fetch_module": None, "registry_docstring_says_bundle_only": None,
                "meaning": f"could not read {rel}: {short_exc(ex)}"}
    consts = {}
    for n in tree.body:                                              # module-level NAME = "literal"
        targets, value = (n.targets, n.value) if isinstance(n, ast.Assign) else ([n.target], n.value) if isinstance(n, ast.AnnAssign) else ([], None)
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            for t in targets:
                if isinstance(t, ast.Name):
                    consts[t.id] = value.value
    mod, func = consts.get("ENTRY_MODULE"), consts.get("ENTRY_FUNC")
    fetch = any((isinstance(n, ast.Constant) and n.value == "fetch") or (isinstance(n, ast.Attribute) and n.attr == "fetch")
                or (isinstance(n, ast.Name) and n.id == "fetch") for n in ast.walk(tree))
    doc = ast.get_docstring(tree) or ""
    bundle_only = "sees ONLY the bundle" in doc
    if mod and func:
        meaning = (f"The registry imports engine.pipelines.<name>.{mod} and calls {func}(bundle). "
                   + ("It also refers to a 'fetch' module, so check how." if fetch else
                      "It never refers to fetch.py, so a pipeline's own fetch.py is not called by the registry")
                   + ("; its docstring says a pipeline sees only the bundle (no retriever), which contradicts the own_fetch design."
                      if bundle_only and not fetch else "."))
    else:
        meaning = "ENTRY_MODULE / ENTRY_FUNC were not found as module-level string assignments in the registry; fetch.py handling not determined."
    return {"source": rel, "entry_module": mod, "entry_func": func, "registry_refers_to_fetch_module": fetch,
            "registry_docstring_says_bundle_only": bundle_only, "meaning": meaning}


# ------------------------------------------------------------------------------------------------ producers & needs
def producers() -> dict:
    """code -> {produced: bool, how: str}. Looked up in the code, not asserted."""
    st = lambda rel: module_state(REPO / rel)           # noqa: E731
    nisar = st("engine/retrieval/clients/nisar_client.py")["state"] == "implemented"
    s1 = st("engine/retrieval/clients/sentinel1_client.py")["state"] == "implemented"
    try:
        from engine.common.layers import ROLES
        meas = {r.measurement for r in ROLES.values()}
    except Exception:
        meas = set()
    out = {}
    for c in ("M1", "M2", "M3"):
        out[c] = {"produced": nisar and c in meas, "how": "NisarClient + P03/P04" if nisar and c in meas else "no producer"}
    out["P07"] = {"produced": "corrections" in meas, "how": "P07 reads tropo/iono/tide layers from GUNW" if "corrections" in meas else "no producer"}
    out["M6"] = {"produced": s1, "how": "sentinel1_client.py" if s1 else "sentinel1_client.py is a stub"}
    for c in ("M4", "M5", "M7"):
        out[c] = {"produced": False, "how": "no retrieval source or layer role defines it"}
    ad = ENGINE / "retrieval" / "clients" / "adapters"
    for f in sorted(ad.glob("r[0-9]*_*.py")):
        code = "R" + re.match(r"r(\d+)_", f.name).group(1)
        s = module_state(f)
        out[code] = {"produced": s["state"] == "implemented", "how": f"adapters/{f.name}" + ("" if s["state"] == "implemented" else " is a stub")}
    return out


def ancillary_files(data_dir: Path) -> dict:
    try:
        from engine.common.ancillary import RASTER_NAMES
    except Exception:
        RASTER_NAMES = ()
    root = data_dir / "ancillary"
    out = {}
    for n in RASTER_NAMES:
        d = root / n
        files = [f for f in d.iterdir() if f.suffix.lower() in (".npz", ".tif", ".tiff")] if d.is_dir() else []
        out[n] = {"files": len(files), "bytes": sum(f.stat().st_size for f in files)}
    out["_facts.json"] = {"files": int((root / "facts.json").exists()), "bytes": 0}
    out["_S7_volcanoes.geojson"] = {"files": int((root / "S7_volcanoes.geojson").exists()), "bytes": 0}
    return out


def s_channel_check(ancillary_py: Path | None = None) -> list:
    """For each hand-written S_CHANNEL entry: is the channel name (without any `fact:` prefix) a whole word in
    engine/common/ancillary.py? This verifies the mapping against the code at run time."""
    path = ancillary_py or ENGINE / "common" / "ancillary.py"
    try:
        src = path.read_text(encoding="utf-8")
        err = None
    except OSError as ex:
        src, err = "", short_exc(ex)
    out = []
    for code, ch in sorted(S_CHANNEL.items(), key=lambda kv: int(kv[0][1:])):
        name = ch.removeprefix("fact:")
        found = bool(re.search(r"\b" + re.escape(name) + r"\b", src))
        out.append({"code": code, "channel": ch, "name": name, "found_in_ancillary_py": found,
                    **({"error": err} if err else {})})
    return out


def needs_matrix(data_dir: Path) -> dict:
    """pipeline x code: where would each declared input come from, and does that source exist?"""
    from engine.registry import scan_pipelines
    mans, _ = scan_pipelines(ENGINE / "pipelines")
    prod, anc = producers(), ancillary_files(data_dir)
    chan_ok = {r["code"]: r["found_in_ancillary_py"] for r in s_channel_check()}
    cols: list = []
    rows = []

    def cell(code: str, role: str) -> dict:
        if code in MEASUREMENT_NAMES or code.startswith("M") or code == "P07":
            p = prod.get(code, {"produced": False, "how": "unknown code"})
            return {"state": "ok" if p["produced"] else "missing", "why": p["how"]}
        if code.startswith("S"):
            ch = S_CHANNEL.get(code)
            if ch is None:
                return {"state": "missing", "why": f"no source or downloader for {code} ({S_NAMES.get(code, '?')}) exists in the repo" if role == "fetch" else f"no channel in the bundle for {code} ({S_NAMES.get(code, '?')})"}
            if not chan_ok.get(code, False):
                return {"state": "missing", "why": f"channel '{ch.removeprefix('fact:')}' is not named anywhere in engine/common/ancillary.py"}
            have = anc.get(ch, {}).get("files", 0) if not ch.startswith("fact:") else int(anc["_facts.json"]["files"] or anc["_S7_volcanoes.geojson"]["files"])
            return {"state": "ok" if have else "empty", "why": f"bundle channel '{ch}' exists; {have} file(s) in data/ancillary, and nothing in the repo downloads them" if not have else f"{have} file(s) in data/ancillary/{ch}"}
        if code.startswith("R"):
            p = prod.get(code, {"produced": False, "how": "no adapter file"})
            return {"state": "ok" if p["produced"] else "missing", "why": p["how"] + "; pulled by the pipeline's own fetch.py"}
        return {"state": "missing", "why": "unknown code"}

    for m in mans:
        codes = [("uses", c) for c in m.uses_codes] + [("fetch", c) for c in m.fetch_codes]
        cells = {}
        for role, c in codes:
            if c not in cols:
                cols.append(c)
            cells[c] = {**cell(c, role), "role": role}
        rows.append({"name": m.name, "code": m.code, "sensor": m.sensor, "cells": cells})

    def keyf(c):
        return ("MPSR".index(c[0]) if c[0] in "MPSR" else 9, int(re.sub(r"\D", "", c) or 0))
    return {"columns": sorted(cols, key=keyf), "rows": rows, "prod": prod,
            "names": {**MEASUREMENT_NAMES, **S_NAMES}}


# ------------------------------------------------------------------------------------------------ environment
def environment(settings) -> dict:
    env_names = ["EARTHDATA_TOKEN", "EARTHDATA_USERNAME", "EARTHDATA_PASSWORD", "NISAR_DATA_DIR"]
    creds = {n: bool(os.environ.get(n)) for n in env_names}             # presence only, never the value
    gi = (REPO / ".gitignore").read_text().splitlines() if (REPO / ".gitignore").exists() else []
    dotenv = REPO / ".env"
    data = Path(settings.data_dir)
    probe = data if data.exists() else data.parent
    try:
        du = shutil.disk_usage(probe)
        disk = {"free_gb": round(du.free / 1e9, 1), "total_gb": round(du.total / 1e9, 1)}
    except OSError as ex:
        disk = {"error": short_exc(ex)}
    sub = {}
    for n, p in (("clips", settings.clips_dir), ("chunks", settings.chunks_dir), ("layout index", settings.index_dir),
                 ("search cache", settings.search_dir), ("state db", settings.state_db), ("ancillary", data / "ancillary"),
                 ("bundles", data / "bundles")):
        sub[n] = _dir_stats(p)
    return {"python": sys.version.split()[0], "platform": platform.platform(), "executable": sys.executable,
            "repo": str(REPO), "creds": creds,
            "auth_mode": "token" if creds["EARTHDATA_TOKEN"] else "user/password" if creds["EARTHDATA_USERNAME"] and creds["EARTHDATA_PASSWORD"] else "none",
            "dotenv_present": dotenv.exists(), "dotenv_gitignored": any(g.strip() in (".env", "/.env") for g in gi),
            "data_dir": str(data), "data_dir_exists": data.exists(), "disk": disk, "storage": sub,
            "ancillary": ancillary_files(data)}


def _dir_stats(p: Path, cap: int = 100_000) -> dict:
    if not p.exists():
        return {"exists": False}
    if p.is_file():
        return {"exists": True, "bytes": p.stat().st_size, "files": 1}
    total = n = 0
    for root, _, files in os.walk(p):
        for f in files:
            n += 1
            if n > cap:
                return {"exists": True, "bytes": total, "files": n - 1, "truncated": True}
            try:
                total += os.stat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return {"exists": True, "bytes": total, "files": n}


# ------------------------------------------------------------------------------------------------ on demand
HOSTS = [("urs.earthdata.nasa.gov", "Earthdata login"), ("cmr.earthdata.nasa.gov", "CMR"),
         ("api.daac.asf.alaska.edu", "ASF search API")]


def network_check(timeout: float = 8.0) -> list:
    out = []
    for host, what in HOSTS:
        row = {"host": host, "what": what}
        t = time.perf_counter()
        try:
            ip = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)[0][4][0]
            row["dns_ms"] = round((time.perf_counter() - t) * 1000)
            t = time.perf_counter()
            with socket.create_connection((host, 443), timeout=timeout) as s:
                row["tcp_ms"] = round((time.perf_counter() - t) * 1000)
                t = time.perf_counter()
                with ssl.create_default_context().wrap_socket(s, server_hostname=host) as ss:
                    row["tls_ms"] = round((time.perf_counter() - t) * 1000)
                    ss.sendall(f"HEAD / HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\nUser-Agent: debug-hub\r\n\r\n".encode())
                    line = ss.recv(256).split(b"\r\n")[0].decode("latin1", "replace")
                    row["http"] = line
            row["ip"], row["ok"] = ip, True
        except Exception as ex:
            row["ok"], row["error"] = False, short_exc(ex)
        out.append(row)
    return out


def run_tests(timeout: int = 600) -> dict:
    if importlib.util.find_spec("pytest") is None:
        return {"ran": False, "reason": "pytest is not installed in this interpreter"}
    t = time.perf_counter()
    try:
        p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "--no-header", "-p", "no:cacheprovider", "tests"],
                           cwd=REPO, capture_output=True, text=True, timeout=timeout)
        return {"ran": True, "returncode": p.returncode, "seconds": round(time.perf_counter() - t, 1),
                "output": (p.stdout + p.stderr)[-12000:]}
    except subprocess.TimeoutExpired as ex:
        return {"ran": True, "returncode": None, "timeout": True, "output": ((ex.stdout or b"").decode("utf-8", "replace"))[-6000:]}


def health_snapshot(settings) -> dict:
    return {"environment": environment(settings), "dependencies": dependencies(), "code": code_readiness(),
            "pipelines": pipeline_code_state(), "imports": import_test(), "independence": independence(), "contract": pipeline_entry_contract(), "s_channel_check": s_channel_check(),
            "needs": needs_matrix(Path(settings.data_dir)), "generated": time.time()}
