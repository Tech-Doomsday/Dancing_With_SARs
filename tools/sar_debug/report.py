"""
Builds the single-file HTML report from the results of retrieval + analysis.

`build(ctx)` takes a plain dict (see __main__.run) so the report can be rebuilt from saved results
and the rest of the package never has to know anything about HTML.
"""
from __future__ import annotations

import base64
import platform
import sys
from datetime import datetime

import numpy as np

from . import __version__, analyze, anatomy, geo
from .render import (Raw, badge, colorize, colorize_cat, data_uri, details, esc, fmt, kv, page, png_bytes, section,
                     shrink, stat_cards, svg_bars, svg_colorbar, svg_footprints, svg_hist, svg_timeline,
                     svg_waterfall, table, utc_now)

R = Raw
IMG_PX, HOVER_PX = 520, 320


def H(*parts) -> Raw:
    """Join parts; plain str is escaped, Raw passes through."""
    return Raw("".join(esc(p) for p in parts))


# ----------------------------------------------------------------------------- environment

def env_checks():
    from .model import Check
    out = []
    for mod, why in (("h5py", "needed to read .h5 granules"), ("fsspec", "remote range reads"), ("earthaccess", "Earthdata login for remote files"),
                     ("asf_search", "live ASF search (a saved nisar_results.csv works instead)"), ("pyproj", "any projection besides UTM"),
                     ("yaml", "parse pipeline manifests in the engine view"), ("dotenv", "optional: read credentials from .env")):
        try:
            m = __import__(mod)
            out.append(Check("ok", f"lib {mod}", getattr(m, "__version__", "installed")))
        except Exception:
            out.append(Check("info" if mod in ("pyproj", "dotenv", "yaml") else "warn", f"lib {mod}", f"not installed: {why}"))
    out.append(Check("info", "georeferencing", geo.backend()))
    return out


# ----------------------------------------------------------------------------- sections

def s_health(checks):
    order = {"fail": 0, "warn": 1, "info": 2, "ok": 3}
    cs = sorted(checks, key=lambda c: order.get(c.status, 9))
    n = {k: sum(1 for c in checks if c.status == k) for k in ("fail", "warn", "ok", "info")}
    cards = stat_cards([("fail", n["fail"]), ("warn", n["warn"]), ("ok", n["ok"]), ("info", n["info"])])
    rows = "".join(f'<tr><td>{badge(c.status)}</td><td>{esc(c.name)}</td><td class="w">{esc(c.detail)}</td></tr>' for c in cs)
    return section("health", "Health", H(cards, R(f'<div class="tw"><table><thead><tr><th>status</th><th>check</th><th>detail</th></tr></thead><tbody>{rows}</tbody></table></div>')),
                   "Everything the debug layer noticed, worst first. Thresholds marked 'heuristic' are debug aids, not acceptance criteria.")


def s_retrieval(trace, probe):
    ev = [e for e in trace.events if e["t1"] is not None]
    reqs = [e for e in ev if e["kind"] == "request"]
    total = (max((e["t1"] for e in ev), default=trace.t_start) - trace.t_start)
    mb = sum(e["bytes"] or 0 for e in reqs) / 1e6
    cards = [("events", len(ev)), ("wall time", f"{total:.1f} s"), ("server requests", len(reqs)), ("fetched", f"{mb:.1f} MB")]
    if reqs:
        slow = max(reqs, key=lambda e: e["t1"] - e["t0"])
        cards.append(("slowest request", f"{slow['t1'] - slow['t0']:.1f} s"))
    parts = [stat_cards(cards), svg_waterfall(ev)]
    if probe:
        rows = [[r["test"], r["status"], fmt(r.get("first_byte_s")), fmt(r.get("total_s")), fmt(r.get("mb")), fmt(r.get("mb_s"))] for r in probe["rows"]]
        parts += [R("<h3>Link probe</h3>"), table(["test", "status", "first byte s", "total s", "MB", "MB/s"], rows),
                  kv([("file size", f"{probe['file_size'] / 1e9:.2f} GB" if probe.get("file_size") else "unknown")] + [(k, v) for k, v in probe["headers"].items()])]
    t0 = trace.t_start
    rows = [[f"{e['t0'] - t0:.2f}", f"{e['t1'] - e['t0']:.2f}", e["kind"], e["name"], fmt(e["bytes"] / 1e6) if e["bytes"] else "", e["status"], e["detail"]] for e in ev[:600]]
    parts.append(details(f"All events ({len(ev)}{'; first 600 shown' if len(ev) > 600 else ''})",
                         table(["start s", "dur s", "kind", "name", "MB", "status", "detail"], rows)))
    lrows = [[f"{l['t'] - t0:.2f}", l["level"], l["msg"]] for l in trace.log[-400:]]
    parts.append(details(f"Log ({len(trace.log)} lines)", table(["t s", "level", "message"], lrows)))
    if trace.dropped:
        parts.append(R(f'<p class="note">{trace.dropped} further events were not recorded (cap reached).</p>'))
    return section("retrieval", "Retrieval trace", H(*parts),
                   "Every search, login, file open and byte-range request, with timing. Bars: blue ok, amber warn, red error.")


def s_search(sa, granules):
    if sa is None:
        return ""
    cards = stat_cards([("granules", sa["n"]), ("lanes", len(sa["lanes"])), ("tracks", len(sa["tracks"])), ("frames", len(sa["frames"])),
                        ("first", sa["first"] or "-"), ("last", sa["last"] or "-"),
                        ("total size", f"{sa['size_mb'] / 1000:.1f} GB" + ("+" if sa["size_known"] < sa["n"] else ""))])
    tracks = sorted({g.track for g in granules if g.track is not None}, key=str)
    cls = lambda g: f"c{tracks.index(g.track) % 8}" if g.track in tracks else "c0"
    lanes = []
    for r in sa["lanes"]:
        items = []
        for g in granules:
            if analyze.lane_key(g) != r["key"] or not g.t0:
                continue
            a, b = analyze._d(g.t0), analyze._d(g.t1 or g.t0)
            tip = f"{g.id}\n{g.t0[:10]} -> {g.t1[:10]}" + (f"\nbaseline {g.baseline_days:.0f} d" if g.baseline_days is not None else "")
            items.append((a, b, tip, cls(g)))
        lanes.append((r["label"], items))
    polys = [(f"{g.id}", ring, cls(g)) for g in granules for ring in (g.footprint or [])][:400]
    fp_note = "" if polys else "ASF returned no footprint geometry for these results (a CSV has none)."
    lane_rows = [[r["label"], r["n"], r["epochs"], r["first"], r["last"], r["span_days"], fmt(r["gap_med"]), fmt(r["gap_max"]),
                  f"{fmt(r['baseline_min'])}..{fmt(r['baseline_max'])}", r["pairs"], f"{r['net_nodes']} nodes / {r['net_edges']} edges", r["components"],
                  ", ".join(r["levels"])] for r in sa["lanes"]]
    ready = analyze.readiness(sa["lanes"])
    ready_rows = [[r["lane"], r["rule"], r["need"], "met" if r["ok"] else "NOT met", r["detail"]] for r in ready]
    g_rows = [[f"{g.t0[:10]} -> {g.t1[:10]}" if g.t1 and g.t1[:10] != g.t0[:10] else g.t0[:19], g.level, g.track, g.dir, g.frame,
               fmt(g.baseline_days), fmt(g.size_mb), g.pol, g.maturity, g.id] for g in sorted(granules, key=lambda g: g.t0, reverse=True)[:1500]]
    parts = [cards,
             R('<div class="panel"><h3>Timeline (one bar per product, reference to secondary date)</h3>'), svg_timeline(lanes), R("</div>"),
             R('<div class="grid2"><div class="panel"><h3>Footprints</h3>'), svg_footprints(polys, sa["bbox"] if sa.get("bbox") else None) if (polys or sa.get("bbox")) else R('<div class="muted">no geometry</div>'),
             R(f'<p class="note">{esc(fp_note)} Dashed red = search box. Graticule only, no basemap.</p></div>'),
             R('<div class="panel"><h3>Temporal baselines (days)</h3>'), svg_bars([l for l, _ in sa["baseline_hist"]], [v for _, v in sa["baseline_hist"]]),
             R("</div>"), R('<div class="panel"><h3>Mix</h3>'),
             kv([("levels", ", ".join(f"{k}: {v}" for k, v in sa["levels"].items())), ("maturity", ", ".join(f"{k}: {v}" for k, v in sa["maturity"].items())),
                 ("polarisation", ", ".join(f"{k}: {v}" for k, v in sa["pol"].items())), ("source", sa["source"]),
                 ("found / dropped", f"{sa['found']} / {sa['dropped']} older versions")]), R("</div></div>"),
             R("<h3>Lanes (track / direction / frame)</h3>"),
             table(["lane", "products", "epochs", "first", "last", "span d", "median gap d", "max gap d", "baselines d", "pairs", "largest network", "islands", "levels"], lane_rows, sortable=True),
             R("<h3>Stack-depth readiness</h3>"),
             R('<p class="note">Only rules written down in Dancing_With_SARs docs/ARCHITECTURE.md (sections 5 and 6). The other pipelines state no stack requirement there, so none is invented here.</p>'),
             table(["lane", "rule", "requirement", "result", "detail"], ready_rows, sortable=True),
             R('<h3>All products</h3><input class="filter" data-target="gtab" placeholder="filter: date, track, frame, level ...">'),
             R('<div id="gtab">'), table(["date", "level", "track", "dir", "frame", "baseline d", "MB", "pol", "maturity", "id"], g_rows, sortable=True), R("</div>")]
    return section("search", "Search results", H(*parts), "What the search returned and how complete the stacks look. No file was opened for this section.")


def _fmt_dims(t):
    return " x ".join(str(int(s)) for s in t)


def s_file(insp):
    a = insp.get("anatomy")
    if not a:
        return ""
    f = a["facts"]
    st = a["stats"]
    facts = [("source", insp["source"]), ("reader", insp["reader"]),
             ("groups / datasets", f"{st['groups']} / {st['datasets']}" + (" (tree truncated)" if st["truncated"] else "")),
             ("pruned subtrees", ", ".join(st["pruned"]) or "none"),
             ("walk time", f"{a['seconds']} s"),
             ("pass direction", f["pass_direction"]), ("look direction", f["look_direction"]), ("track / frame", f"{f['track']} / {f['frame']}"),
             ("polarisation", f["polarizations"]),
             ("centre frequency", f"{f['frequency_hz'] / 1e9:.4f} GHz" if f["frequency_hz"] else None),
             ("wavelength", f"{f['wavelength_m'] * 100:.3f} cm  ({f['wavelength_source']})" if f["wavelength_m"] else "not found"),
             ("ionosphere layers", f"{len(f['ionosphere_layers'])} (recorded in the file; not assumed applied)"),
             ("troposphere layers", f"{len(f['troposphere_layers'])} (recorded in the file; not assumed applied)"),
             ("solid-earth tide layers", f"{len(f['tide_layers'])} (recorded in the file; not assumed applied)")]
    grid_rows = []
    for gp, g in a["grids"].items():
        if "error" in g:
            grid_rows.append([gp, g["error"], "", "", "", ""])
            continue
        b = g["bounds"]
        grid_rows.append([gp, f"{g['nx']} x {g['ny']}", g["epsg"], f"{fmt(g['dx'])} x {fmt(g['dy'])}",
                          f"{b[0]:.4f}, {b[1]:.4f} .. {b[2]:.4f}, {b[3]:.4f}" if b else "no georeference (pyproj / unsupported EPSG)", f"{fmt(g['x0'])} .. {fmt(g['x1'])} | {fmt(g['y0'])} .. {fmt(g['y1'])}"])
    cat_rows = [[anatomy.CAT_LABEL.get(r["cat"], r["cat"]), r["path"], _fmt_dims(r["shape"]), r["dtype"], r["chunks"] or "contiguous",
                 r["compression"] or "none", fmt(r["bytes"] / 1e6) if r["bytes"] else "?", "loaded" if r["path"] in insp["loaded"] else ""] for r in a["rasters"]]
    pr_rows = [[k, v if not isinstance(v, list) else ", ".join(map(str, v))] for k, v in sorted(a["params"].items())]
    parts = [kv(facts), R("<h3>Grids</h3>"), table(["grid group", "pixels (x by y)", "EPSG", "spacing", "lon/lat extent", "projected extent"], grid_rows),
             R("<h3>Gridded layers</h3>"), table(["kind", "path", "shape", "dtype", "chunks", "compression", "MB uncompressed", ""], cat_rows, sortable=True),
             details(f"Key parameters ({len(pr_rows)})", table(["path", "value"], pr_rows)),
             details("Full tree", R(f'<div class="tree">{_tree_html(a["tree"], True)}</div>'))]
    title = f"File anatomy: {insp['title']}"
    return section(f"file-{insp['id']}", title, H(*parts))


def _tree_html(n, top=False):
    if n["type"] == "group":
        kids = "".join(_tree_html(c) for c in n["children"])
        n_ds = sum(1 for c in n["children"] if c["type"] == "ds")
        return f'<details{" open" if top else ""}><summary>{esc(n["name"])}/ <span class="muted">({len(n["children"])} children, {n_ds} datasets)</span></summary>{kids}</details>'
    if n["type"] == "ds":
        extra = f' chunks {n["chunks"]}' if n.get("chunks") else ""
        extra += f' {n["compression"]}' if n.get("compression") else ""
        return f'<div class="ds"><b>{esc(n["name"])}</b> {esc(_fmt_dims(n["shape"]) if n["shape"] else "scalar")} {esc(n["dtype"])}{esc(extra)}</div>'
    return f'<div class="ds">{esc(n["name"])} <span class="muted">{esc(n.get("detail", ""))}</span></div>'


# ----------------------------------------------------------------------------- layer cards

def _style(L, st):
    """colormap + display range + hover-quantisation range for one layer."""
    p = st.get("pct", {})
    if not p:
        return "gray", 0.0, 1.0, 0.0, 1.0
    q_lo, q_hi = p[0.5], p[99.5]
    lo, hi = p[2], p[98]
    if L.kind == "phase":
        return "cyclic", -np.pi, np.pi, -np.pi, np.pi
    if L.category == "coherence" and st["max"] <= 1.0001:
        return "viridis", 0.0, 1.0, 0.0, 1.0
    if L.kind == "amp":
        return "magma", lo, hi, q_lo, q_hi
    if L.category in ("unwrapped", "iono", "offsets") or (st["min"] < 0 < st["max"]):
        c = st["median"] if L.category == "unwrapped" else 0.0
        m = max(abs(hi - c), abs(lo - c)) or 1.0
        return "diverging", c - m, c + m, q_lo, q_hi
    return "viridis", lo, hi, q_lo, q_hi


def layer_card(L, st, wavelength):
    a = np.asarray(L.array, np.float32)
    if not st.get("pct"):
        return R(f'<figure class="lyr"><h4>{esc(L.label)}</h4><div class="muted">no valid pixels</div>'
                 f'<ul class="warns">{"".join(f"<li>{esc(n)}</li>" for n in st["notes"])}</ul></figure>')
    cat = L.kind == "cat"
    small = shrink(a, IMG_PX, "stride" if cat else "mean")
    if cat:
        rgba, cmap, vmin, vmax, qlo, qhi = colorize_cat(small), None, 0, 0, 0, 0
    else:
        cmap, vmin, vmax, qlo, qhi = _style(L, st)
        rgba = colorize(small, cmap, vmin, vmax)
    uri = data_uri(png_bytes(rgba))
    hov = shrink(a, HOVER_PX, "stride" if cat else "mean")
    ok = np.isfinite(hov)
    if cat:
        code = np.where(ok, 1 + np.minimum(np.nan_to_num(hov, nan=0).clip(0), 253), 0)
        qlo, qhi = 0.0, 253.0
    else:
        t = np.clip(np.nan_to_num((hov - qlo) / ((qhi - qlo) or 1.0), nan=0.0), 0, 1)
        code = np.where(ok, 1 + np.rint(t * 254), 0)
    q = base64.b64encode(code.astype(np.uint8).tobytes()).decode()
    corners = ""
    if L.corners:
        corners = ' data-c="' + esc("[" + ",".join(f"[{c[0]:.6f},{c[1]:.6f}]" for c in L.corners) + "]") + '"'
    units = L.units or ("rad" if L.category == "unwrapped" or L.kind == "phase" else "")
    attrs = (f' data-q="{q}" data-w="{hov.shape[1]}" data-h="{hov.shape[0]}" data-vmin="{qlo}" data-vmax="{qhi}" '
             f'data-mode="{"cat" if cat else "num"}" data-units="{esc(units)}"{corners}')
    meta = [f"{L.dtype} {_fmt_dims(L.full_shape)} in file", f"window {L.window[1] - L.window[0]} x {L.window[3] - L.window[2]} px" if L.window else "",
            f"every {L.step}th px" if L.step > 1 else "full resolution", f"slice {L.nz // 2} of {L.nz}" if L.nz else "",
            f"read {L.secs:.1f}s" if L.secs else "", f"{L.remote_bytes / 1e6:.1f} MB fetched" if L.remote_bytes else ""]
    meta = " | ".join(m for m in meta if m)
    bar = svg_colorbar(cmap, vmin, vmax, units) if cmap else R('<div class="muted">colours = component / mask label</div>')
    rows = [("valid", f"{st['valid'] * 100:.1f}%"), ("min / p2 / median", f"{fmt(st['min'])} / {fmt(st['pct'][2])} / {fmt(st['median'])}"),
            ("p98 / max", f"{fmt(st['pct'][98])} / {fmt(st['max'])}"), ("mean / std / MAD-std", f"{fmt(st['mean'])} / {fmt(st['std'])} / {fmt(st['mad_std'])}")] + list(st["extra"])
    hist = svg_hist(st["hist"][0], st["hist"][1], st["hist"][2], units) if st.get("hist") else R("")
    warns = f'<ul class="warns">{"".join(f"<li>{esc(n)}</li>" for n in st["notes"])}</ul>' if st["notes"] else ""
    return R(f'<figure class="lyr"{attrs}><h4>{esc(L.label)}</h4><div class="muted mono" style="font-size:11px;word-break:break-all">{esc(L.path)}</div>'
             f'<img src="{uri}" alt="{esc(L.label)}"><div class="readout"></div>{bar}'
             f'<figcaption><div class="muted" style="font-size:12px">{esc(meta)}</div>{kv(rows)}{hist}{warns}</figcaption></figure>')


def s_layers(insp):
    if not insp["layers"]:
        return ""
    wl = (insp.get("anatomy") or {}).get("facts", {}).get("wavelength_m") or insp.get("wavelength")
    groups = {}
    for L, st in zip(insp["layers"], insp["stats"]):
        groups.setdefault(L.category, []).append((L, st))
    parts = []
    for cat in anatomy.CAT_ORDER:
        if cat not in groups:
            continue
        parts.append(R(f'<h3>{esc(anatomy.CAT_LABEL[cat])} <span class="muted" style="font-weight:400">: {esc(anatomy.CAT_NOTE[cat])}</span></h3>'))
        parts.append(R('<div class="layers">'))
        parts += [layer_card(L, st, wl) for L, st in groups[cat]]
        parts.append(R("</div>"))
    for n in insp["notes"]:
        parts.append(R(f'<p class="note">{esc(n)}</p>'))
    return section(f"layers-{insp['id']}", f"Layers: {insp['title']}", H(*parts),
                   "Hover over a map for the pixel value and lon/lat. Layers are windowed to the search box and decimated (block mean) for display; statistics use the decimated read.")


def s_cross(checks):
    if not checks:
        return ""
    rows = "".join(f'<tr><td>{badge(c.status)}</td><td>{esc(c.name)}</td><td class="w">{esc(c.detail)}</td></tr>' for c in checks)
    return section("cross", "Cross-layer checks",
                   R(f'<div class="tw"><table><thead><tr><th>status</th><th>check</th><th>detail</th></tr></thead><tbody>{rows}</tbody></table></div>'),
                   "Relations between layers of the same window. They test whether the layers agree with each other, not whether the physics is right.")


def s_engine(e):
    if e is None:
        return ""
    if not e["exists"]:
        return section("engine", "Engine view (read-only)", R(f'<p>{esc(e["root"])} is not a folder.</p>'))
    parts = [kv([("root", e["root"]), ("mode", "files only: nothing is imported or executed")])]
    parts += [R("<h3>Layout</h3>"), table(["expected path", "present"], [[p, "yes" if ok else "MISSING"] for p, ok in e["layout"]])]
    pr = []
    for p in e["pipelines"]:
        miss = [f for f, ok in p["files"].items() if not ok and f != "lib_local.py"]   # lib_local.py is optional (ARCHITECTURE.md)
        man = p["manifest"] or {}
        pr.append([p["name"], f"{7 - len(miss)}/7", ", ".join(miss) or "-", p["model_lines"] if p["model_lines"] is not None else "-",
                   "stub?" if p["model_stub"] else ("implemented?" if p["model_stub"] is False else "-"), man.get("applies", ""), man.get("method", "")])
    parts += [R("<h3>Pipelines</h3>"), R('<p class="note">"stub?" is a heuristic: model.py is tiny, or contains NotImplementedError / TODO / a bare pass.</p>'),
              table(["pipeline", "files", "missing", "model.py lines", "status (heuristic)", "applies", "method"], pr, sortable=True) if pr else R('<p class="muted">no engine/pipelines folder</p>')]
    parts += [R("<h3>Common preprocessing chain P01..P13</h3>"),
              table(["step", "file", "lines", "status (heuristic)"], [[c["name"], "yes" if c["exists"] else "MISSING", c["lines"] if c["lines"] is not None else "-", "stub?" if c["stub"] else ("implemented?" if c["stub"] is False else "-")] for c in e["common"]]),
              R("<h3>Verdict steps</h3>"),
              table(["step", "file", "lines", "status (heuristic)"], [[c["name"], "yes" if c["exists"] else "MISSING", c["lines"] if c["lines"] is not None else "-", "stub?" if c["stub"] else ("implemented?" if c["stub"] is False else "-")] for c in e["verdict"]]),
              R("<h3>Pipeline independence (static, by parsing imports)</h3>"),
              R('<p class="ok-line">' + (esc("no pipeline imports a sibling pipeline") if not e["independence"] else "") + "</p>") if not e["independence"] else
              table(["violation"], [[v] for v in e["independence"]])]
    d = e["data"]
    parts += [R("<h3>Data folders</h3>"), table(["folder", "present", "files", "MB"], [[k, "yes" if v["exists"] else "no", v["count"], fmt(v["bytes"] / 1e6)] for k, v in d.items()])]
    for k, v in d.items():
        if v["files"]:
            parts.append(details(f"{k}/ ({v['count']} files)", table(["file", "bytes"], [[f, s] for f, s in v["files"][:100]])))
    for name, items in (e.get("clip_inventory") or {}).items():
        parts.append(details(f"clip {name}", table(["key", "shape", "dtype"], [[k, _fmt_dims(s), t] for k, s, t in items[:200]])))
    db = e["verdicts_db"]
    if db:
        parts.append(R("<h3>verdicts.db</h3>"))
        if db["error"]:
            parts.append(R(f'<p class="warns">{esc(db["error"])}</p>'))
        for t in db["tables"]:
            parts.append(details(f"{t['name']}: {t['rows']} row(s), {len(t['columns'])} column(s)", table(t["columns"], t["sample"])))
        if not db["tables"] and not db["error"]:
            parts.append(R('<p class="muted">database has no tables yet</p>'))
    for fn, txt in e["config"].items():
        parts.append(details(f"config/{fn}", R(f"<pre>{esc(txt)}</pre>")))
    return section("engine", "Engine view (read-only)", H(*parts),
                   "What the engine has produced on disk, read without importing any engine code.")


# ----------------------------------------------------------------------------- assemble

def build(ctx: dict) -> str:
    checks = list(ctx["checks"])
    sections, nav = [], [("health", "Health")]
    sections.append(s_health(checks))
    sections.append(s_retrieval(ctx["trace"], ctx.get("probe")))
    nav.append(("retrieval", "Retrieval"))
    if ctx.get("search") is not None:
        sections.append(s_search(ctx["search"], ctx["granules"]))
        nav.append(("search", "Search"))
    for insp in ctx["inspections"]:
        if insp.get("anatomy"):
            sections.append(s_file(insp))
            nav.append((f"file-{insp['id']}", "File: " + insp["title"][:28]))
        if insp["layers"]:
            sections.append(s_layers(insp))
            nav.append((f"layers-{insp['id']}", "Layers: " + insp["title"][:28]))
    if ctx.get("cross"):
        sections.append(s_cross(ctx["cross"]))
        nav.append(("cross", "Cross-checks"))
    if ctx.get("engine") is not None:
        sections.append(s_engine(ctx["engine"]))
        nav.append(("engine", "Engine"))
    sub = (f"generated {utc_now()} | sar_debug {__version__} | python {platform.python_version()} | "
           f"{ctx.get('args_text', '')}")
    foot = ("sar_debug is independent of the hazard engine: it imports nothing from engine/ and only reads its files. "
            "Reproduce with: " + ctx.get("args_text", ""))
    return page("SAR debug report", sub, nav, sections, foot)
