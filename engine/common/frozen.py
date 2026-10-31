"""Immutability primitives for SceneBundle.

* `FrozenDict`  - immutable, picklable mapping (types.MappingProxyType is neither picklable nor copyable).
* `Dual`        - the same quantity on both canonical grids: `.g80` (80 m) and `.g20` (20 m).
* `freeze_array`- read-only ndarray that owns its data, so nobody can flip it back to writeable through a view.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Iterator

import numpy as np


class FrozenDict(Mapping):
    """Immutable mapping. Values are frozen on the way in (arrays become read-only, dicts/lists nested)."""

    __slots__ = ("_d",)

    def __init__(self, data: Mapping | None = None, **kw: Any):
        d = dict(data or {})
        d.update(kw)
        object.__setattr__(self, "_d", {k: freeze(v) for k, v in d.items()})

    def __getitem__(self, k):
        return self._d[k]

    def __iter__(self) -> Iterator:
        return iter(self._d)

    def __len__(self) -> int:
        return len(self._d)

    def __setattr__(self, name, value):
        raise AttributeError("FrozenDict is immutable")

    def __reduce__(self):
        return (_rebuild_frozen, (dict(self._d),))

    def __repr__(self) -> str:
        return f"FrozenDict({list(self._d)})"

    __hash__ = None  # type: ignore[assignment]


def _rebuild_frozen(d: dict) -> FrozenDict:
    return FrozenDict(d)


def _locked(a: np.ndarray) -> bool:
    """True if nobody can flip `a` back to writeable (read-only memory map, or a view of an immutable buffer)."""
    try:
        a.view().setflags(write=True)
    except ValueError:
        return True
    return False


def freeze_array(a: np.ndarray) -> np.ndarray:
    """Read-only array that cannot be made writeable again with `setflags`.

    A read-only memory map (what `load_bundle` hands out) or a view of immutable bytes is already locked and is returned
    as is (it stays on disk). Anything else is copied once into an immutable `bytes` buffer and viewed from there: numpy
    refuses to re-enable writing on such an array, whereas `a.setflags(write=False)` alone is reversible for an owner."""
    a = np.asarray(a)
    if not a.flags.writeable and _locked(a):
        return a
    if a.dtype.kind == "O":
        raise TypeError("object arrays cannot be frozen")
    if a.size == 0:
        out = np.empty(a.shape, a.dtype)
    else:
        out = np.frombuffer(np.ascontiguousarray(a).tobytes(), dtype=a.dtype).reshape(a.shape)
    out.setflags(write=False)
    return out


def freeze(v: Any) -> Any:
    """Recursively freeze containers and arrays. Leaves frozen dataclasses and scalars alone."""
    if isinstance(v, np.ndarray):
        return freeze_array(v)
    if isinstance(v, FrozenDict):
        return v
    if isinstance(v, dict):
        return FrozenDict(v)
    if isinstance(v, (list, tuple)):
        return tuple(freeze(x) for x in v)
    if isinstance(v, (set, frozenset)):
        return frozenset(v)
    return v


@dataclass(frozen=True)
class Dual:
    """One quantity on both canonical grids. Either side may be None when it does not exist."""
    g80: np.ndarray | None = None
    g20: np.ndarray | None = None

    def __post_init__(self):
        for n in ("g80", "g20"):
            v = getattr(self, n)
            if v is not None:
                object.__setattr__(self, n, freeze_array(v))

    def __getitem__(self, grid: str) -> np.ndarray | None:
        return {"g80": self.g80, "g20": self.g20, "G80": self.g80, "G20": self.g20}[grid]

    @property
    def available(self) -> bool:
        return self.g80 is not None or self.g20 is not None
