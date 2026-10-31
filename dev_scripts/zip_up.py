#!/usr/bin/env python3
"""zip_up - bundle ONE part of the project into a zip, with just enough context around it.

    python dev_scripts/zip_up.py retrieval          # also: ret, retr, retirivel, "1" ...
    python dev_scripts/zip_up.py preprocessing      # also: processing, pro, pross, pre, common, "2" ...
    python dev_scripts/zip_up.py earthquake         # a single pipeline: name, keyword, or number (3.01)
    python dev_scripts/zip_up.py pipelines          # stage 3 as a whole (every pipeline folder)
    python dev_scripts/zip_up.py --list             # every target + accepted aliases

What goes in the zip (each file appears once, under its strongest role):

    PART       every file of the requested part, in full
    COMPANION  non-Python things the part needs at runtime / its own contract (config yaml, requirements)
    USAGE      tests/ + tools/ files that import the part (shows how it is called)
    IMPORT     files OUTSIDE the part that the part imports DIRECTLY (one hop, that exact module only,
               not the whole package) - so you can see how it uses the neighbouring segments
    VISION     README.md, docs/ARCHITECTURE.md, pyproject.toml (+ the workflow html if present)

A ZIP_MANIFEST.md at the top of the zip lists every file with its role and who imports it.
Output: dev_scripts/out/<target>.zip   (override with --out)

Limits: imports are found statically (ast). importlib / registry-style dynamic loading is not seen.
Stdlib only, Python >= 3.9.
"""
from __future__ import annotations

import argparse
import ast
import datetime as dt
import difflib
import glob
import re
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
IMPORT_ROOTS = [REPO, REPO / "engine", REPO / "src", REPO / "tests"]
OUT_DIR_DEFAULT = Path(__file__).resolve().parent / "out"

IGNORE_DIRS = {
    ".git", "__pycache__", "node_modules", ".next", ".venv", "venv", "env", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".idea", ".vscode", "dist", "build", ".cache",
}
IGNORE_SUFFIXES = {".pyc", ".pyo", ".db", ".sqlite", ".sqlite3", ".log"}
IGNORE_NAMES = {".DS_Store", "Thumbs.db"}


def is_secret_env_file(name: str) -> bool:
    """.env and .env.* carry NISAR_BACKGROUND_TOKEN / Earthdata credentials: never zipped (.env.example is fine)."""
    return (name == ".env" or name.startswith(".env.")) and name != ".env.example"

VISION_FILES = ["README.md", "docs/ARCHITECTURE.md", "pyproject.toml", "docs/SARs_full_workflow.html",
                "SARs_full_workflow.html"]

# role -> priority (lower wins when a file qualifies for several)
ROLE_RANK = {"PART": 0, "COMPANION": 1, "USAGE": 2, "IMPORT": 3, "VISION": 4}

PIPELINE_ORDER = [
    "earthquake_coseismic", "land_subsidence", "volcanic_deformation", "landslide", "glacier_ice_flow",
    "permafrost_thaw", "flood_inundation", "deforestation", "wildfire_burn_scar", "river_erosion",
    "coastal_erosion", "urban_damage", "tropical_cyclone", "tsunami_inundation", "oil_spill",
    "iceberg_calving_drift",
]
PIPELINE_SYNONYMS = {
    "earthquake_coseismic": ["quake", "eq", "seismic"],
    "land_subsidence": ["sinking", "subside"],
    "volcanic_deformation": ["volcano", "mogi"],
    "landslide": ["slide", "slope"],
    "glacier_ice_flow": ["glacial", "icestream"],
    "permafrost_thaw": ["thaw", "frozen"],
    "flood_inundation": ["flooding"],
    "deforestation": ["forest", "logging"],
    "wildfire_burn_scar": ["fire", "wildfire", "burnscar"],
    "river_erosion": ["riverbank"],
    "coastal_erosion": ["coast", "shoreline"],
    "urban_damage": ["city", "buildings", "damage"],
    "tropical_cyclone": ["cyclone", "hurricane", "typhoon", "storm"],
    "tsunami_inundation": ["tidalwave"],
    "oil_spill": ["oil", "spill"],
    "iceberg_calving_drift": ["berg", "calving"],
    "_template": ["template"],
}
TOKEN_STOPWORDS = {"and", "the"}


# --------------------------------------------------------------------------------------- targets
@dataclass
class Target:
    key: str
    title: str
    aliases: list[str]                     # strong aliases (full-weight)
    parts: list[str]                       # repo-relative dirs/files included in full
    companions: list[str] = field(default_factory=list)   # globs, repo-relative
    weak_aliases: list[str] = field(default_factory=list)  # keywords (slightly down-weighted)


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9.]", "", s.lower())


def build_targets() -> list[Target]:
    t: list[Target] = [
        Target("retrieval", "1 - DATA RETRIEVAL", [
            "retrieval", "retrieve", "retrievals", "ret", "retr", "dataretrieval", "ingest", "ingestion",
            "fetching", "1",
        ], ["engine/retrieval"], [
            "config/retrieval.yaml", "config/catalog/**", "config/tiling.yaml",
            "requirements-retrieval.txt", "README_retrieval.md", ".env.example",
        ]),
        Target("preprocessing", "2 - COMMON PREPROCESSING (P01..P13)", [
            "preprocessing", "preprocess", "preprocessor", "preproc", "processing", "process", "prep",
            "pre", "pross", "common", "commonpreprocessing", "scenebundle", "2",
        ], ["engine/common"], [
            "config/tiling.yaml", "tests/test_scene_bundle_contract.py",
        ]),
        Target("pipelines", "3 - ALL PIPELINES (every folder + _template)", [
            "pipelines", "pipeline", "pipes", "allpipelines", "hazards", "hazard", "3",
        ], ["engine/pipelines"], [
            "tests/test_pipeline_independence.py", "tests/test_scene_bundle_contract.py",
        ]),
        Target("verdict", "4 - VERDICT", ["verdict", "verdicts", "verdicting", "4"], ["engine/verdict"]),
        Target("serving", "5 - SERVING & UI", [
            "serving", "serve", "server", "api", "ui", "app", "frontend", "web", "5",
        ], ["engine/api", "app"]),
        Target("validation", "6 - VALIDATION & CALIBRATION", [
            "validation", "validate", "valid", "calibration", "labels", "metrics", "6",
        ], ["engine/validation"]),
        Target("crosscutting", "7 - CROSS-CUTTING (registry, tests, config, docs)", [
            "crosscutting", "cross", "registry", "tests", "test", "config", "docs", "7",
        ], ["engine/registry.py", "tests", "config", "docs"]),
        Target("lib", "SHARED LIB - engine/lib", ["lib", "libs", "library", "shared"], ["engine/lib"]),
        Target("tiling", "TILING - engine/tiling", ["tiling", "tiles", "tile", "mgrs", "grid"],
               ["engine/tiling"], ["config/tiling.yaml"]),
    ]
    # individual pipelines, discovered from disk (falls back to the documented list)
    pdir = REPO / "engine" / "pipelines"
    if pdir.is_dir():
        names = [p.name for p in sorted(pdir.iterdir())
                 if p.is_dir() and p.name not in IGNORE_DIRS and not p.name.startswith(".")]
    else:
        names = PIPELINE_ORDER + ["_template"]
    for name in names:
        num = PIPELINE_ORDER.index(name) + 1 if name in PIPELINE_ORDER else None
        strong = [name, name.lstrip("_")]
        if num:
            strong.append(f"3.{num:02d}")
        tokens = [x for x in name.strip("_").split("_") if len(x) >= 3 and x not in TOKEN_STOPWORDS]
        title = f"3.{num:02d} {name}" if num else f"3.-- {name}"
        t.append(Target(
            f"pipeline_{name.lstrip('_')}", title, strong, [f"engine/pipelines/{name}"],
            ["tests/test_pipeline_independence.py", "tests/test_scene_bundle_contract.py"],
            weak_aliases=tokens + PIPELINE_SYNONYMS.get(name, []),
        ))
    return t


# --------------------------------------------------------------------------------------- matching
def _score(q: str, alias: str) -> float:
    a = norm(alias)
    if not a:
        return 0.0
    if q == a:
        return 1.0
    if len(q) >= 2 and a.startswith(q):
        return 0.9 - 0.1 * (1 - len(q) / len(a))
    if len(a) >= 4 and q.startswith(a):
        return 0.85
    if len(q) < 4:                      # too short to fuzzy-match safely ("foo" must not become "flood")
        return 0.0
    r = difflib.SequenceMatcher(None, q, a).ratio()
    return r * 0.85 if r >= 0.72 else 0.0


def match_target(query: str, targets: list[Target]) -> tuple[Target | None, list[Target], str]:
    """-> (best, ambiguous_candidates, how). best is None when nothing/too-many matched."""
    q = norm(query.strip().strip("/\\"))
    if not q:
        return None, [], "empty"

    m = re.fullmatch(r"3\.?(\d{1,2})", q)       # 3.07 / 307 -> pipeline #7   (bare "3" stays stage 3)
    if m and ("." in q or len(q) >= 3):
        want = f"3.{int(m.group(1)):02d}"
        for t in targets:
            if want in [norm(a) for a in t.aliases]:
                return t, [], "pipeline number"

    scored: list[tuple[float, Target]] = []
    for t in targets:
        best = max([_score(q, a) for a in t.aliases] + [0.95 * _score(q, a) for a in t.weak_aliases] + [0.0])
        if best > 0:
            scored.append((best, t))
    if not scored:
        return None, [], "none"
    scored.sort(key=lambda x: -x[0])
    top = scored[0][0]
    close = [t for s, t in scored if s >= top - 0.16]
    if top >= 1.0 and len([1 for s, _ in scored if s >= 1.0]) == 1:
        return scored[0][1], [], "exact"
    if len(close) == 1:
        return close[0], [], "exact" if top >= 0.95 else "fuzzy"
    return None, close, "ambiguous"


# --------------------------------------------------------------------------------------- file walking
def walk(p: Path):
    if p.is_symlink():
        return
    if p.is_file():
        if p.name not in IGNORE_NAMES and p.suffix not in IGNORE_SUFFIXES and not is_secret_env_file(p.name):
            yield p
        return
    if not p.is_dir():
        return
    for child in sorted(p.iterdir()):
        if child.is_dir():
            if child.name in IGNORE_DIRS or child.name.endswith(".egg-info"):
                continue
            yield from walk(child)
        else:
            yield from walk(child)


def expand_glob(pattern: str) -> list[Path]:
    out: list[Path] = []
    for hit in sorted(glob.glob(pattern, root_dir=str(REPO), recursive=True)):
        out.extend(walk(REPO / hit))
    return out


def rel(p: Path) -> str:
    return p.relative_to(REPO).as_posix()


def is_inside(p: Path, folder: Path) -> bool:
    try:
        p.relative_to(folder)
        return True
    except ValueError:
        return False


# --------------------------------------------------------------------------------------- import resolution
def parse_imports(py: Path) -> list[tuple[str, int, list[str]]]:
    """-> [(module, level, [names])] ; module '' for 'from . import x'."""
    try:
        tree = ast.parse(py.read_text(encoding="utf-8", errors="replace"), filename=str(py))
    except SyntaxError as e:
        print(f"  ! cannot parse {rel(py)}: {e.msg} (line {e.lineno}) - imports skipped", file=sys.stderr)
        return []
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [(a.name, 0, []) for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            out.append((node.module or "", node.level, [a.name for a in node.names if a.name != "*"]))
    return out


def _dotted(base: Path, parts: list[str]) -> tuple[list[Path], Path | None]:
    """Files for dotted module `parts` under `base` (+ __init__ of every package on the way).
    Second value is the package dir when the target is a package (so `from pkg import sub` can be resolved)."""
    files: list[Path] = []
    if not parts:
        init = base / "__init__.py"
        return ([init] if init.is_file() else []), base
    cur = base
    for i, part in enumerate(parts):
        last = i == len(parts) - 1
        d, m = cur / part, cur / f"{part}.py"
        if m.is_file() and (last or not d.is_dir()):
            if not last:
                return [], None
            return files + [m], None
        if d.is_dir():
            init = d / "__init__.py"
            if init.is_file():
                files.append(init)
            cur = d
            continue
        return [], None
    return files, cur


def resolve_import(module: str, level: int, names: list[str], src: Path) -> list[Path]:
    parts = module.split(".") if module else []
    if level > 0:
        base = src.parent
        for _ in range(level - 1):
            base = base.parent
        bases = [base]
    else:
        bases = IMPORT_ROOTS
    for base in bases:
        if not base.is_dir():
            continue
        files, pkgdir = _dotted(base, parts)
        if not files and pkgdir is None:
            continue
        if pkgdir is not None:
            for n in names:
                if (pkgdir / f"{n}.py").is_file():
                    files.append(pkgdir / f"{n}.py")
                elif (pkgdir / n / "__init__.py").is_file():
                    files.append(pkgdir / n / "__init__.py")
        files = [f for f in files if is_inside(f, REPO)]
        if files:
            return files
    return []


# --------------------------------------------------------------------------------------- bundle
@dataclass
class Bundle:
    roles: dict[Path, str] = field(default_factory=dict)
    notes: dict[Path, set[str]] = field(default_factory=dict)   # path -> importers / reasons
    skipped: list[tuple[str, str]] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    max_bytes: int = 5 * 1024 * 1024

    def add(self, path: Path, role: str, note: str = "") -> None:
        path = path.resolve()
        if path.stat().st_size > self.max_bytes:
            self.skipped.append((rel(path), f"larger than {self.max_bytes // 1024 // 1024} MB"))
            return
        cur = self.roles.get(path)
        if cur is None or ROLE_RANK[role] < ROLE_RANK[cur]:
            self.roles[path] = role
        if note:
            self.notes.setdefault(path, set()).add(note)

    def of(self, role: str) -> list[Path]:
        return sorted((p for p, r in self.roles.items() if r == role), key=rel)


def collect(target: Target, vision: bool, companions: bool, usage: bool, imports: bool, max_mb: float) -> Bundle:
    b = Bundle(max_bytes=int(max_mb * 1024 * 1024))
    part_dirs: list[Path] = []

    # PART
    for p in target.parts:
        path = REPO / p
        if not path.exists():
            b.missing.append(p)
            continue
        part_dirs.append(path)
        for f in walk(path):
            b.add(f, "PART")
    if not b.of("PART"):
        return b
    # package __init__ files above the part so it stays importable
    for d in part_dirs:
        for anc in d.parents:
            if anc == REPO.parent or not is_inside(anc, REPO) or anc == REPO:
                break
            init = anc / "__init__.py"
            if init.is_file():
                b.add(init, "IMPORT", "package __init__ above the part")

    part_py = [p for p in b.of("PART") if p.suffix == ".py"]
    part_set = set(b.of("PART"))

    # COMPANION
    if companions:
        for pat in target.companions:
            hits = expand_glob(pat)
            if not hits:
                b.missing.append(pat)
            for f in hits:
                b.add(f, "COMPANION")

    # IMPORT (one hop out of the part)
    if imports:
        for py in part_py:
            for module, level, names in parse_imports(py):
                for dep in resolve_import(module, level, names, py):
                    if dep.resolve() not in part_set and dep.suffix == ".py":
                        b.add(dep, "IMPORT", f"imported by {rel(py)}")

    # USAGE (tests/tools that import the part), plus the small test helpers they pull in
    if usage:
        for folder in ("tests", "tools"):
            for f in (walk(REPO / folder) if (REPO / folder).is_dir() else []):
                if f.suffix != ".py" or f.resolve() in part_set:
                    continue
                hit = False
                for module, level, names in parse_imports(f):
                    if any(d.resolve() in part_set for d in resolve_import(module, level, names, f)):
                        hit = True
                        break
                if hit:
                    b.add(f, "USAGE", "imports the part")
                    for module, level, names in parse_imports(f):
                        for dep in resolve_import(module, level, names, f):
                            if is_inside(dep, REPO / "tests") and dep.suffix == ".py":
                                b.add(dep, "USAGE", f"test helper imported by {rel(f)}")

    # VISION
    if vision:
        for v in VISION_FILES:
            if (REPO / v).is_file():
                b.add(REPO / v, "VISION")
    return b


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def manifest(target: Target, b: Bundle, query: str, how: str) -> str:
    lines = [
        f"# ZIP_MANIFEST - {target.title}",
        "",
        f"- Requested as: `{query}` (matched by {how})",
        f"- Generated: {dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- Files: {len(b.roles)}",
        "",
        "Roles: **PART** = the requested part, complete. **COMPANION** = config / requirements the part reads at runtime. "
        "**USAGE** = tests/tools that call the part. **IMPORT** = files outside the part that the part imports directly "
        "(only that module, not its whole package) - context for how it talks to neighbouring segments. "
        "**VISION** = project-level overview.",
        "",
    ]
    for role in ROLE_RANK:
        files = b.of(role)
        if not files:
            continue
        lines += [f"## {role} ({len(files)})", ""]
        for f in files:
            note = b.notes.get(f)
            extra = ""
            if note and role != "PART":
                shown = sorted(note)
                extra = " - " + "; ".join(shown[:4]) + (f"; +{len(shown) - 4} more" if len(shown) > 4 else "")
            lines.append(f"- `{rel(f)}`{extra}")
        lines.append("")
    if b.missing:
        lines += ["## Not found in this checkout", ""] + [f"- `{m}`" for m in b.missing] + [""]
    if b.skipped:
        lines += ["## Skipped", ""] + [f"- `{p}` - {why}" for p, why in b.skipped] + [""]
    return "\n".join(lines)


# --------------------------------------------------------------------------------------- CLI
def print_targets(targets: list[Target]) -> None:
    print("Stages / parts:")
    for t in targets:
        if not t.key.startswith("pipeline_"):
            print(f"  {t.key:<14} {t.title}\n  {'':<14} aliases: {', '.join(t.aliases)}")
    print("\nSingle pipelines (name, number, or keyword):")
    for t in targets:
        if t.key.startswith("pipeline_"):
            kw = ", ".join(t.weak_aliases)
            print(f"  {t.title:<34} keywords: {kw}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="zip_up.py", description="Zip one part of the project plus essential context.")
    ap.add_argument("name", nargs="?", help="part to zip: retrieval | preprocessing | <pipeline> | pipelines | verdict | "
                                            "serving | validation | lib | tiling | crosscutting (fuzzy; see --list)")
    ap.add_argument("--list", action="store_true", help="show all targets and aliases")
    ap.add_argument("--out", type=Path, default=OUT_DIR_DEFAULT, help="output directory (default: dev_scripts/out)")
    ap.add_argument("--dry-run", action="store_true", help="print what would be zipped, write nothing")
    ap.add_argument("--no-vision", action="store_true", help="skip README / ARCHITECTURE / pyproject")
    ap.add_argument("--no-imports", action="store_true", help="skip the directly-imported outside files")
    ap.add_argument("--no-usage", action="store_true", help="skip tests/tools that call the part")
    ap.add_argument("--no-companions", action="store_true", help="skip config / requirements companions")
    ap.add_argument("--max-mb", type=float, default=5.0, help="skip any single file larger than this (default 5)")
    args = ap.parse_args(argv)

    targets = build_targets()
    if args.list:
        print_targets(targets)
        return 0
    if not args.name:
        ap.print_help()
        return 2

    target, cands, how = match_target(args.name, targets)
    if target is None:
        if cands:
            print(f"'{args.name}' is ambiguous. Did you mean:", file=sys.stderr)
            for c in cands:
                print(f"  - {c.title}   (use: {c.aliases[0]})", file=sys.stderr)
        else:
            print(f"No part matches '{args.name}'. Run with --list to see options.", file=sys.stderr)
            sugg = difflib.get_close_matches(norm(args.name), [norm(a) for t in targets for a in t.aliases], n=3)
            if sugg:
                print(f"Closest: {', '.join(sugg)}", file=sys.stderr)
        return 2
    print(f"'{args.name}' -> {target.title}  [{how}]")

    b = collect(target, vision=not args.no_vision, companions=not args.no_companions,
                usage=not args.no_usage, imports=not args.no_imports, max_mb=args.max_mb)
    if not b.of("PART"):
        print(f"Nothing found for this part. Missing: {', '.join(b.missing)}", file=sys.stderr)
        return 1

    total = sum(p.stat().st_size for p in b.roles)
    for role in ROLE_RANK:
        files = b.of(role)
        if files:
            print(f"  {role:<10} {len(files):>4} files  {human(sum(p.stat().st_size for p in files)):>9}")
    print(f"  {'TOTAL':<10} {len(b.roles):>4} files  {human(total):>9}")
    for m in b.missing:
        print(f"  (not in this checkout: {m})")
    for p, why in b.skipped:
        print(f"  (skipped {p}: {why})")

    if args.dry_run:
        for role in ROLE_RANK:
            for f in b.of(role):
                print(f"  [{role}] {rel(f)}")
        return 0

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out.parent / ".gitignore").write_text("out/\n") if not (args.out.parent / ".gitignore").exists() else None
    zpath = args.out / f"{target.key}.zip"
    root = f"{REPO.name}_{target.key}"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{root}/ZIP_MANIFEST.md", manifest(target, b, args.name, how))
        for f in sorted(b.roles, key=lambda p: (ROLE_RANK[b.roles[p]], rel(p))):
            z.write(f, f"{root}/{rel(f)}")
    print(f"\nWrote {zpath}  ({human(zpath.stat().st_size)})")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:      # e.g. `zip_up.py --list | head`
        sys.exit(0)
