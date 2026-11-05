"""Array -> PNG rendering for the gated /api/background/preview and trace-array routes.

Adapted from tools/debug_hub/imaging.py (the §4.5 design reference) WITHOUT importing the hub
server: the same numpy-only PNG encoder, colour maps, decimation and auto-range ideas, made
standalone. No science: this is display encoding the ENGINE performs, and every number it uses
(min/max/percentiles) is reported back to the UI as metadata rather than hidden.
"""
from __future__ import annotations

import struct
import zlib

import numpy as np

_ANCHORS = {
    "viridis": [(0, (68, 1, 84)), (.125, (71, 44, 122)), (.25, (59, 81, 139)), (.375, (44, 113, 142)),
                (.5, (33, 144, 141)), (.625, (39, 173, 129)), (.75, (92, 200, 99)), (.875, (170, 220, 50)),
                (1, (253, 231, 37))],
    "diverging": [(0, (45, 80, 190)), (.25, (120, 160, 225)), (.5, (238, 238, 238)),
                  (.75, (232, 150, 120)), (1, (178, 24, 43))],
    "magma": [(0, (0, 0, 4)), (.25, (80, 18, 123)), (.5, (182, 54, 121)), (.75, (251, 136, 97)),
              (1, (252, 253, 191))],
    "gray": [(0, (15, 15, 15)), (1, (240, 240, 240))],
}
_CAT = [(78, 121, 167), (242, 142, 43), (89, 161, 79), (225, 87, 89), (176, 122, 161), (237, 201, 72),
        (118, 183, 178), (255, 157, 167), (156, 117, 95), (186, 176, 172)]
_LUT: dict = {}
CMAPS = tuple(_ANCHORS) + ("categorical",)


def png(rgba: np.ndarray) -> bytes:
    rgba = np.ascontiguousarray(rgba, np.uint8)
    h, w, _ = rgba.shape
    raw = np.concatenate([np.zeros((h, 1), np.uint8), rgba.reshape(h, w * 4)], axis=1).tobytes()

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def _lut(name: str) -> np.ndarray:
    if name not in _LUT:
        pts = _ANCHORS[name]
        t = np.linspace(0, 1, 256)
        _LUT[name] = np.stack([np.interp(t, [p[0] for p in pts], [p[1][c] for p in pts])
                               for c in range(3)], 1).astype(np.uint8)
    return _LUT[name]


def finite_view(a: np.ndarray, mask_int_fill: bool = False) -> np.ndarray:
    """2-D float32 view for display: complex -> |z|, bool/int -> float, non-finite -> NaN.
    A (k, ny, nx) cube with a small leading axis shows its middle slice (noted in metadata).

    mask_int_fill=True (retrieved SAR layers only): an integer layer's fill value - dtype max for unsigned, dtype min
    for signed, the same convention engine/common/p03_decode_gate applies - becomes NaN, so 255 / 65535 never gets
    painted as a class. Float no-data is NaN already (the retrieval client now leaves never-written chunks as NaN)."""
    a = np.asarray(a)
    sliced = None
    if a.ndim == 3:
        sliced = a.shape[0] // 2
        a = a[sliced]
    if a.ndim != 2:
        a = a.reshape(a.shape[-2], a.shape[-1]) if a.ndim > 2 else a.reshape(1, -1)
    if a.dtype.kind == "c":
        a = np.abs(a)
    out = a.astype(np.float32, copy=False)
    if mask_int_fill and a.dtype.kind in "iu":
        from engine.common.layers import integer_fill
        out = out.copy()
        out[a == integer_fill(a.dtype)] = np.nan
    return out


def slice_note(a: np.ndarray) -> str | None:
    """Honest note when a cube is shown by one slice."""
    if np.asarray(a).ndim == 3:
        return f"slice {np.asarray(a).shape[0] // 2} of {np.asarray(a).shape[0]} shown"
    return None


def to_db(a: np.ndarray) -> np.ndarray:
    """10*log10(a) for display. Values <= 0 and non-finite values become NaN (no-data, alpha 0):
    a log of zero or a negative is not a number and is never painted as one."""
    a = np.asarray(a, np.float64)
    out = np.full(a.shape, np.nan, np.float32)
    ok = np.isfinite(a) & (a > 0)
    out[ok] = (10.0 * np.log10(a[ok])).astype(np.float32)
    return out


def decimate(a: np.ndarray, max_px: int) -> tuple:
    """Stride decimation (exact pixels, no resampling). -> (array, stride)."""
    step = max(1, int(np.ceil(max(a.shape) / max(1, int(max_px)))))
    return (a[::step, ::step] if step > 1 else a), step


def auto_range(a: np.ndarray, symmetric: bool = False) -> tuple:
    f = a[np.isfinite(a)]
    if f.size == 0:
        return 0.0, 1.0
    lo, hi = np.percentile(f, [2, 98])
    if symmetric:
        m = float(max(abs(lo), abs(hi))) or 1.0
        return -m, m
    if hi <= lo:
        lo, hi = float(f.min()), float(f.max())
    return (float(lo), float(hi)) if hi > lo else (float(lo) - 0.5, float(hi) + 0.5)


def modal(v: np.ndarray, cap: int = 1_000_000) -> tuple:
    """(most frequent exact value, its share of v) - how a flat or saturated image is told apart from a real one.
    Large arrays are strided down to `cap` samples first (exact values are kept, nothing is resampled)."""
    v = np.asarray(v).reshape(-1)
    if v.size == 0:
        return None, 0.0
    if v.size > cap:
        v = v[:: -(-v.size // cap)]
    u, c = np.unique(v, return_counts=True)
    i = int(np.argmax(c))
    return float(u[i]), float(c[i]) / float(v.size)


def stats(a: np.ndarray, masked: np.ndarray | None = None) -> dict:
    """Engine-computed stats reported to the UI (shape/dtype/min/max/percentiles/nan fraction).
    `masked` = the display view with no-data already NaN (finite_view(a, mask_int_fill=True), before any dB
    transform): the numbers then describe the VALID pixels, so they agree with the legend's no-data share."""
    a = np.asarray(a)
    out = {"shape": list(a.shape), "dtype": str(a.dtype), "n_bytes": int(a.nbytes)}
    if masked is not None:
        f = np.asarray(masked, np.float64)
        fin = np.isfinite(f)
    else:
        f = np.abs(a) if a.dtype.kind == "c" else a.astype(np.float64, copy=False)
        fin = np.isfinite(f) if a.dtype.kind in "fc" else np.ones(a.shape, bool)
    out["nan_fraction"] = float(1.0 - fin.mean()) if fin.size else 0.0
    if fin.any():
        v = f[fin]
        p2, p50, p98 = np.percentile(v, [2, 50, 98])
        mv, mf = modal(v)
        out.update(min=float(v.min()), max=float(v.max()), mean=float(v.mean()),
                   p2=float(p2), p50=float(p50), p98=float(p98), modal_value=mv, modal_fraction=mf)
    else:
        out.update(min=None, max=None, mean=None, p2=None, p50=None, p98=None, modal_value=None, modal_fraction=None)
    return out


def render(a: np.ndarray, cmap: str, lo: float, hi: float, alpha: np.ndarray | None = None) -> bytes:
    """RGBA PNG. `alpha` (0..1, same shape as a) scales each valid pixel's opacity - used by the coherence
    validation to fade unreliable pixels. No-data stays fully transparent whatever alpha says."""
    a = np.asarray(a, np.float32)
    ok = np.isfinite(a)
    out = np.zeros(a.shape + (4,), np.uint8)
    if cmap == "categorical":
        lab = np.nan_to_num(a, nan=0.0).astype(np.int64)
        pal = np.array(_CAT, np.uint8)
        out[..., :3] = pal[(np.maximum(lab, 1) - 1) % len(pal)]
        out[ok & (lab <= 0), :3] = (35, 35, 40)
    else:
        t = np.clip(np.nan_to_num((a - lo) / ((hi - lo) or 1.0), nan=0.0, posinf=1.0, neginf=0.0), 0, 1)
        out[..., :3] = _lut(cmap)[(t * 255).astype(np.uint8)]
    if alpha is None:
        out[..., 3] = np.where(ok, 255, 0)       # transparent = no data (never painted as a value)
    else:
        al = np.clip(np.nan_to_num(np.asarray(alpha, np.float32), nan=0.0), 0.0, 1.0)
        out[..., 3] = np.where(ok, np.rint(al * 255.0), 0).astype(np.uint8)
    return png(out)


def _axis_ranges(tc: np.ndarray, sc: np.ndarray):
    """For every target pixel centre `tc`: the half-open index range [i0, i1) of source pixels (centres `sc`) that
    fall inside the target pixel, plus a mask of targets that lie outside the source extent. Both axes may run in
    either direction; indices refer to the arrays as given. A target finer than the source takes the nearest one."""
    tc, sc = np.asarray(tc, np.float64), np.asarray(sc, np.float64)
    n = sc.size
    s_flip = bool(n > 1 and sc[0] > sc[-1])
    s = sc[::-1] if s_flip else sc                       # ascending source centres
    t_flip = bool(tc.size > 1 and tc[0] > tc[-1])
    t = tc[::-1] if t_flip else tc                       # ascending target centres
    ds_ = np.diff(s)
    dt_ = np.diff(t)
    half_s = float(np.median(ds_)) / 2 if ds_.size else 0.5
    half_t = float(np.median(dt_)) / 2 if dt_.size else half_s
    te_lo, te_hi = t - half_t, t + half_t                # target pixel edges (regular grids)
    i0 = np.searchsorted(s, te_lo, side="left")
    i1 = np.searchsorted(s, te_hi, side="left")
    nearest = np.clip(np.searchsorted((s[1:] + s[:-1]) / 2, t), 0, n - 1) if n > 1 else np.zeros(t.size, int)
    empty = i1 <= i0
    i0 = np.where(empty, nearest, i0)
    i1 = np.where(empty, nearest + 1, i1)
    outside = (t < s[0] - half_s) | (t > s[-1] + half_s)
    if s_flip:                                           # back to the source's own index order
        i0, i1 = n - i1, n - i0
    if t_flip:                                           # back to the target's own order
        i0, i1, outside = i0[::-1], i1[::-1], outside[::-1]
    return i0.astype(np.int64), i1.astype(np.int64), outside


def resample_mean(src: np.ndarray, sx, sy, tx, ty) -> np.ndarray:
    """Mean of the VALID source pixels inside each target pixel (both grids in the same CRS) -> float32 array of
    shape (len(ty), len(tx)); NaN where the target lies outside the source or no valid source pixel falls in it.
    Used to bring a coherence layer onto another layer's grid (NISAR's pixelOffsets grid is coarser than the
    interferogram grid). Exact: integral images, no interpolation, NaN-aware."""
    src = np.asarray(src, np.float64)
    if src.ndim != 2 or src.shape != (len(sy), len(sx)):
        raise ValueError("source array does not match its axes")
    for name, ax in (("source x", sx), ("source y", sy), ("target x", tx), ("target y", ty)):
        d = np.diff(np.asarray(ax, np.float64))
        if d.size and (np.any(d == 0) or not np.allclose(d, d[0], rtol=1e-3, atol=0)):
            raise ValueError(f"{name} axis is not regularly spaced")        # never guess on a malformed grid
    r0, r1, out_r = _axis_ranges(ty, sy)
    c0, c1, out_c = _axis_ranges(tx, sx)
    ok = np.isfinite(src)
    val = np.where(ok, src, 0.0)
    S = np.zeros((src.shape[0] + 1, src.shape[1] + 1)); C = np.zeros_like(S)
    S[1:, 1:] = val.cumsum(0).cumsum(1)
    C[1:, 1:] = ok.astype(np.float64).cumsum(0).cumsum(1)
    R0, C0 = np.ix_(r0, c0); R1, C1 = np.ix_(r1, c1)
    tot = S[R1, C1] - S[R0, C1] - S[R1, C0] + S[R0, C0]
    cnt = C[R1, C1] - C[R0, C1] - C[R1, C0] + C[R0, C0]
    with np.errstate(invalid="ignore", divide="ignore"):
        out = np.where(cnt > 0.5, tot / np.maximum(cnt, 1.0), np.nan)
    out[out_r, :] = np.nan
    out[:, out_c] = np.nan
    return out.astype(np.float32)


def quality_alpha(q: np.ndarray, lo: float, hi: float, min_alpha: float) -> np.ndarray:
    """Opacity from a 0..1 quality value: `min_alpha` at/below lo, 1.0 at/above hi, linear in between.
    Unknown quality (NaN) is NOT trusted: it gets min_alpha, the same as the worst measured quality."""
    q = np.asarray(q, np.float64)
    t = np.clip(np.nan_to_num((q - lo) / (hi - lo), nan=0.0, posinf=1.0, neginf=0.0), 0.0, 1.0)
    return (min_alpha + (1.0 - min_alpha) * t).astype(np.float32)


__all__ = ["CMAPS", "auto_range", "decimate", "finite_view", "png", "to_db", "render", "slice_note", "stats",
           "modal", "resample_mean", "quality_alpha"]
