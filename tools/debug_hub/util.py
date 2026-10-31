"""JSON-safe conversion of engine objects. Arrays are summarised (shape/dtype), never dumped."""
from __future__ import annotations

import dataclasses
import math
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path

import numpy as np


def plain(o, depth: int = 0):
    if depth > 12:
        return "<max depth>"
    if o is None or isinstance(o, (bool, str)):
        return o
    if isinstance(o, (int, np.integer)):
        return int(o)
    if isinstance(o, (float, np.floating)):
        f = float(o)
        return f if math.isfinite(f) else None
    if isinstance(o, np.ndarray):
        return {"__array__": True, "shape": list(o.shape), "dtype": str(o.dtype)}
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, Path):
        return str(o)
    if dataclasses.is_dataclass(o) and not isinstance(o, type):
        return {f.name: plain(getattr(o, f.name), depth + 1) for f in dataclasses.fields(o)}
    if isinstance(o, Mapping):
        return {str(k): plain(v, depth + 1) for k, v in o.items()}
    if isinstance(o, (list, tuple, set, frozenset)):
        return [plain(v, depth + 1) for v in (sorted(o, key=str) if isinstance(o, (set, frozenset)) else o)]
    return str(o)


def short_exc(ex: BaseException) -> str:
    return f"{type(ex).__name__}: {ex}"
