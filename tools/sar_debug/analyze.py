"""
Analysis layer: turns what retrieval returned into numbers a human can judge.

  analyze_search()   what the search found: lanes (track/frame/direction), epochs, pair network, baselines
  readiness()        which documented stack-depth rules are met (from Dancing_With_SARs docs/ARCHITECTURE.md)
  layer_stats()      per-raster statistics + category-specific integrity checks
  cross_checks()     relations between layers read for the same window (gate overlap, wrapped vs unwrapped)

Thresholds used for warnings are DEBUG HEURISTICS, labelled as such in the report. They flag things
worth a look; they are not scientific acceptance criteria.
"""
from __future__ import annotations

import math
from collections import Counter
from datetime import datetime

import numpy as np

from .model import Check, Granule, LayerData

PCTS = [0.5, 1, 2, 5, 25, 50, 75, 95, 98, 99, 99.5]


def _d(iso):
    return datetime.strptime(iso[:10], "%Y-%m-%d").toordinal()


# ----------------------------------------------------------------------------- search analysis

class _UF:
    def __init__(self):
        self.p = {}

    def find(self, a):
        self.p.setdefault(a, a)
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a, b):
        self.p[self.find(a)] = self.find(b)


def _network(pairs):
    """pairs: [(day_a, day_b)] -> (n_nodes_in_largest_component, n_edges_in_it, n_components)."""
    if not pairs:
        return 0, 0, 0
    uf = _UF()
    for a, b in pairs:
        uf.union(a, b)
    comp = Counter(uf.find(a) for a, _ in pairs)
    nodes = Counter(uf.find(n) for n in {x for p in pairs for x in p})
    best = max(comp, key=lambda r: (comp[r], nodes[r]))
    return nodes[best], comp[best], len(nodes)


def lane_key(g: Granule):
    return (g.track, g.dir, g.frame)


def lane_label(k):
    t, d, f = k
    return f"track {t if t is not None else '?'} {d} frame {f if f is not None else '?'}"


def analyze_search(granules, bbox=None, info=None) -> dict:
    info = info or {}
    lanes = {}
    for g in granules:
        lane = lanes.setdefault(lane_key(g), {"granules": []})
        lane["granules"].append(g)
    rows = []
    for k, lane in sorted(lanes.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][2]))):
        gs = lane["granules"]
        pairs = [(_d(g.ref), _d(g.sec)) for g in gs if g.ref and g.sec]
        epochs = sorted({_d(x) for g in gs for x in (g.ref, g.sec) if x} | {_d(g.t0) for g in gs if g.t0 and not (g.ref and g.sec)})
        gaps = [b - a for a, b in zip(epochs, epochs[1:])]
        bl = [g.baseline_days for g in gs if g.baseline_days is not None]
        short = [(_d(g.ref), _d(g.sec)) for g in gs if g.baseline_days is not None and g.baseline_days <= 36.0 and g.ref and g.sec]
        n_nodes, n_edges, n_comp = _network(pairs)
        s_nodes, s_edges, _ = _network(short)
        rows.append({
            "key": k, "label": lane_label(k), "n": len(gs), "epochs": len(epochs),
            "first": datetime.fromordinal(epochs[0]).strftime("%Y-%m-%d") if epochs else "",
            "last": datetime.fromordinal(epochs[-1]).strftime("%Y-%m-%d") if epochs else "",
            "span_days": (epochs[-1] - epochs[0]) if epochs else 0, "gap_min": min(gaps) if gaps else None,
            "gap_med": float(np.median(gaps)) if gaps else None, "gap_max": max(gaps) if gaps else None,
            "baseline_min": min(bl) if bl else None, "baseline_max": max(bl) if bl else None,
            "pairs": len(pairs), "net_nodes": n_nodes, "net_edges": n_edges, "components": n_comp,
            "short_edges": s_edges, "short_nodes": s_nodes, "levels": sorted({g.level or "?" for g in gs}),
            "size_mb": sum(g.size_mb or 0 for g in gs), "size_known": sum(1 for g in gs if g.size_mb),
        })
    all_bl = [g.baseline_days for g in granules if g.baseline_days is not None]
    bins = [(0, 6, "<6"), (6, 12, "6-11"), (12, 13, "12"), (13, 24, "13-23"), (24, 25, "24"), (25, 36, "25-35"), (36, 37, "36"),
            (37, 72, "37-71"), (72, 1e9, ">=72")]
    hist = [(lab, sum(1 for b in all_bl if lo <= round(b) < hi)) for lo, hi, lab in bins]
    all_t = [t for g in granules for t in (g.t0, g.t1) if t]
    return {
        "n": len(granules), "lanes": rows, "baseline_hist": hist,
        "tracks": sorted({g.track for g in granules if g.track is not None}), "frames": sorted({g.frame for g in granules if g.frame is not None}),
        "levels": Counter(g.level or "?" for g in granules), "maturity": Counter(g.maturity or "?" for g in granules),
        "pol": Counter(g.pol or "?" for g in granules),
        "size_mb": sum(g.size_mb or 0 for g in granules), "size_known": sum(1 for g in granules if g.size_mb),
        "first": min(all_t)[:10] if all_t else "", "last": max(all_t)[:10] if all_t else "",
        "found": info.get("found", len(granules)), "dropped": info.get("dropped", 0), "capped": info.get("capped", False),
        "bbox": bbox, "source": info.get("source", ""),
    }


# Documented rules (Dancing_With_SARs docs/ARCHITECTURE.md sections 5 and 6). Edit here if the docs change.
READINESS_RULES = [
    ("land_subsidence", "pairs >= 10 in one connected network, baselines <= 36 d",
     lambda r: (r["short_edges"] >= 10, f"{r['short_edges']} short-baseline pair(s) in the largest connected network")),
    ("seasonal harmonic fit (any pipeline)", "history >= 1 year, else linear model only",
     lambda r: (r["span_days"] >= 365, f"history {r['span_days']} d")),
    ("permafrost_thaw", "epochs >= 12 spanning a full thaw season",
     lambda r: (r["epochs"] >= 12, f"{r['epochs']} epoch(s) over {r['span_days']} d (thaw-season coverage needs a human check)")),
]


def readiness(lanes_rows) -> list:
    out = []
    for r in lanes_rows:
        for name, rule, fn in READINESS_RULES:
            ok, detail = fn(r)
            out.append({"lane": r["label"], "rule": name, "need": rule, "ok": ok, "detail": detail})
    return out


def search_checks(a: dict) -> list:
    c = []
    if a["n"] == 0:
        return [Check("fail", "search", "no granules touch this box with these filters")]
    c.append(Check("ok", "search", f"{a['n']} granule(s) on {len(a['lanes'])} track/frame lane(s), {a['first']} .. {a['last']}"))
    if a["capped"]:
        c.append(Check("warn", "search cap", f"hit the result cap ({a['found']}); there may be more: raise --max or narrow the dates"))
    if a["dropped"]:
        c.append(Check("info", "versions", f"{a['dropped']} older duplicate processing version(s) dropped (newest kept)"))
    if a["size_known"] < a["n"]:
        c.append(Check("info", "sizes", f"ASF reported a size for {a['size_known']} of {a['n']} granules"))
    thin = [r["label"] for r in a["lanes"] if r["epochs"] < 3]
    if thin:
        c.append(Check("info", "stack depth", f"{len(thin)} lane(s) have fewer than 3 epochs (cold start): " + "; ".join(thin[:4]) + (" ..." if len(thin) > 4 else "")))
    split = [r["label"] for r in a["lanes"] if r["components"] > 1]
    if split:
        c.append(Check("warn", "pair network", f"{len(split)} lane(s) have a disconnected pair network (several date islands): " + "; ".join(split[:3])))
    return c


# ----------------------------------------------------------------------------- layer statistics

def _sample(v, n=400_000):
    return v if v.size <= n else v[:: math.ceil(v.size / n)]


def layer_stats(L: LayerData, wavelength=None) -> dict:
    a = np.asarray(L.array, np.float32)
    finite = np.isfinite(a)
    v = a[finite]
    st = {"n_px": int(a.size), "shape": tuple(a.shape), "valid": float(v.size / a.size) if a.size else 0.0, "notes": [], "extra": []}
    if not v.size:
        st["notes"].append("no valid pixels in this window")
        return st
    s = _sample(v)
    p = np.percentile(s, PCTS)
    med = float(p[5])
    st.update(min=float(v.min()), max=float(v.max()), mean=float(v.mean()), std=float(v.std()), median=med,
              mad_std=float(1.4826 * np.median(np.abs(s - med))), zero_frac=float(np.mean(v == 0)),
              pct=dict(zip(PCTS, (float(x) for x in p))))
    lo, hi = st["pct"][0.5], st["pct"][99.5]
    if L.kind == "cat" or hi <= lo:
        if hi <= lo:
            lo, hi = lo - 0.5, hi + 0.5
    counts, edges = np.histogram(np.clip(s, lo, hi), bins=60, range=(lo, hi))
    st["hist"] = (counts.tolist(), float(lo), float(hi))
    if st["std"] == 0:
        st["notes"].append("constant layer (std = 0)")
    if st["valid"] < 0.05:
        st["notes"].append(f"only {st['valid'] * 100:.1f}% of pixels are valid (heuristic: <5%)")
    if st["zero_frac"] > 0.5 and L.category not in ("components", "mask"):
        st["notes"].append(f"{st['zero_frac'] * 100:.0f}% of valid pixels are exactly 0 (fill value or missing data?)")

    if L.category == "unwrapped" and L.kind == "data":
        _unwrapped_extra(a, st, wavelength)
    elif L.category == "coherence" and L.kind == "data":
        st["extra"].append(("coherence >= 0.3 / 0.5 / 0.7 / 0.9",
                            " / ".join(f"{np.mean(v >= t) * 100:.1f}%" for t in (0.3, 0.5, 0.7, 0.9))))
        if st["max"] > 1.0001 or st["min"] < -0.0001:
            st["notes"].append(f"coherence outside 0..1 ({st['min']:.3g}..{st['max']:.3g}): wrong layer or unscaled data?")
    elif L.category == "components":
        lab = v[v > 0].astype(np.int64)
        cnt = Counter(lab.tolist())
        top = cnt.most_common(5)
        st["extra"].append(("labels (excluding 0)", f"{len(cnt)}"))
        st["extra"].append(("label 0 (not unwrapped)", f"{np.mean(v == 0) * 100:.1f}% of valid px"))
        st["extra"].append(("largest labels", ", ".join(f"#{k}: {c / v.size * 100:.1f}%" for k, c in top) or "none"))
        if not cnt:
            st["notes"].append("every pixel has component 0: nothing was unwrapped here")
    elif L.category == "mask":
        cnt = Counter(v.astype(np.int64).tolist()).most_common(8)
        st["extra"].append(("values", ", ".join(f"{k}: {c / v.size * 100:.1f}%" for k, c in cnt)))
    return st


def _unwrapped_extra(a, st, wavelength):
    v = a[np.isfinite(a)]
    if wavelength:
        k = wavelength / (4 * math.pi) * 1000.0           # mm per radian (sign convention not asserted here)
        st["extra"].append(("LOS scale", f"lambda/4pi = {k:.3f} mm per rad (lambda = {wavelength * 100:.2f} cm; sign per product convention)"))
        s = _sample(v)
        p2, p98 = np.percentile(s, [2, 98])
        st["extra"].append(("range p2..p98 as LOS length", f"{(p98 - p2) * k:.1f} mm  ({p2 * k:.1f} .. {p98 * k:.1f} mm)"))
        st["extra"].append(("robust spread (MAD) as LOS length", f"{st['mad_std'] * k:.2f} mm"))
    else:
        st["notes"].append("no wavelength found in the file, so radians could not be converted to millimetres")
    # phase jumps between neighbours: |d phi| > pi can't be real deformation at 80 m sampling -> unwrapping error candidates
    jx = np.abs(np.diff(a, axis=1))
    jy = np.abs(np.diff(a, axis=0))
    okx, oky = jx[np.isfinite(jx)], jy[np.isfinite(jy)]
    tot = okx.size + oky.size
    if tot:
        bad = int((okx > math.pi).sum() + (oky > math.pi).sum())
        frac = bad / tot
        st["extra"].append(("neighbour jumps > pi", f"{bad:,} of {tot:,} ({frac * 100:.3f}%)"))
        if frac > 0.005:
            st["notes"].append(f"{frac * 100:.2f}% of neighbouring pixel pairs jump by more than pi: possible unwrapping errors / cycle slips (heuristic: >0.5%)")
    # planar ramp (orbital / long-wavelength trend)
    ys, xs = np.nonzero(np.isfinite(a))
    if ys.size > 50:
        sel = slice(None, None, max(1, ys.size // 100_000))
        h, w = a.shape
        X, Y, Z = xs[sel] / max(w - 1, 1) - 0.5, ys[sel] / max(h - 1, 1) - 0.5, a[ys[sel], xs[sel]].astype(np.float64)
        A = np.c_[np.ones_like(X), X, Y]
        coef, *_ = np.linalg.lstsq(A, Z, rcond=None)
        res = Z - A @ coef
        st["extra"].append(("best-fit plane", f"{coef[1]:+.3g} rad across width, {coef[2]:+.3g} rad across height"))
        st["extra"].append(("std before / after plane removal", f"{Z.std():.3g} / {res.std():.3g} rad"))
        if Z.std() > 0 and res.std() / Z.std() < 0.5:
            st["notes"].append("a plane explains more than 75% of the variance: probably a ramp (orbit / atmosphere), not deformation")


# ----------------------------------------------------------------------------- cross-layer checks

def cross_checks(layers) -> list:
    """Relations between layers of the same file/window. Returns a list of Check."""
    by = {}
    for L in layers:
        by.setdefault((L.source, L.grid, L.window, L.step), {}).setdefault((L.category, L.kind), L)
    out = []
    for key, d in by.items():
        src = key[0] or "layers"
        unw, coh = d.get(("unwrapped", "data")), d.get(("coherence", "data"))
        cc, wph = d.get(("components", "cat")), d.get(("wrapped", "phase"))

        def same(*ls):
            return all(x is not None for x in ls) and len({x.array.shape for x in ls}) == 1

        if same(unw, cc):
            u, c = unw.array, cc.array
            ok = np.isfinite(c)
            in_cc = ok & (c >= 1)
            zero = ok & (c == 0)
            out.append(Check("info", f"{src}: component gate", f"{in_cc.sum() / max(ok.sum(), 1) * 100:.1f}% of pixels have component >= 1 (usable for the cc>=1 gate)"))
            if zero.any():
                leak = np.isfinite(u) & (u != 0) & zero
                frac = leak.sum() / max(zero.sum(), 1)
                out.append(Check("warn" if frac > 0.01 else "info", f"{src}: phase outside components",
                                 f"{frac * 100:.2f}% of component-0 pixels still carry non-zero finite unwrapped phase "
                                 f"(heuristic: warn above 1%; downstream code must gate on component, not on finiteness)"))
        if same(unw, coh):
            u, c = unw.array, coh.array
            fin = np.isfinite(u) & np.isfinite(c)
            if fin.any() and (~fin & np.isfinite(c)).any():
                out.append(Check("info", f"{src}: coherence vs phase", f"{(np.isfinite(c) & ~np.isfinite(u)).sum() / max(np.isfinite(c).sum(), 1) * 100:.1f}% of pixels with a coherence value have no unwrapped phase"))
        if same(unw, wph):
            diff = np.angle(np.exp(1j * (unw.array.astype(np.float64) - wph.array.astype(np.float64))))
            dd = diff[np.isfinite(diff)]
            if dd.size:
                off = float(np.median(dd))
                spread = float(np.median(np.abs(dd - off)) * 1.4826)
                bad = float(np.mean(np.abs(dd - off) > 0.5))
                out.append(Check("warn" if bad > 0.05 else "ok", f"{src}: wrapped vs unwrapped",
                                 f"wrap(unwrapped - wrapped) has median offset {off:+.3f} rad, robust spread {spread:.3f} rad, "
                                 f"{bad * 100:.1f}% of pixels differ by more than 0.5 rad from that offset (heuristic: warn above 5%)"))
        shapes = {tuple(x.array.shape) for x in d.values()}
        if len(shapes) > 1:
            out.append(Check("info", f"{src}: grid agreement", "layers in this window have different pixel sizes (different grids): " + ", ".join(map(str, sorted(shapes)))))
    return out
