"""
Rendering helpers: PNG encoder, colormaps, SVG charts and the HTML page shell.
numpy is the only dependency (no matplotlib, no PIL), so the report builds anywhere.
"""
from __future__ import annotations

import base64
import html as _html
import math
import struct
import warnings
import zlib
from datetime import datetime, timezone

import numpy as np


class Raw(str):
    """A string that is already safe HTML and must not be escaped again."""


def esc(x) -> str:
    if isinstance(x, Raw):
        return str(x)
    return _html.escape("" if x is None else str(x), quote=True)


def fmt(v, digits=4):
    """Compact number formatting that never raises."""
    if v is None:
        return "-"
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, int):
        return f"{v:,}"
    if isinstance(v, float):
        if not math.isfinite(v):
            return "nan" if v != v else ("inf" if v > 0 else "-inf")
        if v == 0:
            return "0"
        a = abs(v)
        if a >= 1e6 or a < 1e-3:
            return f"{v:.{digits - 1}e}"
        return f"{v:,.{digits}g}" if a < 1e4 else f"{v:,.0f}"
    return str(v)


# ----------------------------------------------------------------------------- PNG

def png_bytes(rgba) -> bytes:
    rgba = np.ascontiguousarray(rgba, dtype=np.uint8)
    h, w, _ = rgba.shape
    raw = np.concatenate([np.zeros((h, 1), np.uint8), rgba.reshape(h, w * 4)], axis=1).tobytes()

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def data_uri(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


# ----------------------------------------------------------------------------- colormaps

_ANCHORS = {
    "viridis": [(0, (68, 1, 84)), (.125, (71, 44, 122)), (.25, (59, 81, 139)), (.375, (44, 113, 142)),
                (.5, (33, 144, 141)), (.625, (39, 173, 129)), (.75, (92, 200, 99)), (.875, (170, 220, 50)),
                (1, (253, 231, 37))],
    "diverging": [(0, (45, 80, 190)), (.25, (120, 160, 225)), (.5, (238, 238, 238)),
                  (.75, (232, 150, 120)), (1, (178, 24, 43))],
    "magma": [(0, (0, 0, 4)), (.25, (80, 18, 123)), (.5, (182, 54, 121)), (.75, (251, 136, 97)),
              (1, (252, 253, 191))],
    "cyclic": [(0, (200, 60, 60)), (.25, (210, 190, 60)), (.5, (50, 170, 130)), (.75, (70, 100, 210)),
               (1, (200, 60, 60))],
    "gray": [(0, (15, 15, 15)), (1, (240, 240, 240))],
}
_CAT = [(78, 121, 167), (242, 142, 43), (89, 161, 79), (225, 87, 89), (176, 122, 161), (237, 201, 72),
        (118, 183, 178), (255, 157, 167), (156, 117, 95), (186, 176, 172), (31, 119, 180), (148, 103, 189)]
_LUTS: dict = {}


def lut(name: str) -> np.ndarray:
    if name not in _LUTS:
        pts = _ANCHORS[name]
        xs = [p[0] for p in pts]
        t = np.linspace(0, 1, 256)
        _LUTS[name] = np.stack([np.interp(t, xs, [p[1][c] for p in pts]) for c in range(3)], 1).astype(np.uint8)
    return _LUTS[name]


def colorize(a, cmap: str, vmin: float, vmax: float) -> np.ndarray:
    a = np.asarray(a, np.float32)
    ok = np.isfinite(a)
    span = (vmax - vmin) if vmax > vmin else 1.0
    t = np.clip(np.nan_to_num((a - vmin) / span, nan=0.0, posinf=1.0, neginf=0.0), 0, 1)
    idx = (t * 255).astype(np.uint8)
    out = np.zeros(a.shape + (4,), np.uint8)
    out[..., :3] = lut(cmap)[idx]
    out[..., 3] = np.where(ok, 255, 0)
    return out


def colorize_cat(a) -> np.ndarray:
    a = np.asarray(a, np.float32)
    ok = np.isfinite(a)
    lab = np.nan_to_num(a, nan=0.0).astype(np.int64)
    pal = np.array(_CAT, np.uint8)
    out = np.zeros(a.shape + (4,), np.uint8)
    out[..., :3] = pal[(np.maximum(lab, 1) - 1) % len(pal)]
    zero = ok & (lab <= 0)
    out[zero, :3] = (35, 35, 40)
    out[..., 3] = np.where(ok, 255, 0)
    return out


def shrink(a, maxdim: int, mode: str = "mean") -> np.ndarray:
    """Reduce a 2-D array so its longest side is <= maxdim (block mean, or stride for categorical)."""
    a = np.asarray(a)
    h, w = a.shape
    step = max(1, math.ceil(max(h, w) / maxdim))
    if step == 1:
        return a
    if mode != "mean" or h < step or w < step:
        return a[::step, ::step]
    hh, ww = h // step * step, w // step * step
    b = a[:hh, :ww].astype(np.float32).reshape(hh // step, step, ww // step, step)
    with np.errstate(all="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return np.nanmean(b, axis=(1, 3)).astype(np.float32)


# ----------------------------------------------------------------------------- SVG pieces

def _nice_ticks(lo, hi, n=5):
    if not (math.isfinite(lo) and math.isfinite(hi)) or hi <= lo:
        return [lo]
    raw = (hi - lo) / max(1, n)
    mag = 10 ** math.floor(math.log10(raw))
    step = min((m * mag for m in (1, 2, 5, 10) if m * mag >= raw), default=raw)
    first = math.ceil(lo / step) * step
    out, v = [], first
    while v <= hi + 1e-12 * abs(hi) + 1e-300:
        out.append(round(v, 12))
        v += step
    return out


def svg_colorbar(cmap: str, vmin: float, vmax: float, unit: str = "", w: int = 220) -> Raw:
    stops = "".join(f'<stop offset="{i / 8:.3f}" stop-color="rgb{tuple(int(c) for c in lut(cmap)[int(i / 8 * 255)])}"/>'
                    for i in range(9))
    gid = f"g{abs(hash((cmap, vmin, vmax, unit))) % 10**8}"
    mid = (vmin + vmax) / 2
    return Raw(
        f'<svg class="cbar" viewBox="0 0 {w} 34" width="{w}" height="34" role="img" aria-label="colour scale">'
        f'<defs><linearGradient id="{gid}" x1="0" x2="1">{stops}</linearGradient></defs>'
        f'<rect x="1" y="2" width="{w - 2}" height="10" fill="url(#{gid})" stroke="var(--line)"/>'
        f'<text x="1" y="26" class="tick">{esc(fmt(vmin, 3))}</text>'
        f'<text x="{w / 2}" y="26" class="tick" text-anchor="middle">{esc(fmt(mid, 3))}</text>'
        f'<text x="{w - 1}" y="26" class="tick" text-anchor="end">{esc(fmt(vmax, 3))}</text>'
        f'<text x="{w / 2}" y="34" class="tick unit" text-anchor="middle">{esc(unit)}</text></svg>')


def svg_hist(counts, lo: float, hi: float, unit: str = "", w: int = 300, h: int = 96) -> Raw:
    counts = np.asarray(counts, float)
    if counts.size == 0 or counts.max() <= 0:
        return Raw('<div class="muted">no data</div>')
    n, cmax = len(counts), counts.max()
    pad_l, pad_b, pad_t = 4, 18, 4
    bw = (w - pad_l - 4) / n
    bars = "".join(
        f'<rect class="bar" x="{pad_l + i * bw:.2f}" y="{pad_t + (h - pad_b - pad_t) * (1 - c / cmax):.2f}" '
        f'width="{max(bw - 0.6, 0.5):.2f}" height="{(h - pad_b - pad_t) * c / cmax:.2f}"/>'
        for i, c in enumerate(counts))
    ticks = "".join(
        f'<text class="tick" x="{pad_l + (t - lo) / (hi - lo) * (w - pad_l - 4):.1f}" y="{h - 5}" text-anchor="middle">{esc(fmt(t, 3))}</text>'
        for t in _nice_ticks(lo, hi, 4) if hi > lo)
    return Raw(f'<svg class="hist" viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="histogram {esc(unit)}">'
               f'{bars}<line class="axis" x1="{pad_l}" x2="{w - 4}" y1="{h - pad_b}" y2="{h - pad_b}"/>{ticks}</svg>')


def svg_bars(labels, values, w=420, h=130, color_class="bar") -> Raw:
    """Simple vertical bar chart with text labels (used for baseline histograms)."""
    values = [float(v) for v in values]
    if not values or max(values) <= 0:
        return Raw('<div class="muted">no data</div>')
    n, vmax = len(values), max(values)
    pad_l, pad_b, pad_t = 6, 28, 12
    bw = (w - pad_l - 6) / n
    out = []
    for i, (lab, v) in enumerate(zip(labels, values)):
        bh = (h - pad_b - pad_t) * v / vmax
        x = pad_l + i * bw
        out.append(f'<rect class="{color_class}" x="{x + 1:.1f}" y="{h - pad_b - bh:.1f}" width="{max(bw - 2, 1):.1f}" height="{bh:.1f}"/>'
                   f'<text class="tick" x="{x + bw / 2:.1f}" y="{h - pad_b - bh - 2:.1f}" text-anchor="middle">{esc(fmt(v, 3))}</text>'
                   f'<text class="tick" x="{x + bw / 2:.1f}" y="{h - 14}" text-anchor="middle">{esc(lab)}</text>')
    return Raw(f'<svg viewBox="0 0 {w} {h}" width="100%" role="img">{"".join(out)}'
               f'<line class="axis" x1="{pad_l}" x2="{w - 4}" y1="{h - pad_b}" y2="{h - pad_b}"/></svg>')


def _pack_rows(intervals, cap=48):
    """Greedy interval packing: returns (row index per interval, rows used)."""
    order = sorted(range(len(intervals)), key=lambda i: intervals[i][0])
    ends, rows = [], [0] * len(intervals)
    for i in order:
        a, b = intervals[i]
        for r, e in enumerate(ends):
            if a > e:
                ends[r] = b
                rows[i] = r
                break
        else:
            if len(ends) < cap:
                ends.append(b)
                rows[i] = len(ends) - 1
            else:
                rows[i] = cap - 1
    return rows, max(1, len(ends))


def svg_timeline(lanes, w=900) -> Raw:
    """lanes: [(label, [(day_t0, day_t1, tooltip, css_class)])] with times as ordinal days."""
    allt = [t for _, items in lanes for it in items for t in it[:2]]
    if not allt:
        return Raw('<div class="muted">no granules</div>')
    t0, t1 = min(allt), max(allt)
    if t1 <= t0:
        t1 = t0 + 1
    left, right, top = 170, 14, 22
    plot_w = w - left - right
    X = lambda t: left + (t - t0) / (t1 - t0) * plot_w
    y, body = top, []
    for label, items in lanes:
        rows, nrows = _pack_rows([(a, b) for a, b, *_ in items])
        lane_h = nrows * 6 + 10
        body.append(f'<rect class="lane" x="{left}" y="{y}" width="{plot_w}" height="{lane_h}"/>'
                    f'<text class="lbl" x="{left - 8}" y="{y + lane_h / 2 + 4}" text-anchor="end">{esc(label)}</text>')
        for (a, b, tip, cls), r in zip(items, rows):
            yy = y + 5 + r * 6
            xa, xb = X(a), max(X(b), X(a) + 2)
            body.append(f'<g><title>{esc(tip)}</title><rect class="{esc(cls)}" x="{xa:.1f}" y="{yy}" width="{xb - xa:.1f}" height="4" rx="1.5"/></g>')
        y += lane_h + 6
    ticks = []
    for t in _nice_ticks(t0, t1, 8):
        d = datetime.fromordinal(int(t)).strftime("%Y-%m-%d")
        ticks.append(f'<line class="grid" x1="{X(t):.1f}" x2="{X(t):.1f}" y1="{top - 4}" y2="{y}"/>'
                     f'<text class="tick" x="{X(t):.1f}" y="12" text-anchor="middle">{d}</text>')
    return Raw(f'<svg class="timeline" viewBox="0 0 {w} {y + 6}" width="100%" role="img" aria-label="granule timeline">'
               f'{"".join(ticks)}{"".join(body)}</svg>')


def svg_footprints(polys, bbox=None, w=560, h=380) -> Raw:
    """polys: [(label, ring[(lon,lat)], css_class)]; bbox: (W,S,E,N). Equirectangular, graticule only."""
    pts = [p for _, ring, _ in polys for p in ring]
    if bbox:
        pts += [(bbox[0], bbox[1]), (bbox[2], bbox[3])]
    if not pts:
        return Raw('<div class="muted">no footprints</div>')
    lons, lats = [p[0] for p in pts], [p[1] for p in pts]
    lo_x, hi_x, lo_y, hi_y = min(lons), max(lons), min(lats), max(lats)
    padx, pady = max((hi_x - lo_x) * .08, .02), max((hi_y - lo_y) * .08, .02)
    lo_x, hi_x, lo_y, hi_y = lo_x - padx, hi_x + padx, lo_y - pady, hi_y + pady
    k = math.cos(math.radians((lo_y + hi_y) / 2))
    span_x, span_y = (hi_x - lo_x) * k, hi_y - lo_y
    scale = min((w - 50) / span_x, (h - 30) / span_y)
    ox, oy = 40, 8
    X = lambda lon: ox + (lon - lo_x) * k * scale
    Y = lambda lat: oy + (hi_y - lat) * scale
    W_, H_ = ox + span_x * scale + 8, oy + span_y * scale + 22
    g = []
    for t in _nice_ticks(lo_x, hi_x, 5):
        g.append(f'<line class="grid" x1="{X(t):.1f}" x2="{X(t):.1f}" y1="{oy}" y2="{H_ - 22:.1f}"/>'
                 f'<text class="tick" x="{X(t):.1f}" y="{H_ - 8:.1f}" text-anchor="middle">{t:g}</text>')
    for t in _nice_ticks(lo_y, hi_y, 5):
        g.append(f'<line class="grid" x1="{ox}" x2="{W_ - 8:.1f}" y1="{Y(t):.1f}" y2="{Y(t):.1f}"/>'
                 f'<text class="tick" x="{ox - 4}" y="{Y(t) + 3:.1f}" text-anchor="end">{t:g}</text>')
    for label, ring, cls in polys:
        d = "M" + "L".join(f"{X(a):.1f},{Y(b):.1f}" for a, b in ring) + "Z"
        g.append(f'<g><title>{esc(label)}</title><path class="fp {esc(cls)}" d="{d}"/></g>')
    if bbox:
        w0, s0, e0, n0 = bbox
        g.append(f'<rect class="bbox" x="{X(w0):.1f}" y="{Y(n0):.1f}" width="{X(e0) - X(w0):.1f}" height="{Y(s0) - Y(n0):.1f}"/>')
    return Raw(f'<svg viewBox="0 0 {W_:.0f} {H_:.0f}" width="100%" role="img" aria-label="footprint map">{"".join(g)}</svg>')


def svg_waterfall(events, w=900) -> Raw:
    """events: [{'kind','name','t0','t1','status','bytes'}] -> one lane per kind, bars on a shared time axis."""
    ev = [e for e in events if e.get("t1") is not None]
    if not ev:
        return Raw('<div class="muted">no trace events</div>')
    t0, t1 = min(e["t0"] for e in ev), max(e["t1"] for e in ev)
    span = max(t1 - t0, 1e-6)
    kinds = []
    for e in ev:
        if e["kind"] not in kinds:
            kinds.append(e["kind"])
    left, right, top, lane_h = 110, 14, 20, 20
    plot_w = w - left - right
    X = lambda t: left + (t - t0) / span * plot_w
    body = []
    for i, k in enumerate(kinds):
        y = top + i * lane_h
        body.append(f'<rect class="lane" x="{left}" y="{y}" width="{plot_w}" height="{lane_h - 3}"/>'
                    f'<text class="lbl" x="{left - 8}" y="{y + 13}" text-anchor="end">{esc(k)}</text>')
    for e in ev:
        i = kinds.index(e["kind"])
        y = top + i * lane_h + 3
        xa, xb = X(e["t0"]), max(X(e["t1"]), X(e["t0"]) + 1.5)
        cls = "err" if e.get("status") == "error" else ("warnb" if e.get("status") == "warn" else "okb")
        tip = f'{e["name"]} | {e["t1"] - e["t0"]:.2f}s' + (f' | {e["bytes"] / 1e6:.2f} MB' if e.get("bytes") else "")
        body.append(f'<g><title>{esc(tip)}</title><rect class="{cls}" x="{xa:.1f}" y="{y}" width="{xb - xa:.1f}" height="{lane_h - 9}" rx="2"/></g>')
    ticks = "".join(f'<line class="grid" x1="{X(t0 + span * f):.1f}" x2="{X(t0 + span * f):.1f}" y1="{top - 3}" y2="{top + len(kinds) * lane_h}"/>'
                    f'<text class="tick" x="{X(t0 + span * f):.1f}" y="12" text-anchor="middle">{span * f:.1f}s</text>'
                    for f in (0, .25, .5, .75, 1))
    return Raw(f'<svg viewBox="0 0 {w} {top + len(kinds) * lane_h + 6}" width="100%" role="img" aria-label="retrieval trace">{ticks}{"".join(body)}</svg>')


# ----------------------------------------------------------------------------- HTML pieces

def badge(status: str, text: str = "") -> Raw:
    label = text or {"ok": "OK", "warn": "WARN", "fail": "FAIL", "info": "INFO"}.get(status, status)
    return Raw(f'<span class="badge {esc(status)}">{esc(label)}</span>')


def table(headers, rows, cls="", sortable=False) -> Raw:
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    cell = lambda c: f'<td class="w">{esc(c)}</td>' if len(str(c)) > 48 else f"<td>{esc(c)}</td>"
    body = "".join("<tr>" + "".join(cell(c) for c in r) + "</tr>" for r in rows)
    return Raw(f'<div class="tw"><table class="{esc(cls)}{" sortable" if sortable else ""}"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>')


def kv(pairs, cls="kv") -> Raw:
    return Raw(f'<dl class="{cls}">' + "".join(f"<dt>{esc(k)}</dt><dd>{esc(v)}</dd>" for k, v in pairs) + "</dl>")


def details(summary, body, open_=False) -> Raw:
    return Raw(f'<details{" open" if open_ else ""}><summary>{esc(summary)}</summary>{esc(body)}</details>')


def stat_cards(items) -> Raw:
    return Raw('<div class="cards">' + "".join(
        f'<div class="stat"><div class="n">{esc(v)}</div><div class="l">{esc(k)}</div></div>' for k, v in items) + "</div>")


def section(sid: str, title: str, body, note: str = "") -> str:
    n = f'<p class="note">{esc(note)}</p>' if note else ""
    return f'<section id="{esc(sid)}"><h2>{esc(title)}</h2>{n}{esc(body)}</section>'


CSS = """
:root{--bg:#fbfbfa;--fg:#1d2024;--muted:#6b7280;--card:#fff;--line:#d9dbe0;--accent:#3b6fb6;--ok:#2e7d4f;--warn:#b7791f;--fail:#c0392b;--info:#5b6b83;--lane:#f0f1f4;--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
@media (prefers-color-scheme:dark){:root{--bg:#14161a;--fg:#e6e8eb;--muted:#9aa3af;--card:#1c1f25;--line:#323741;--accent:#7aa7e6;--ok:#58c486;--warn:#e0b04a;--fail:#ef7b6e;--info:#98a6bd;--lane:#1f232a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
header.top{padding:20px 24px 8px}h1{margin:0 0 4px;font-size:22px}h2{font-size:17px;margin:0 0 10px;padding-top:4px}h3{font-size:14px;margin:16px 0 6px}
nav{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--line);padding:8px 24px;display:flex;gap:14px;flex-wrap:wrap;font-size:13px}
nav a{color:var(--accent);text-decoration:none}nav a:hover{text-decoration:underline}
main{padding:0 24px 60px;max-width:1280px}section{margin:26px 0;padding-top:6px;border-top:1px solid var(--line)}
.muted,.note{color:var(--muted)}.note{margin:0 0 10px}code,pre,.mono,td.n{font-family:var(--mono);font-size:12.5px}
pre{background:var(--card);border:1px solid var(--line);padding:10px;overflow:auto;border-radius:6px;max-height:340px}
.tw{overflow:auto;max-height:520px;border:1px solid var(--line);border-radius:6px;background:var(--card)}
table{border-collapse:collapse;width:100%;font-size:12.5px}th,td{padding:4px 9px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top;white-space:nowrap}
td.w{white-space:normal;word-break:break-word;min-width:260px}th{position:sticky;top:0;background:var(--lane);font-weight:600;cursor:default}table.sortable th{cursor:pointer}
.badge{display:inline-block;padding:0 7px;border-radius:9px;font-size:11px;font-weight:700;color:#fff;letter-spacing:.3px}
.badge.ok{background:var(--ok)}.badge.warn{background:var(--warn)}.badge.fail{background:var(--fail)}.badge.info{background:var(--info)}
.cards{display:flex;gap:10px;flex-wrap:wrap;margin:6px 0 12px}.stat{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px 14px;min-width:110px}
.stat .n{font-size:20px;font-weight:650;font-family:var(--mono)}.stat .l{font-size:12px;color:var(--muted)}
dl.kv{display:grid;grid-template-columns:max-content 1fr;gap:3px 14px;margin:0}dl.kv dt{color:var(--muted)}dl.kv dd{margin:0;font-family:var(--mono);font-size:12.5px;word-break:break-all}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:14px}.panel{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 12px}
.layers{display:grid;grid-template-columns:repeat(auto-fill,minmax(420px,1fr));gap:16px}
figure.lyr{margin:0;background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px}
figure.lyr img{width:100%;height:auto;display:block;border-radius:4px;background:repeating-conic-gradient(var(--lane) 0 25%,var(--card) 0 50%) 0 0/14px 14px;image-rendering:pixelated;cursor:crosshair}
figure.lyr figcaption{margin-top:8px}.lyr h4{margin:0 0 6px;font-size:13px;font-family:var(--mono);word-break:break-all}
.readout{font-family:var(--mono);font-size:12px;color:var(--muted);min-height:18px;margin-top:4px}
.warns{margin:6px 0 0;padding-left:18px;color:var(--warn)}.warns li{margin:2px 0}
details{margin:6px 0}summary{cursor:pointer;color:var(--accent)}
svg{max-width:100%}.tick{font-size:10px;fill:var(--muted);font-family:var(--mono)}.lbl{font-size:11px;fill:var(--fg);font-family:var(--mono)}.unit{font-style:italic}
.bar,.okb{fill:var(--accent)}.err{fill:var(--fail)}.warnb{fill:var(--warn)}.axis{stroke:var(--muted);stroke-width:1}.grid{stroke:var(--line);stroke-width:.6}.lane{fill:var(--lane)}
.fp{fill-opacity:.18;stroke-width:1.3}.bbox{fill:none;stroke:var(--fail);stroke-width:1.6;stroke-dasharray:5 3}
.c0{fill:#4e79a7;stroke:#4e79a7}.c1{fill:#f28e2b;stroke:#f28e2b}.c2{fill:#59a14f;stroke:#59a14f}.c3{fill:#e15759;stroke:#e15759}.c4{fill:#b07aa1;stroke:#b07aa1}.c5{fill:#edc948;stroke:#edc948}.c6{fill:#76b7b2;stroke:#76b7b2}.c7{fill:#ff9da7;stroke:#ff9da7}
.tree{font-family:var(--mono);font-size:12px}.tree details{margin:0 0 0 14px}.tree .ds{margin-left:28px;color:var(--muted)}.tree .ds b{color:var(--fg);font-weight:500}
input.filter{padding:5px 8px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--fg);margin:0 0 6px;min-width:260px}
footer{color:var(--muted);font-size:12px;padding:20px 24px;border-top:1px solid var(--line)}
"""

JS = """
(function(){
 document.querySelectorAll('figure.lyr[data-q]').forEach(function(f){
  var img=f.querySelector('img'),tip=f.querySelector('.readout');if(!img||!tip)return;
  var q=null,w=+f.dataset.w,h=+f.dataset.h,lo=+f.dataset.vmin,hi=+f.dataset.vmax,cat=f.dataset.mode==='cat',u=f.dataset.units||'';
  var c=f.dataset.c?JSON.parse(f.dataset.c):null,idle='hover the map for values';tip.textContent=idle;
  img.addEventListener('mousemove',function(e){
   if(!q){var s=atob(f.dataset.q);q=new Uint8Array(s.length);for(var i=0;i<s.length;i++)q[i]=s.charCodeAt(i);}
   var r=img.getBoundingClientRect(),uu=(e.clientX-r.left)/r.width,vv=(e.clientY-r.top)/r.height;
   var x=Math.max(0,Math.min(w-1,Math.floor(uu*w))),y=Math.max(0,Math.min(h-1,Math.floor(vv*h))),code=q[y*w+x],t;
   if(code===0)t='no data';else if(cat)t='label '+(code-1)+(code>=254?'+':'');
   else{var val=lo+(code-1)/254*(hi-lo);t=val.toPrecision(4)+(u?' '+u:'')+(code===1?' (<= range)':code===255?' (>= range)':'');}
   if(c){var lon=c[0][0]+uu*(c[1][0]-c[0][0])+vv*(c[2][0]-c[0][0]),lat=c[0][1]+uu*(c[1][1]-c[0][1])+vv*(c[2][1]-c[0][1]);t+='   |   '+lon.toFixed(4)+', '+lat.toFixed(4);}
   tip.textContent='px ('+x+','+y+')   '+t;});
  img.addEventListener('mouseleave',function(){tip.textContent=idle;});
 });
 document.querySelectorAll('input.filter').forEach(function(inp){
  inp.addEventListener('input',function(){var t=document.getElementById(inp.dataset.target);if(!t)return;var s=inp.value.toLowerCase();
   t.querySelectorAll('tbody tr').forEach(function(tr){tr.style.display=tr.textContent.toLowerCase().indexOf(s)>=0?'':'none';});});});
 document.querySelectorAll('table.sortable').forEach(function(t){
  t.querySelectorAll('th').forEach(function(th,i){th.addEventListener('click',function(){
   var b=t.tBodies[0],rows=Array.prototype.slice.call(b.rows),asc=th.dataset.asc!=='1';th.dataset.asc=asc?'1':'0';
   rows.sort(function(a,c){var x=a.cells[i].textContent.replace(/,/g,''),y=c.cells[i].textContent.replace(/,/g,''),nx=parseFloat(x),ny=parseFloat(y);
    var r=(!isNaN(nx)&&!isNaN(ny))?nx-ny:x.localeCompare(y);return asc?r:-r;});rows.forEach(function(r){b.appendChild(r);});});});});
})();
"""


def page(title: str, subtitle: str, nav, sections, footer: str = "") -> str:
    nav_html = "".join(f'<a href="#{esc(i)}">{esc(t)}</a>' for i, t in nav)
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{esc(title)}</title><style>{CSS}</style></head><body>'
            f'<header class="top"><h1>{esc(title)}</h1><div class="muted">{esc(subtitle)}</div></header>'
            f'<nav>{nav_html}</nav><main>{"".join(sections)}</main><footer>{esc(footer)}</footer>'
            f'<script>{JS}</script></body></html>')


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
