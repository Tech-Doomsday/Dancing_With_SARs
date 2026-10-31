"""Watch the P01..P13 chain work: after every step, record which arrays appeared or changed and what they look like.

    trace = traced_build(tile, retriever, as_of=..., cfg=...)
    trace.bundle            the normal frozen SceneBundle (identical to build_scene_bundle's result)
    trace.steps             [StepSnap]: code, status, seconds, message, new flags / assumptions, arrays that are new or changed
    trace.arrays            {path: ArrayRec}: the latest version of every array, with the step that last touched it

The tracer only reads the BuildContext through the chain's `observer` hook. It never writes to it, and an error in
the tracer itself is recorded in `trace.tracer_errors` instead of breaking the build.
"""
from __future__ import annotations

import dataclasses
import zlib
from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np

from engine.common.chain import build_scene_bundle
from engine.common.layers import integer_fill

ROOTS = ("clips", "decoded", "harm_pairs", "harm_epochs", "harm_offsets", "harm_geometry", "geometry", "ifg", "cov",
         "stable", "ancillary", "_terrain")
MAX_PX = 400           # longest side of a stored thumbnail (decimated, never resampled)
MAX_DEPTH = 9


@dataclass
class ArrayRec:
    path: str
    label: str
    group: str                      # first path component: clips | decoded | harm_pairs | ifg | ...
    step: str                       # step that last created or changed it
    first_step: str                 # step that first produced it
    shape: tuple
    dtype: str
    kind: str                       # float | int | bool
    stats: dict
    thumb: np.ndarray               # float32 (h, w), NaN where invalid
    stride: int
    extent: tuple | None            # (xmin, xmax, ymin, ymax) pixel edges in `epsg`
    epsg: int | None
    fill: float | None = None
    categories: dict | None = None  # integer / bool layers: value -> pixel count
    note: str = ""
    history: list = field(default_factory=list)   # [(step, fingerprint)]


@dataclass
class StepSnap:
    code: str
    name: str
    status: str
    seconds: float
    message: str
    new_flags: list                 # [{code, severity, message}]
    new_assumptions: list
    arrays: list                    # paths that are new or changed in this step
    n_arrays_total: int = 0


@dataclass
class Trace:
    steps: list = field(default_factory=list)
    arrays: dict = field(default_factory=dict)
    bundle: object = None
    tracer_errors: list = field(default_factory=list)


# ---------------------------------------------------------------------------------------------
# walking the context
# ---------------------------------------------------------------------------------------------
def _short(s: str) -> str:
    s = str(s)
    return s if len(s) <= 28 else f"{s[:10]}…{s[-8:]}"


def _extent_of(obj):
    x, y = getattr(obj, "x", None), getattr(obj, "y", None)
    if isinstance(x, np.ndarray) and isinstance(y, np.ndarray) and x.ndim == 1 and y.ndim == 1 and len(x) > 1 and len(y) > 1:
        dx, dy = abs(float(x[1] - x[0])), abs(float(y[1] - y[0]))
        epsg = getattr(obj, "epsg", None)
        return (float(x.min()) - dx / 2, float(x.max()) + dx / 2, float(y.min()) - dy / 2, float(y.max()) + dy / 2), \
               (int(epsg) if epsg is not None else None)
    return None


def _walk(obj, path, out, ext, depth=0):
    if depth > MAX_DEPTH or obj is None:
        return
    if isinstance(obj, np.ndarray):
        out.append((path, obj, ext))
        return
    if isinstance(obj, (str, bytes, int, float, bool, np.generic)):
        return
    e = _extent_of(obj)
    if e is not None:
        ext = e
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        for f in dataclasses.fields(obj):
            _walk(getattr(obj, f.name, None), path + [f.name], out, ext, depth + 1)
    elif isinstance(obj, Mapping):
        for k, v in obj.items():
            _walk(v, path + ["-".join(map(str, k)) if isinstance(k, tuple) else str(k)], out, ext, depth + 1)
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            role = getattr(v, "role", None)
            name = (f"{role}-{v.pol}" if getattr(v, "pol", None) else str(role)) if isinstance(role, str) else str(i)
            _walk(v, path + [name], out, ext, depth + 1)
    elif type(obj).__module__.startswith("engine.common") and hasattr(obj, "__dict__"):
        for k, v in vars(obj).items():
            _walk(v, path + [k], out, ext, depth + 1)


def _slices(path, a, ext):
    """2-D arrays as they are; (k, ny, nx) with k <= 4 as k layers; bigger leading axes: only the first, noted."""
    if a.ndim == 2:
        yield path, a, ext, ""
    elif a.ndim == 3 and a.shape[0] <= 4:
        for i in range(a.shape[0]):
            yield path + [f"[{i}]"], a[i], ext, ""
    elif a.ndim == 3:
        yield path + ["[0]"], a[0], ext, f"first of {a.shape[0]}"


# ---------------------------------------------------------------------------------------------
# summarising one array
# ---------------------------------------------------------------------------------------------
def _summ(a: np.ndarray):
    """-> kind, float32 view with NaN for invalid, fill, categories, stats."""
    if a.dtype == bool:
        f = a.astype(np.float32)
        return "bool", f, None, {"false": int((~a).sum()), "true": int(a.sum())}, a.size
    if a.dtype.kind in "iu":
        fill = integer_fill(a.dtype)
        bad = a == fill
        vals, cnt = np.unique(a, return_counts=True)
        cat = {int(v): int(c) for v, c in sorted(zip(vals, cnt), key=lambda t: -t[1])[:12]}
        f = a.astype(np.float32)
        f[bad] = np.nan
        return "int", f, fill, cat, a.size
    f = a.astype(np.float32, copy=False)
    return "float", f, None, None, a.size


def _stats(f: np.ndarray) -> dict:
    ok = np.isfinite(f)
    n = int(ok.sum())
    s = {"valid": n / f.size if f.size else 0.0, "n": int(f.size)}
    if n:
        v = f[ok]
        samp = v[:: max(1, v.size // 200_000)]
        p2, p98 = np.percentile(samp, [2, 98])
        s.update(min=float(v.min()), max=float(v.max()), mean=float(v.mean()), std=float(v.std()),
                 p2=float(p2), p98=float(p98))
    else:
        s.update(min=None, max=None, mean=None, std=None, p2=None, p98=None)
    return s


_DROP = {"layers", "data", "grids", "units"}
STAGE = {"clips": "raw clip (P02)", "decoded": "decoded (P03)", "harm_pairs": "harmonised (P04)",
         "harm_epochs": "harmonised (P04)", "harm_offsets": "harmonised (P04)", "harm_geometry": "harmonised geometry (P04)",
         "geometry": "geometry (P05)", "ifg": "interferometric (P06/P07)", "cov": "backscatter (P08/P10)",
         "stable": "stable mask (P11)", "ancillary": "ancillary (P09)", "_terrain": "terrain (P05/P09)"}


def _label(path) -> str:
    g, rest = path[0], list(path[1:])
    if g == "clips":
        idx = "".join(p for p in rest if p.startswith("["))
        flat = [p for p in rest if "__" in p]
        return ("/".join(flat[-1].split("__")[-2:]) if flat else "/".join(rest[-2:])) + idx
    rest = [p for p in rest if p not in _DROP and len(p) <= 30]
    return "/".join(rest[-4:]) or g


def _record(path, a, ext, note, step) -> ArrayRec:
    kind, f, fill, cat, _ = _summ(a)
    st = max(1, int(np.ceil(max(f.shape) / MAX_PX)))
    thumb = np.ascontiguousarray(f[::st, ::st], dtype=np.float32)
    group = path[0] if path else ""
    label = _label(path)
    fp = f"{a.shape}|{a.dtype}|{zlib.adler32(np.ascontiguousarray(a).view(np.uint8))}"
    return ArrayRec("/".join(path), label, group, step, step, tuple(a.shape), str(a.dtype), kind, _stats(f), thumb, st,
                    ext[0] if ext else None, ext[1] if ext else None, fill, cat, note, [(step, fp)])


# ---------------------------------------------------------------------------------------------
# the tracer
# ---------------------------------------------------------------------------------------------
class Tracer:
    def __init__(self):
        self.trace = Trace()
        self._seen_flags = 0
        self._seen_assume = 0

    def _canon_extent(self, ctx, ext, shape):
        if ext is not None:
            return ext
        for g in (ctx.grids.g80, ctx.grids.g20):
            if tuple(shape) == tuple(g.shape):
                x0, x1, y0, y1 = g.bounds if len(g.bounds) == 4 else (None,) * 4
                dx = float(g.dx)
                return ((float(g.x.min()) - dx / 2, float(g.x.max()) + dx / 2,
                         float(g.y.min()) - dx / 2, float(g.y.max()) + dx / 2), int(g.epsg))
        return None

    def __call__(self, step, ctx, rec):
        try:
            self._observe(step, ctx, rec)
        except Exception as ex:                                   # the tracer must never break a build
            self.trace.tracer_errors.append(f"{step.code}: {type(ex).__name__}: {ex}")

    def _observe(self, step, ctx, rec):
        found: list = []
        for r in ROOTS:
            _walk(getattr(ctx, r, None), [r], found, None)
        changed, seen_ids = [], set()
        for path, a, ext in found:
            if a.ndim < 2 or min(a.shape[-2:]) < 4 or a.dtype.kind not in "fiub":
                continue
            for p, sl, e, note in _slices(path, a, ext):
                key = "/".join(p)
                if key in seen_ids:
                    continue
                seen_ids.add(key)
                e = self._canon_extent(ctx, e, sl.shape)
                fp = f"{sl.shape}|{sl.dtype}|{zlib.adler32(np.ascontiguousarray(sl).view(np.uint8))}"
                old = self.trace.arrays.get(key)
                if old is not None and old.history[-1][1] == fp:
                    continue
                new = _record(p, sl, e, note, step.code)
                if old is not None:
                    new.first_step, new.history = old.first_step, old.history + new.history
                self.trace.arrays[key] = new
                changed.append(key)
        flags = [{"code": f.code, "severity": f.severity, "message": f.message} for f in ctx.flags[self._seen_flags:]]
        self._seen_flags = len(ctx.flags)
        assume = list(ctx.assumptions[self._seen_assume:])
        self._seen_assume = len(ctx.assumptions)
        self.trace.steps.append(StepSnap(rec.code, rec.name, rec.status, rec.seconds, rec.message, flags, assume,
                                         changed, len(seen_ids)))


def traced_build(tile, retriever, **kw) -> Trace:
    """build_scene_bundle(...) with a step-by-step record of what each of P01..P13 did to the arrays."""
    tr = Tracer()
    tr.trace.bundle = build_scene_bundle(tile, retriever, observer=tr, **kw)
    return tr.trace
