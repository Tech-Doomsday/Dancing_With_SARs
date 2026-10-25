"""Render a Trace (plus an optional reality check) as one self-contained HTML file: no server, no network.

    write_report("bundle_debug.html", trace, retriever=r, reality=rows, title="...")

Layer cards follow the look of nisar-data_analysis/nisar_browser.html: one canvas per array, colormap + lo/hi stretch
(p2-p98 by default), min/max/mean/valid %, fill value, hover readout with row/col and map coordinates.
"""
from __future__ import annotations

import base64
import dataclasses
import json
from collections.abc import Mapping
from pathlib import Path

import numpy as np

from .trace import STAGE, Trace

TEMPLATE = Path(__file__).with_name("report.html")


def _plain(v, depth=0):
    """Recursive JSON-safe view of the small dataclasses in a bundle (arrays are never serialised)."""
    if depth > 6:
        return str(v)
    if isinstance(v, np.ndarray):
        return f"<array {v.shape} {v.dtype}>"
    if isinstance(v, np.generic):
        return v.item()
    if isinstance(v, (str, int, float, bool)) or v is None:
        return None if isinstance(v, float) and not np.isfinite(v) else v
    if dataclasses.is_dataclass(v) and not isinstance(v, type):
        return {f.name: _plain(getattr(v, f.name), depth + 1) for f in dataclasses.fields(v)}
    if isinstance(v, Mapping):
        return {str(k): _plain(x, depth + 1) for k, x in v.items()}
    if isinstance(v, (list, tuple, set, frozenset)):
        return [_plain(x, depth + 1) for x in v]
    return str(v)


def _array_json(a) -> dict:
    t = np.ascontiguousarray(a.thumb, dtype="<f4")
    return {"path": a.path, "label": a.label, "group": a.group, "stage": STAGE.get(a.group, a.group), "step": a.step,
            "first_step": a.first_step, "history": [h[0] for h in a.history], "shape": list(a.shape), "dtype": a.dtype,
            "kind": a.kind, "stats": a.stats, "w": int(t.shape[1]), "h": int(t.shape[0]), "stride": a.stride,
            "extent": list(a.extent) if a.extent else None, "epsg": a.epsg, "fill": a.fill,
            "cats": {str(k): v for k, v in a.categories.items()} if a.categories else None, "note": a.note,
            "b64": base64.b64encode(t.tobytes()).decode()}


# phase -> LOS lineage: (stage label, group, label suffix)
_LINEAGE = (("raw clip", "clips", "unwrappedPhase"), ("P03 decoded", "decoded", "unw_phase-HH"),
            ("P04 harmonised", "harm_pairs", "unw_phase-HH"), ("P06 LOS", "ifg", "los_m/[0]"))


def _lineage(trace: Trace) -> list:
    out = []
    for stage, group, suffix in _LINEAGE:
        for a in trace.arrays.values():
            if a.group == group and a.label.endswith(suffix):
                out.append({"stage": stage, "valid": a.stats["valid"]})
                break
    return out


def scene_card(retriever, tile, max_px: int = 400) -> dict | None:
    """The whole real product (phase) with the tile outlined, when the source can provide it."""
    ph = getattr(retriever, "phase", None)
    if ph is None:
        return None
    st = max(1, int(np.ceil(max(ph.shape) / max_px)))
    t = np.ascontiguousarray(ph[::st, ::st], dtype="<f4")
    x0, x1 = float(retriever.x[0] - retriever.dx / 2), float(retriever.x[-1] + retriever.dx / 2)
    y1, y0 = float(retriever.y[0] + retriever.dx / 2), float(retriever.y[-1] - retriever.dx / 2)
    sx, sy = t.shape[1] / (x1 - x0), t.shape[0] / (y1 - y0)
    box = [(tile.e0 - x0) * sx, (y1 - (tile.n0 + tile.size)) * sy, tile.size * sx, tile.size * sy]
    ok = np.isfinite(t)
    s = {"valid": float(ok.mean()), "n": int(t.size), "min": None, "max": None, "mean": None, "std": None, "p2": None, "p98": None}
    if ok.any():
        v = t[ok]
        p2, p98 = np.percentile(v, [2, 98])
        s.update(min=float(v.min()), max=float(v.max()), mean=float(v.mean()), std=float(v.std()), p2=float(p2), p98=float(p98))
    return {"path": "scene/unwrappedPhase", "label": "unwrappedPhase (whole product, tile outlined in yellow)",
            "group": "scene", "stage": "real product", "step": "-", "first_step": "-", "history": ["-"], "shape": list(ph.shape),
            "dtype": "float32", "kind": "float", "stats": s, "w": int(t.shape[1]), "h": int(t.shape[0]), "stride": st,
            "extent": [x0, x1, y0, y1], "epsg": retriever.epsg, "fill": None, "cats": None, "note": "", "box": box,
            "b64": base64.b64encode(t.tobytes()).decode()}


def report_data(trace: Trace, retriever=None, reality=None, title="SceneBundle debug", limitations=None) -> dict:
    b = trace.bundle
    stack = b.stack()
    order = {s.code: i for i, s in enumerate(trace.steps)}
    arrays = sorted(trace.arrays.values(), key=lambda a: (order.get(a.first_step, 99), a.group, a.label))
    routing = []
    if b.routing is not None:
        for name, d in sorted(b.routing.decisions.items(), key=lambda kv: ({"yes": 0, "unknown": 1, "no": 2}[kv[1].applies], kv[0])):
            routing.append({"pipeline": name, "applies": d.applies, "reasons": [str(r) for r in d.reasons], "needs": [str(n) for n in d.needs]})
    tile = _plain(b.tile)
    return {
        "title": title, "tracer_errors": list(trace.tracer_errors),
        "bundle": {"status": b.status, "id": b.bundle_id, "contract": b.contract_version, "tile": tile,
                   "request": _plain(b.request), "capabilities": sorted(b.capabilities), "depth": _plain(b.depth),
                   "quality": _plain(stack.quality) if stack is not None else None},
        "steps": [dataclasses.asdict(s) for s in trace.steps],
        "arrays": [_array_json(a) for a in arrays],
        "lineage": _lineage(trace),
        "flags": [{"code": f.code, "severity": f.severity, "step": f.step, "message": f.message} for f in b.flags],
        "assumptions": list(b.provenance.assumptions),
        "routing": routing,
        "reality": list(reality or []),
        "limitations": list(limitations or []),
        "scene": scene_card(retriever, _tile_obj(b, retriever)) if retriever is not None else None,
    }


def _tile_obj(bundle, retriever):
    ti = bundle.tile
    class _T:  # only e0 / n0 / size are needed for the outline
        pass
    t = _T()
    t.e0, t.n0, t.size = ti.e0, ti.n0, ti.size_m
    return t


def render(data: dict) -> str:
    blob = json.dumps(data, separators=(",", ":"), allow_nan=False).replace("</", "<\\/")
    return TEMPLATE.read_text(encoding="utf-8").replace("__DATA__", blob)


def write_report(path, trace: Trace, **kw) -> Path:
    p = Path(path)
    p.write_text(render(report_data(trace, **kw)), encoding="utf-8")
    return p
