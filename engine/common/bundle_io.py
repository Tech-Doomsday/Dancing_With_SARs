"""SceneBundle <-> disk.

    <root>/<bundle_id>/bundle.json      the whole bundle tree, arrays replaced by {"__a": n}
    <root>/<bundle_id>/arrays/0000.npy  one .npy per array (np.load(mmap_mode="r") -> truly read-only)

Bundle ids are content addressed (P13), so saving an id that already exists is a no-op, and several worker
processes can map the same bundle without copying it. Writes are atomic (temp directory + rename).
No pickle anywhere: a bundle file cannot execute code when loaded.
"""
from __future__ import annotations

import dataclasses
import json
import math
import os
import shutil
from pathlib import Path
from typing import Any

import numpy as np

from . import scene_bundle as sb
from .frozen import Dual, FrozenDict
from .grids import GridSet, GridSpec

FORMAT = "scenebundle-1"

_CLASSES: dict[str, type] = {}
for _c in list(vars(sb).values()) + [Dual, GridSet, GridSpec]:
    if isinstance(_c, type) and dataclasses.is_dataclass(_c):
        _CLASSES[_c.__name__] = _c


# ---------------------------------------------------------------------------------------------
def _enc(o: Any, arrays: list) -> Any:
    if o is None or isinstance(o, (bool, str)):
        return o
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (int, np.integer)):
        return int(o)
    if isinstance(o, (float, np.floating)):
        f = float(o)
        return f if math.isfinite(f) else {"__f": "nan" if math.isnan(f) else ("inf" if f > 0 else "-inf")}
    if isinstance(o, np.ndarray):
        if o.dtype == object:
            raise TypeError("object arrays cannot be stored in a bundle")
        arrays.append(o)
        return {"__a": len(arrays) - 1}
    if isinstance(o, tuple):
        return {"__t": [_enc(v, arrays) for v in o]}
    if isinstance(o, list):
        return {"__l": [_enc(v, arrays) for v in o]}
    if isinstance(o, (frozenset, set)):
        return {"__fs": sorted((_enc(v, arrays) for v in o), key=lambda v: json.dumps(v, sort_keys=True))}
    if isinstance(o, (FrozenDict, dict)):
        return {"__d": [[_enc(k, arrays), _enc(v, arrays)] for k, v in o.items()]}
    if dataclasses.is_dataclass(o) and not isinstance(o, type):
        name = type(o).__name__
        if name not in _CLASSES:
            raise TypeError(f"{name} is not a registered bundle type")
        return {"__dc": name, "f": {f.name: _enc(getattr(o, f.name), arrays) for f in dataclasses.fields(o)}}
    raise TypeError(f"cannot store {type(o).__name__} in a bundle")


def _dec(o: Any, arrays: list) -> Any:
    if not isinstance(o, dict):
        return o
    if "__a" in o:
        return arrays[o["__a"]]
    if "__f" in o:
        return float(o["__f"])
    if "__t" in o:
        return tuple(_dec(v, arrays) for v in o["__t"])
    if "__l" in o:
        return [_dec(v, arrays) for v in o["__l"]]
    if "__fs" in o:
        return frozenset(_dec(v, arrays) for v in o["__fs"])
    if "__d" in o:
        return FrozenDict({_dec(k, arrays): _dec(v, arrays) for k, v in o["__d"]})
    if "__dc" in o:
        cls = _CLASSES[o["__dc"]]
        return cls(**{k: _dec(v, arrays) for k, v in o["f"].items()})
    raise ValueError(f"unrecognised node {list(o)[:3]}")


# ---------------------------------------------------------------------------------------------
def bundle_path(root: str | Path, bundle_id: str) -> Path:
    return Path(root) / bundle_id


def save_bundle(bundle: sb.SceneBundle, root: str | Path) -> Path:
    """Write the bundle under <root>/<bundle_id>. Idempotent and safe against concurrent writers."""
    final = bundle_path(root, bundle.bundle_id)
    if (final / "bundle.json").exists():
        return final
    arrays: list[np.ndarray] = []
    tree = _enc(bundle, arrays)
    final.parent.mkdir(parents=True, exist_ok=True)
    tmp = final.with_name(f".{final.name}.tmp{os.getpid()}")
    shutil.rmtree(tmp, ignore_errors=True)
    (tmp / "arrays").mkdir(parents=True)
    for i, a in enumerate(arrays):
        np.save(tmp / "arrays" / f"{i:04d}.npy", np.ascontiguousarray(a), allow_pickle=False)
    (tmp / "bundle.json").write_text(json.dumps({"format": FORMAT, "n_arrays": len(arrays), "tree": tree},
                                                separators=(",", ":")))
    try:
        os.replace(tmp, final)
    except OSError:
        shutil.rmtree(tmp, ignore_errors=True)
        if not (final / "bundle.json").exists():       # a concurrent writer finished first -> theirs is fine
            raise
    return final


def _load_array(p: Path, mmap: bool) -> np.ndarray:
    if mmap:
        try:
            return np.load(p, mmap_mode="r", allow_pickle=False)
        except ValueError:                 # zero-size arrays cannot be memory-mapped
            pass
    return np.load(p, allow_pickle=False)


def load_bundle(path: str | Path, mmap: bool = True) -> sb.SceneBundle:
    """Load a bundle. mmap=True maps the arrays read-only from disk (what pipeline workers use)."""
    path = Path(path)
    doc = json.loads((path / "bundle.json").read_text())
    if doc.get("format") != FORMAT:
        raise ValueError(f"{path}: unsupported bundle format {doc.get('format')!r}")
    arrays = [_load_array(path / "arrays" / f"{i:04d}.npy", mmap) for i in range(doc["n_arrays"])]
    b = _dec(doc["tree"], arrays)
    if not isinstance(b, sb.SceneBundle):
        raise ValueError(f"{path}: not a SceneBundle")
    return b
