"""
sar_debug command line.

  python -m sar_debug --demo --out demo_report.html                      # synthetic data, no network
  python -m sar_debug --bbox -118.5 33.9 -118.0 34.2 --level GUNW        # live search, open the newest granule
  python -m sar_debug --csv nisar_results.csv --bbox ... --open 2        # reuse a saved search, open 2 granules
  python -m sar_debug --file granule.h5 [--bbox ...]                     # one local file
  python -m sar_debug --url https://.../granule.h5 --bbox ... --probe    # one remote file + link speed probe
  python -m sar_debug --npz clip1.npz clip2.npz                          # clips from nisar_region.py / nisar_fast.py
  python -m sar_debug --engine ./Dancing_With_SARs                       # what the engine produced on disk (read-only)
  python -m sar_debug --selftest
"""
from __future__ import annotations

import argparse
import ast
import os
import re
import shlex
import sys
import tempfile
import traceback
import webbrowser
import zlib
from html.parser import HTMLParser

from . import __version__, analyze, anatomy, engine_view, report, retrieve, synthetic
from .model import Check


def parse_args(argv=None):
    ap = argparse.ArgumentParser(prog="python -m sar_debug", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bbox", type=float, nargs=4, metavar=("W", "S", "E", "N"), help="search box and read window (degrees)")
    ap.add_argument("--start"), ap.add_argument("--end")
    ap.add_argument("--level", default="GUNW", help="processing level(s), comma separated (default GUNW)")
    ap.add_argument("--maturity", choices=["PROVISIONAL", "BETA"])
    ap.add_argument("--max", type=int, default=250, help="max search hits (default 250)")
    ap.add_argument("--keep-all", action="store_true", help="keep older processing versions of a scene")
    ap.add_argument("--csv", help="reuse nisar_results.csv instead of searching")
    ap.add_argument("--no-search", action="store_true")
    ap.add_argument("--open", type=int, default=1, metavar="N", help="open the N newest search hits (0 = search only; default 1)")
    ap.add_argument("--granule", help="open hits whose id contains this text instead of the newest")
    ap.add_argument("--file", nargs="+", default=[], help="local .h5 file(s)")
    ap.add_argument("--url", nargs="+", default=[], help="remote https .h5 url(s)")
    ap.add_argument("--npz", nargs="+", default=[], help="clip(s) from nisar_region.py / nisar_fast.py")
    ap.add_argument("--engine", help="path to a Dancing_With_SARs checkout (read-only view)")
    ap.add_argument("--engine-clips", type=int, default=1, help="render this many clips found in <engine>/data/clips (default 1)")
    ap.add_argument("--layers", help="regex on HDF5 layer path (default: unwrapped, coherence, components, wrapped, ionosphere, mask)")
    ap.add_argument("--max-layers", type=int, default=24)
    ap.add_argument("--max-px", type=int, default=1_000_000, help="decimate each layer read to about this many pixels")
    ap.add_argument("--deep", action="store_true", help="also walk orbit/attitude/calibration groups (slower)")
    ap.add_argument("--probe", action="store_true", help="measure the link to the server (needs a remote file)")
    ap.add_argument("--out", default="sar_debug_report.html")
    ap.add_argument("--show", action="store_true", help="open the report in a browser when done")
    ap.add_argument("--demo", action="store_true", help="build a report from synthetic data (no network, no credentials)")
    ap.add_argument("--selftest", action="store_true")
    return ap.parse_args(argv)


# ----------------------------------------------------------------------------- orchestration

def inspect_h5(src, title, a, trace, fs, ctx, display="", idx=1):
    """Open one HDF5 source, walk it, read the chosen layers, compute stats."""
    insp = {"id": f"f{idx}", "title": title, "kind": "h5", "source": display or str(src), "reader": "", "anatomy": None,
            "layers": [], "stats": [], "notes": [], "loaded": set()}
    remote = isinstance(src, str) and src.startswith("http")
    try:
        with retrieve.H5Handle(src, trace, fs=fs, display=display or str(src)) as h:
            insp["reader"] = h.reader
            with trace.step("anatomy", f"walk {title}") as ev:
                an = anatomy.inspect_file(h.h5, trace, deep=a.deep)
                ev["detail"] = f"{an['stats']['datasets']} datasets, {len(an['rasters'])} rasters"
            insp["anatomy"] = an
            chosen, cut = retrieve.pick_rasters(an["rasters"], a.layers, a.max_layers)
            if cut:
                insp["notes"].append(f"{cut} more matching layer(s) not read (raise --max-layers)")
            if remote and not a.bbox:
                insp["notes"].append("remote file and no --bbox: layers were not read (a full read would pull the whole file). Pass --bbox to read a window.")
                ctx["checks"].append(Check("info", f"layers: {title}", "skipped: remote file needs --bbox"))
            elif not chosen:
                insp["notes"].append("no raster layers matched")
                ctx["checks"].append(Check("warn", f"layers: {title}", "no raster layers matched the selection"))
            else:
                notes = []
                insp["layers"] = retrieve.read_layers(h.h5, chosen, a.bbox, a.max_px, trace, source=title, notes=notes, default_epsg=an.get("default_epsg"))
                insp["notes"] += notes
                insp["loaded"] = {L.path for L in insp["layers"]}
                for n in notes:
                    ctx["checks"].append(Check("warn", f"read: {title}", n))
        ctx["checks"].append(Check("ok", f"open: {title}", f"{insp['reader']}; {len(insp['layers'])} layer image(s) read"))
    except Exception as ex:
        trace.note("DEBUG", traceback.format_exc())
        ctx["checks"].append(Check("fail", f"open: {title}", f"{type(ex).__name__}: {ex}"))
    return insp


def finish_inspection(insp, ctx):
    wl = ((insp.get("anatomy") or {}).get("facts") or {}).get("wavelength_m") or insp.get("wavelength")
    insp["wavelength"] = wl
    insp["stats"] = [analyze.layer_stats(L, wl) for L in insp["layers"]]
    for L, st in zip(insp["layers"], insp["stats"]):
        for n in st["notes"]:
            ctx["checks"].append(Check("warn", f"{insp['title']}: {L.label}", n))
    if insp.get("anatomy") and not wl and any(L.category == "unwrapped" for L in insp["layers"]):
        ctx["checks"].append(Check("warn", f"{insp['title']}: wavelength", "not found in the file: unwrapped phase cannot be converted to mm"))


def run(a):
    trace = retrieve.Trace()
    ctx = {"trace": trace, "checks": report.env_checks(), "inspections": [], "search": None, "granules": [], "engine": None,
           "probe": None, "cross": [], "args_text": " ".join(shlex.quote(x) for x in (sys.argv[1:] if sys.argv[1:] else []))}
    granules, info, bbox = [], {}, tuple(a.bbox) if a.bbox else None
    tmp = None
    targets = []                       # (title, source, display)
    scene = None

    # ---- demo data
    if a.demo:
        tmp = tempfile.mkdtemp(prefix="sar_debug_demo_")
        bbox = bbox or synthetic.DEMO_BBOX
        scene = synthetic.build_scene()
        granules, info = synthetic.fake_granules(bbox), {"found": 0, "dropped": 0, "capped": False, "source": "synthetic"}
        info["found"] = len(granules)
        try:
            import h5py  # noqa: F401
            path = os.path.join(tmp, "NISAR_synthetic_GUNW_demo.h5")
            synthetic.to_h5py_file(scene, path)
            targets.append(("synthetic GUNW (real HDF5)", path, path))
        except ImportError:
            targets.append(("synthetic GUNW (in-memory stand-in)", scene, "in-memory synthetic scene"))
        a.engine = a.engine or synthetic.make_engine_tree(os.path.join(tmp, "Dancing_With_SARs_demo"), scene)
        clip = os.path.join(tmp, "demo_clip.npz")
        synthetic.write_clip(scene, clip, bbox)
        a.npz = list(a.npz) + [clip]
        ctx["checks"].append(Check("info", "demo", "all data is synthetic. The fake engine folder contains one planted pipeline-independence violation so you can see the check fire. No network was used."))

    # ---- search
    elif not a.no_search and (a.csv or bbox):
        try:
            if a.csv:
                granules, info = retrieve.granules_from_csv(a.csv, trace)
            else:
                granules, info = retrieve.search_granules(bbox, a.start, a.end, a.level, a.maturity, a.max, a.keep_all, trace)
        except Exception as ex:
            trace.note("DEBUG", traceback.format_exc())
            ctx["checks"].append(Check("fail", "search", f"{type(ex).__name__}: {ex}"))
    if granules or (a.csv or a.demo):
        sa = analyze.analyze_search(granules, bbox, info)
        ctx.update(search=sa, granules=granules)
        ctx["checks"] += analyze.search_checks(sa)

    # ---- choose what to open
    for f in a.file:
        targets.append((os.path.basename(f), f, os.path.abspath(f)))
    for u in a.url:
        targets.append((os.path.basename(u), u, u))
    if granules and not a.demo and not a.file and not a.url and a.open > 0:
        pool = [g for g in granules if g.url]
        if a.granule:
            pool = [g for g in pool if a.granule in g.id]
        pool.sort(key=lambda g: (g.t1, g.t0), reverse=True)
        if not pool and a.granule:
            ctx["checks"].append(Check("warn", "open", f"no search hit contains '{a.granule}'"))
        for g in pool[:a.open]:
            targets.append((g.id[:40] + "...", g.url, g.url))

    # ---- open files
    fs = None
    if any(isinstance(t[1], str) and t[1].startswith("http") for t in targets):
        try:
            fs = retrieve.earthdata_fs(trace)
        except Exception as ex:
            trace.note("DEBUG", traceback.format_exc())
            ctx["checks"].append(Check("fail", "login", f"{type(ex).__name__}: {ex}"))
    for i, (title, src, disp) in enumerate(targets, 1):
        insp = inspect_h5(src, title, a, trace, fs, ctx, disp, i)
        finish_inspection(insp, ctx)
        ctx["inspections"].append(insp)

    if a.probe:
        url = next((t[1] for t in targets if isinstance(t[1], str) and t[1].startswith("http")), None)
        if not url:
            ctx["checks"].append(Check("info", "probe", "needs a remote file (--url, or a search hit)"))
        else:
            try:
                ctx["probe"] = retrieve.probe_link(url, trace)
            except Exception as ex:
                trace.note("DEBUG", traceback.format_exc())
                ctx["checks"].append(Check("fail", "probe", f"{type(ex).__name__}: {ex}"))

    # ---- clips
    clips = list(a.npz)
    eng = None
    if a.engine:
        try:
            eng = engine_view.scan(a.engine)
            eng["clip_inventory"] = {os.path.basename(p): engine_view.npz_inventory(p) for p in eng.get("clip_paths", [])[:20]}
            ctx["engine"] = eng
            if not eng["exists"]:
                ctx["checks"].append(Check("fail", "engine", f"{a.engine} is not a folder"))
            else:
                missing = [p for p, ok in eng["layout"] if not ok]
                ctx["checks"].append(Check("warn" if missing else "ok", "engine layout", f"{len(missing)} expected folder(s) missing" + (": " + ", ".join(missing[:5]) if missing else "")))
                if eng["independence"]:
                    ctx["checks"].append(Check("fail", "pipeline independence", f"{len(eng['independence'])} violation(s): " + "; ".join(eng["independence"][:2])))
                else:
                    ctx["checks"].append(Check("ok", "pipeline independence", "no pipeline imports a sibling pipeline"))
                stubs = sum(1 for p in eng["pipelines"] if p["model_stub"])
                ctx["checks"].append(Check("info", "pipelines", f"{len(eng['pipelines'])} folder(s), {stubs} look like stubs (heuristic)"))
                if not a.demo:
                    clips += eng.get("clip_paths", [])[:a.engine_clips]
        except Exception as ex:
            trace.note("DEBUG", traceback.format_exc())
            ctx["checks"].append(Check("fail", "engine", f"{type(ex).__name__}: {ex}"))
    for j, c in enumerate(clips, 1):
        insp = {"id": f"c{j}", "title": os.path.basename(c), "kind": "npz", "source": os.path.abspath(c), "reader": "numpy (npz)", "anatomy": None,
                "layers": [], "stats": [], "notes": [], "loaded": set()}
        try:
            insp["layers"], insp["meta"], notes = retrieve.load_npz(c, a.max_px, trace)
            insp["notes"] += notes
            ctx["checks"].append(Check("warn" if notes else "ok", f"clip: {insp['title']}", "; ".join(notes) or f"{len(insp['layers'])} layer image(s); grids: {len(insp['meta'].get('grids', []))}"))
        except Exception as ex:
            trace.note("DEBUG", traceback.format_exc())
            ctx["checks"].append(Check("fail", f"clip: {os.path.basename(c)}", f"{type(ex).__name__}: {ex}"))
        # wavelength for clips: take it from a file inspected in the same run if there is one
        insp["wavelength"] = next((i.get("wavelength") for i in ctx["inspections"] if i.get("wavelength")), None) or (synthetic.LAMBDA if a.demo else None)
        finish_inspection(insp, ctx)
        ctx["inspections"].append(insp)

    ctx["cross"] = analyze.cross_checks([L for i in ctx["inspections"] for L in i["layers"]])
    ctx["checks"] += ctx["cross"]
    if not (ctx["inspections"] or ctx["search"] or ctx["engine"]):
        ctx["checks"].append(Check("warn", "nothing to inspect", "give --bbox, --csv, --file, --url, --npz, --engine or --demo"))
    return report.build(ctx), ctx


# ----------------------------------------------------------------------------- selftest

class _Balance(HTMLParser):
    VOID = {"meta", "br", "hr", "img", "input", "link", "col", "stop", "rect", "line", "path", "circle", "use"}

    def __init__(self):
        super().__init__()
        self.stack, self.errors = [], []

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"unexpected </{tag}> (open: {self.stack[-3:]})")
            if tag in self.stack:
                while self.stack and self.stack.pop() != tag:
                    pass
        else:
            self.stack.pop()


def _png_ok(b64):
    import base64
    raw = base64.b64decode(b64)
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        return False
    w, h = int.from_bytes(raw[16:20], "big"), int.from_bytes(raw[20:24], "big")
    pos, idat = 8, b""
    while pos < len(raw):
        n = int.from_bytes(raw[pos:pos + 4], "big")
        tag, data = raw[pos + 4:pos + 8], raw[pos + 8:pos + 8 + n]
        if int.from_bytes(raw[pos + 8 + n:pos + 12 + n], "big") != (zlib.crc32(tag + data) & 0xFFFFFFFF):
            return False
        if tag == b"IDAT":
            idat += data
        pos += 12 + n
    return len(zlib.decompress(idat)) == h * (1 + w * 4)


def independence_scan() -> list:
    """No module of this package may import the engine."""
    bad = []
    here = os.path.dirname(os.path.abspath(__file__))
    for fn in sorted(os.listdir(here)):
        if not fn.endswith(".py"):
            continue
        for n in ast.walk(ast.parse(open(os.path.join(here, fn), encoding="utf-8").read())):
            mods = [x.name for x in n.names] if isinstance(n, ast.Import) else ([n.module or ""] if isinstance(n, ast.ImportFrom) and n.level == 0 else [])
            bad += [f"{fn}: imports {m}" for m in mods if m.split(".")[0] == "engine"]
    return bad


def selftest() -> int:
    fails = []

    def check(ok, msg):
        print(("PASS " if ok else "FAIL ") + msg)
        if not ok:
            fails.append(msg)

    check(not independence_scan(), "package imports nothing from engine/")
    out = os.path.join(tempfile.mkdtemp(prefix="sar_debug_test_"), "r.html")
    a = parse_args(["--demo", "--out", out])
    html, ctx = run(a)
    open(out, "w", encoding="utf-8").write(html)
    check(len(html) > 50_000, f"report written ({len(html) / 1e6:.2f} MB)")
    bal = _Balance()
    bal.feed(html)
    check(not bal.errors and not bal.stack, "HTML tags balance" + (f": {bal.errors[:2]} {bal.stack[:3]}" if bal.errors or bal.stack else ""))
    imgs = re.findall(r'src="data:image/png;base64,([A-Za-z0-9+/=]+)"', html)
    check(len(imgs) >= 8 and all(_png_ok(i) for i in imgs), f"{len(imgs)} embedded PNGs decode and have the right size")
    for sid in ("health", "retrieval", "search", "engine", "cross"):
        check(f'id="{sid}"' in html, f"section '{sid}' present")
    check(any(i["kind"] == "h5" and i["layers"] for i in ctx["inspections"]), "HDF5 path produced layers")
    check(any(i["kind"] == "npz" and i["layers"] for i in ctx["inspections"]), "npz clip path produced layers")
    check(any(c.name == "pipeline independence" and c.status == "fail" for c in ctx["checks"]), "planted pipeline-independence violation is detected")
    h5i = next(i for i in ctx["inspections"] if i["kind"] == "h5")
    unw = next(s for L, s in zip(h5i["layers"], h5i["stats"]) if L.category == "unwrapped")
    check(any("plane" in k for k, _ in unw["extra"]), "unwrapped-phase ramp analysis ran")
    check("<script>alert" not in html, "no unescaped script injection")
    # escaping: hostile text must not survive as markup
    from .render import esc, table
    check("<b>" not in str(table(["h"], [["<b>x</b>"]])), "table cells are escaped")
    print("\nselftest:", "OK" if not fails else f"{len(fails)} FAILED")
    return 0 if not fails else 1


def main(argv=None):
    a = parse_args(argv)
    if a.selftest:
        return selftest()
    html, ctx = run(a)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(html)
    n = {k: sum(1 for c in ctx["checks"] if c.status == k) for k in ("fail", "warn", "ok", "info")}
    print(f"wrote {os.path.abspath(a.out)} ({len(html) / 1e6:.2f} MB): {n['fail']} fail, {n['warn']} warn, {n['ok']} ok, {n['info']} info")
    for c in ctx["checks"]:
        if c.status in ("fail", "warn"):
            print(f"  [{c.status.upper()}] {c.name}: {c.detail}")
    if a.show:
        webbrowser.open("file://" + os.path.abspath(a.out))
    return 1 if (n["fail"] and not a.demo) else 0     # the demo plants one failure on purpose


if __name__ == "__main__":
    sys.exit(main())
