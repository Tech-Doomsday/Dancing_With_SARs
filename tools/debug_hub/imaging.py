"""Array -> PNG, with no dependency beyond numpy (own PNG encoder, own colour maps)."""
from __future__ import annotations

import struct
import zlib

import numpy as np

_ANCHORS = {
    "viridis": [(0, (68, 1, 84)), (.125, (71, 44, 122)), (.25, (59, 81, 139)), (.375, (44, 113, 142)), (.5, (33, 144, 141)),
                (.625, (39, 173, 129)), (.75, (92, 200, 99)), (.875, (170, 220, 50)), (1, (253, 231, 37))],
    "diverging": [(0, (45, 80, 190)), (.25, (120, 160, 225)), (.5, (238, 238, 238)), (.75, (232, 150, 120)), (1, (178, 24, 43))],
    "magma": [(0, (0, 0, 4)), (.25, (80, 18, 123)), (.5, (182, 54, 121)), (.75, (251, 136, 97)), (1, (252, 253, 191))],
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
        _LUT[name] = np.stack([np.interp(t, [p[0] for p in pts], [p[1][c] for p in pts]) for c in range(3)], 1).astype(np.uint8)
    return _LUT[name]


def finite_view(a: np.ndarray) -> np.ndarray:
    """2-D float32 view for display: complex -> |z|, ints -> float, non-finite -> NaN. Never invents values."""
    a = np.asarray(a)
    if a.ndim == 3:
        a = a[a.shape[0] // 2]
    if a.ndim != 2:
        a = a.reshape(a.shape[-2], a.shape[-1]) if a.ndim > 2 else a.reshape(1, -1)
    if a.dtype.kind == "c":
        a = np.abs(a)
    return a.astype(np.float32, copy=False)


def decimate(a: np.ndarray, max_px: int) -> tuple:
    """Stride decimation (exact pixels, no resampling). -> (array, stride)."""
    step = max(1, int(np.ceil(max(a.shape) / max_px)))
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


def render(a: np.ndarray, cmap: str, lo: float, hi: float) -> bytes:
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
    out[..., 3] = np.where(ok, 255, 0)           # transparent = no data (never painted as a value)
    return png(out)
