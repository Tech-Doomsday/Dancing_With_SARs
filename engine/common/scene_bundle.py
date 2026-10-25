"""SceneBundle: the read-only object every pipeline receives.

Immutability is enforced, not promised: arrays are flagged non-writeable and all
mappings are ``MappingProxyType``. ``save``/``load`` persist a bundle under
``<root>/<bundle_id>/`` (intended root: data/bundles/).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from types import MappingProxyType
from typing import Any, Dict, Mapping, Optional, Tuple

import numpy as np

from ._types import Grid, TileSpec

SCHEMA_VERSION = "1.0"

# Layers every bundle is guaranteed to carry (all on G80).
CORE_LAYERS: Tuple[str, ...] = ("los_disp_m", "los_sigma_m", "coherence", "conncomp",
                                "valid", "layover_shadow", "stable")


def _ro(a: np.ndarray) -> np.ndarray:
    a = np.asarray(a)
    a.flags.writeable = False
    return a


def _jsonable(o: Any) -> Any:
    if isinstance(o, Mapping):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set, frozenset)):
        return [_jsonable(v) for v in (sorted(o) if isinstance(o, (set, frozenset)) else o)]
    if isinstance(o, (np.floating,)):
        v = float(o)
        return None if v != v else v
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, Grid):
        return o.to_dict()
    if isinstance(o, float) and (o != o):
        return None
    return o


def _freeze(m: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(m))


@dataclass(frozen=True)
class TemporalStack:
    """P10 output. Rows of the stacks follow ``pairs`` order (current pair last)."""

    epochs: Tuple[Mapping[str, Any], ...] = ()
    pairs: Tuple[Mapping[str, Any], ...] = ()
    coh_stack: Optional[np.ndarray] = None        # (n_pairs, ny, nx) float16, G80
    disp_stack: Optional[np.ndarray] = None       # (n_pairs, ny, nx) float32 metres, G80
    bs_epochs: Tuple[Mapping[str, Any], ...] = ()
    bs_stack: Mapping[str, np.ndarray] = field(default_factory=dict)  # pol -> (n, ny20, nx20) float16 dB
    n_pairs: int = 0
    n_epochs: int = 0
    span_days: float = 0.0
    cold_start: bool = True

    def __post_init__(self) -> None:
        for name in ("coh_stack", "disp_stack"):
            v = getattr(self, name)
            if v is not None:
                object.__setattr__(self, name, _ro(v))
        object.__setattr__(self, "bs_stack", _freeze({k: _ro(v) for k, v in self.bs_stack.items()}))
        object.__setattr__(self, "epochs", tuple(_freeze(e) for e in self.epochs))
        object.__setattr__(self, "pairs", tuple(_freeze(p) for p in self.pairs))
        object.__setattr__(self, "bs_epochs", tuple(_freeze(e) for e in self.bs_epochs))


@dataclass(frozen=True)
class SceneBundle:
    bundle_id: str
    tile: TileSpec
    sensor: str
    product: str
    group: Mapping[str, Any]                 # sensor/track/frame/direction
    epoch: Mapping[str, Any]                 # granule ids, ref/sec times
    grids: Mapping[str, Grid]
    layers: Mapping[str, np.ndarray]
    layer_grid: Mapping[str, str]
    meta: Mapping[str, Any]                  # scalars: wavelength_m, los_azimuth_deg, hand_method, ...
    stack: TemporalStack
    corrections: Mapping[str, Any]           # P07 audit (recorded, never applied)
    quality: Mapping[str, Any]               # P12 tile quality
    routing: Mapping[str, Any]               # P12 per-pipeline applicability + reasons
    flags: Tuple[str, ...]
    cold_start: bool
    degraded_mode: bool
    provenance: Mapping[str, Any]
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        set_ = lambda k, v: object.__setattr__(self, k, v)  # noqa: E731
        set_("grids", _freeze(self.grids))
        set_("layers", _freeze({k: _ro(v) for k, v in self.layers.items()}))
        set_("layer_grid", _freeze(self.layer_grid))
        for k in ("group", "epoch", "meta", "corrections", "quality", "routing", "provenance"):
            set_(k, _freeze(getattr(self, k)))
        set_("flags", tuple(sorted(set(self.flags))))
        missing = [n for n in self.layer_grid if n not in self.layers]
        if missing:
            raise ValueError(f"layer_grid names without data: {missing}")

    # ---- access -------------------------------------------------------
    def has(self, name: str) -> bool:
        return name in self.layers

    def layer(self, name: str) -> np.ndarray:
        try:
            return self.layers[name]
        except KeyError:
            raise KeyError(f"layer '{name}' not in bundle {self.bundle_id}; "
                           f"available: {sorted(self.layers)}") from None

    def require(self, *names: str) -> None:
        miss = [n for n in names if n not in self.layers]
        if miss:
            raise KeyError(f"bundle {self.bundle_id} lacks required layers {miss}")

    def grid_of(self, name: str) -> Grid:
        self.layer(name)
        return self.grids[self.layer_grid[name]]

    def applicable_pipelines(self) -> Tuple[str, ...]:
        return tuple(self.routing.get("applicable", ()))

    def summary(self) -> Dict[str, Any]:
        return _jsonable({
            "bundle_id": self.bundle_id, "tile": self.tile.tile_id, "sensor": self.sensor,
            "product": self.product, "epoch": dict(self.epoch), "cold_start": self.cold_start,
            "degraded_mode": self.degraded_mode, "flags": list(self.flags),
            "quality": dict(self.quality), "applicable": list(self.applicable_pipelines()),
            "n_layers": len(self.layers), "n_pairs": self.stack.n_pairs,
            "n_epochs": self.stack.n_epochs,
        })

    # ---- persistence ----------------------------------------------------
    def save(self, root) -> "Any":
        from pathlib import Path
        out = Path(root) / self.bundle_id
        out.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(out / "layers.npz", **{k: np.asarray(v) for k, v in self.layers.items()})
        st: Dict[str, np.ndarray] = {}
        if self.stack.coh_stack is not None:
            st["coh_stack"] = np.asarray(self.stack.coh_stack)
        if self.stack.disp_stack is not None:
            st["disp_stack"] = np.asarray(self.stack.disp_stack)
        for pol, arr in self.stack.bs_stack.items():
            st[f"bs_{pol}"] = np.asarray(arr)
        np.savez_compressed(out / "stack.npz", **st)
        meta = _jsonable({
            "schema_version": self.schema_version, "bundle_id": self.bundle_id,
            "tile": {"tile_id": self.tile.tile_id, "bbox": list(self.tile.bbox)},
            "sensor": self.sensor, "product": self.product, "group": self.group,
            "epoch": self.epoch, "grids": self.grids, "layer_grid": self.layer_grid,
            "meta": self.meta,
            "stack": {"epochs": list(self.stack.epochs), "pairs": list(self.stack.pairs),
                      "bs_epochs": list(self.stack.bs_epochs), "n_pairs": self.stack.n_pairs,
                      "n_epochs": self.stack.n_epochs, "span_days": self.stack.span_days,
                      "cold_start": self.stack.cold_start,
                      "bs_pols": list(self.stack.bs_stack)},
            "corrections": self.corrections, "quality": self.quality, "routing": self.routing,
            "flags": list(self.flags), "cold_start": self.cold_start,
            "degraded_mode": self.degraded_mode, "provenance": self.provenance,
        })
        (out / "meta.json").write_text(json.dumps(meta, indent=2, sort_keys=True))
        return out

    @staticmethod
    def load(path) -> "SceneBundle":
        from pathlib import Path
        p = Path(path)
        m = json.loads((p / "meta.json").read_text())
        with np.load(p / "layers.npz") as z:
            layers = {k: z[k] for k in z.files}
        with np.load(p / "stack.npz") as z:
            sz = {k: z[k] for k in z.files}
        s = m["stack"]
        stack = TemporalStack(
            epochs=tuple(s["epochs"]), pairs=tuple(s["pairs"]),
            coh_stack=sz.get("coh_stack"), disp_stack=sz.get("disp_stack"),
            bs_epochs=tuple(s["bs_epochs"]),
            bs_stack={pol: sz[f"bs_{pol}"] for pol in s["bs_pols"]},
            n_pairs=s["n_pairs"], n_epochs=s["n_epochs"], span_days=s["span_days"],
            cold_start=s["cold_start"])
        return SceneBundle(
            bundle_id=m["bundle_id"], tile=TileSpec(m["tile"]["tile_id"], tuple(m["tile"]["bbox"])),
            sensor=m["sensor"], product=m["product"], group=m["group"], epoch=m["epoch"],
            grids={k: Grid.from_dict(v) for k, v in m["grids"].items()}, layers=layers,
            layer_grid=m["layer_grid"], meta=m["meta"], stack=stack, corrections=m["corrections"],
            quality=m["quality"], routing=m["routing"], flags=tuple(m["flags"]),
            cold_start=m["cold_start"], degraded_mode=m["degraded_mode"],
            provenance=m["provenance"], schema_version=m["schema_version"])
