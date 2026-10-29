"""Canonical LayerPayload — the ONE contract evidence rasters cross the API with.

D1 (task §5.1): tsunami_ocean_evidence used to emit `data` (nested list) + nested bounds, which
the UI cannot decode. Everything a pipeline emits now goes through `encode_layer` here:

    {name, dtype ("<f4"|"<u1"), shape [h, w], data_b64 (row 0 = north, NaN = no data),
     units, colormap, vmin, vmax, note, bounds_lonlat [W, S, E, N],
     categories?: [{value, label, tone: positive|negative|neutral}]}   (categorical <u1 only)

`validate_layer_payload(payload) -> list[str]` is the API-edge gate: an empty list is valid, a
non-empty list is a 422 with the problems — never a 500, never a silent pass. Validation is
transport-only (field presence / lengths / finiteness): it never interprets the numbers.
"""
from __future__ import annotations

import base64
import binascii
import math

import numpy as np

DTYPES = ("<f4", "<u1")
TONES = ("positive", "negative", "neutral")


def encode_layer(
    name: str,
    arr: np.ndarray,
    *,
    units: str = "",
    colormap: str = "sequential",
    vmin: float | None = None,
    vmax: float | None = None,
    note: str = "",
    bounds_lonlat: list | tuple | None = None,
    categories: list[dict] | None = None,
) -> dict:
    """Canonical payload for one evidence raster.

    Float grids encode as little-endian float32 with NaN = no data; boolean/uint8 grids encode
    as <u1. Row 0 of `arr` must be the NORTH row (the engine's grids already are: y descends).
    `bounds_lonlat` is the flat [W, S, E, N] core-grid rectangle (plan §6.4 as corrected:
    the nested [[w,s],[e,n]] form was doc drift — see docs/IMPLEMENTATION_PLAN.md §6.4).
    """
    a = np.asarray(arr)
    if a.dtype == np.bool_ or a.dtype == np.uint8:
        payload = a.astype(np.uint8).astype("<u1", copy=False)
    else:
        payload = a.astype(np.float32).astype("<f4", copy=False)
    if vmin is None or vmax is None:
        finite = payload.astype(np.float64)
        finite = finite[np.isfinite(finite)]
        if finite.size:
            lo, hi = float(finite.min()), float(finite.max())
            if vmin is None:
                vmin = -max(abs(lo), abs(hi)) if colormap.startswith("diverging") else lo
            if vmax is None:
                vmax = max(abs(lo), abs(hi)) if colormap.startswith("diverging") else hi
    out = {
        "name": name,
        "dtype": "<u1" if payload.dtype == np.uint8 else "<f4",
        "shape": [int(payload.shape[0]), int(payload.shape[1])],
        "data_b64": base64.b64encode(payload.tobytes()).decode("ascii"),
        "units": units,
        "colormap": colormap,
        "vmin": vmin,
        "vmax": vmax,
        "note": note,
        "bounds_lonlat": [float(v) for v in bounds_lonlat] if bounds_lonlat is not None else None,
    }
    if categories is not None:
        out["categories"] = [
            {"value": int(c["value"]), "label": str(c["label"]), "tone": str(c.get("tone", "neutral"))}
            for c in categories
        ]
    return out


def resample_mercator_rows(arr: np.ndarray, lat_top: float, lat_bottom: float, rows: int | None = None) -> tuple[np.ndarray, str]:
    """Rows linear in Web-Mercator Y (task DEC10): a raster whose latitude span exceeds 1 deg must
    be resampled by the ENGINE so rows are linear in Mercator Y — the client never reprojects.

    The input's rows are assumed uniform in LATITUDE (row 0 = north). The output's rows are
    uniform in MERCATOR Y (what a maplibre image source interpolates across the bbox). Each
    output row takes the source row nearest to its latitude — nearest-neighbour, so NaN stays
    NaN and no value is ever invented. Returns (resampled, note); note is empty when the grid
    already satisfies the contract.
    """
    from pyproj import Transformer

    a = np.asarray(arr)
    h = a.shape[0]
    n = rows or h
    tr = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    # source rows: uniform LATITUDE -> mercator Y of each row centre
    src_lat = lat_top + (lat_bottom - lat_top) * ((np.arange(h) + 0.5) / h)
    src_y = tr.transform(np.zeros(h), src_lat)[1]
    # output rows: uniform MERCATOR Y -> the latitude each output row represents
    _, y_top = tr.transform(0.0, lat_top)
    _, y_bot = tr.transform(0.0, lat_bottom)
    dst_y = y_top + (y_bot - y_top) * ((np.arange(n) + 0.5) / n)
    idx = np.abs(src_y[None, :] - dst_y[:, None]).argmin(axis=1)
    if np.array_equal(idx, np.arange(h)) and n == h:
        return a, ""
    return a[idx], (f"rows resampled to be linear in Web-Mercator Y "
                    f"(lat span {abs(lat_top - lat_bottom):.2f} deg > 1 deg)")


def _finite(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v))


def validate_layer_payload(payload) -> list[str]:
    """Transport-level contract check. Empty list = valid. Never raises, never interprets data."""
    problems: list[str] = []
    if not isinstance(payload, dict):
        return ["payload is not an object"]
    for key in ("name", "dtype", "shape", "data_b64", "bounds_lonlat"):
        if key not in payload or payload[key] is None:
            problems.append(f"missing field: {key}")
    if problems:
        return problems
    if payload["dtype"] not in DTYPES:
        problems.append(f"dtype {payload['dtype']!r} not in {DTYPES}")
    shape = payload["shape"]
    if not isinstance(shape, list) or len(shape) != 2 or not all(isinstance(v, int) and v > 0 for v in shape):
        problems.append(f"shape must be [h, w] of positive ints, got {shape!r}")
    if isinstance(shape, list) and len(shape) == 2 and all(isinstance(v, int) and v > 0 for v in shape):
        itemsize = 4 if payload["dtype"] == "<f4" else 1
        need = shape[0] * shape[1] * itemsize
        try:
            raw = base64.b64decode(str(payload["data_b64"]), validate=True)
        except (binascii.Error, ValueError):
            problems.append("data_b64 is not valid base64")
        else:
            if len(raw) != need:
                problems.append(f"decoded data_b64 is {len(raw)} bytes; shape*itemsize is {need}")
    b = payload["bounds_lonlat"]
    if not (isinstance(b, (list, tuple)) and len(b) == 4):
        problems.append(f"bounds_lonlat must be [W, S, E, N], got {b!r}")
    else:
        if not all(_finite(v) for v in b):
            problems.append(f"bounds_lonlat must be four finite numbers, got {b!r}")
        else:
            w, s, e, n = (float(v) for v in b)
            if w == e:
                problems.append("bounds_lonlat has W == E")
            if s >= n:
                problems.append(f"bounds_lonlat has S >= N ({s} >= {n})")
    for key in ("vmin", "vmax"):
        v = payload.get(key)
        if v is not None and not _finite(v):
            problems.append(f"{key} must be finite or null, got {v!r}")
    cats = payload.get("categories")
    if cats is not None:
        if not isinstance(cats, list):
            problems.append("categories must be a list")
        else:
            for c in cats:
                if not isinstance(c, dict) or "value" not in c or "label" not in c:
                    problems.append(f"category entry needs value+label, got {c!r}")
                elif c.get("tone") not in TONES:
                    problems.append(f"category tone {c.get('tone')!r} not in {TONES}")
    return problems
