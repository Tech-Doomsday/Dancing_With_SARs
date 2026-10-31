"""The SceneBundle contract, as executable checks.

`check_bundle(bundle)` returns the list of violations (empty = the bundle honours the contract documented at the top of
`scene_bundle.py`); `assert_bundle_contract(bundle)` raises `ContractViolation` with all of them. It is what
`tests/test_scene_bundle_contract.py` runs against every bundle the tests build, and what a pipeline author can call on
their own synthetic bundle. `deep=True` additionally scans array values (unit vectors, physical ranges, NaN rules).

Structural checks (always): version / status / ids, grid shapes, dtype + shape of every layer, ordering of pairs and
epochs, read-only-ness of EVERY array and immutability of every container reachable from the bundle, consistency of
`capabilities`, `routing` and `depth` with the data.
"""
from __future__ import annotations

import dataclasses
from collections.abc import Mapping

import numpy as np

from .frozen import Dual, FrozenDict
from .scene_bundle import (CONTRACT_VERSION, STATUS_EMPTY, STATUS_OK, STATUS_PARTIAL, SceneBundle)

SEVERITIES = ("info", "warn", "error")
APPLIES = ("yes", "no", "unknown")

# the fields a pipeline may rely on being present on every bundle (None / empty where the data does not exist)
BUNDLE_FIELDS = ("contract_version", "bundle_id", "status", "tile", "request", "epoch", "grids", "stacks", "ancillary",
                 "routing", "depth", "capabilities", "flags", "provenance")
STACK_FIELDS = ("key", "track", "direction", "interferometric", "offsets", "backscatter", "geometry", "stable", "quality")


class ContractViolation(AssertionError):
    def __init__(self, problems: list):
        super().__init__(f"{len(problems)} SceneBundle contract violation(s):\n  - " + "\n  - ".join(problems))
        self.problems = problems


def _walk(o, path: str, out: list, seen: set) -> None:
    """Every array must be read-only, every container immutable."""
    if id(o) in seen:
        return
    if isinstance(o, np.ndarray):
        seen.add(id(o))
        if o.flags.writeable:
            out.append(f"{path}: array is writeable")
        if o.dtype.kind == "O":
            out.append(f"{path}: object array")
        return
    if isinstance(o, (dict, list, set)):
        out.append(f"{path}: mutable {type(o).__name__}")
        return
    if isinstance(o, (FrozenDict, Mapping)):
        seen.add(id(o))
        for k, v in o.items():
            _walk(v, f"{path}[{k!r}]", out, seen)
        return
    if isinstance(o, (tuple, frozenset)):
        seen.add(id(o))
        for i, v in enumerate(o):
            _walk(v, f"{path}[{i}]", out, seen)
        return
    if dataclasses.is_dataclass(o) and not isinstance(o, type):
        seen.add(id(o))
        params = getattr(o, "__dataclass_params__", None)
        if params is not None and not params.frozen:
            out.append(f"{path}: non-frozen dataclass {type(o).__name__}")
        for f in dataclasses.fields(o):
            _walk(getattr(o, f.name), f"{path}.{f.name}", out, seen)


def _shape(out: list, path: str, a, shape: tuple, dtype=None, lead: int | None = None) -> None:
    if a is None:
        return
    exp = shape if lead is None else (lead,) + shape
    if a.shape != exp:
        out.append(f"{path}: shape {a.shape}, expected {exp}")
    if dtype is not None and a.dtype != np.dtype(dtype):
        out.append(f"{path}: dtype {a.dtype}, expected {np.dtype(dtype)}")


def check_bundle(b: SceneBundle, deep: bool = False) -> list:
    out: list = []
    for f in BUNDLE_FIELDS:
        if not hasattr(b, f):
            out.append(f"missing field {f}")
    if out:
        return out
    if b.contract_version != CONTRACT_VERSION:
        out.append(f"contract_version {b.contract_version!r} != {CONTRACT_VERSION!r}")
    if b.status not in (STATUS_OK, STATUS_PARTIAL, STATUS_EMPTY):
        out.append(f"status {b.status!r}")
    if b.bundle_id != b.provenance.bundle_id:
        out.append("bundle_id differs from provenance.bundle_id")
    if not b.bundle_id.startswith(b.tile.id + "-"):
        out.append("bundle_id does not start with the tile id")
    if b.status == STATUS_EMPTY and b.stacks:
        out.append("status 'empty' but stacks exist")
    if b.status != STATUS_EMPTY and not b.stacks:
        out.append(f"status {b.status!r} but no stacks")
    _walk(b, "bundle", out, set())

    g80, g20 = b.grids.g80, b.grids.g20
    s80, s20 = g80.shape, g20.shape
    if s80[0] != s80[1] or s20[0] != s20[1]:
        out.append("canonical grids are not square")
    if s80[0] * b.grids.ratio != s20[0]:
        out.append(f"grid ratio {b.grids.ratio} inconsistent with {s80} / {s20}")
    if b.grids.index_map_20_to_80.shape != s20:
        out.append("index_map_20_to_80 shape differs from G20")
    elif b.grids.index_map_20_to_80.size and (b.grids.index_map_20_to_80.min() < 0 or
                                              b.grids.index_map_20_to_80.max() >= s80[0] * s80[1]):
        out.append("index_map_20_to_80 points outside G80")
    if g80.epsg != b.tile.epsg or g20.epsg != b.tile.epsg:
        out.append("grids are not in the tile's CRS")

    caps = set()
    for key, s in b.stacks.items():
        p = f"stacks[{key!r}]"
        for f in STACK_FIELDS:
            if not hasattr(s, f):
                out.append(f"{p}: missing {f}")
        if s.key != key:
            out.append(f"{p}: key mismatch {s.key!r}")
        i = s.interferometric
        if i is not None:
            caps.add("M1")
            P = len(i.pairs)
            if P == 0:
                out.append(f"{p}.interferometric: no pairs")
            for n in ("los_m", "sigma_m", "coherence", "phase_rad", "iono_floor_m"):
                _shape(out, f"{p}.interferometric.{n}", getattr(i, n), s80, np.float32, P)
            _shape(out, f"{p}.interferometric.conn_comp", i.conn_comp, s80, np.uint16, P)
            keyed = [(q.sec_time or "", q.ref_time or "") for q in i.pairs if q.times_resolved]
            if keyed != sorted(keyed):
                out.append(f"{p}.interferometric.pairs not ordered by secondary time")
            for q in i.pairs:
                if q.times_resolved and not (q.ref_time < q.sec_time):
                    out.append(f"{p}: pair {q.granule_ids[0]} has ref_time >= sec_time")
                if q.times_resolved and q.dt_days is not None and q.dt_days <= 0:
                    out.append(f"{p}: pair {q.granule_ids[0]} dt_days <= 0")
            for name, it in i.corrections.items.items():
                if it.applied_by_engine:
                    out.append(f"{p}: correction {name} was applied by the engine (never allowed)")
                if it.layer_m is not None:
                    _shape(out, f"{p}.corrections.{name}.layer_m", it.layer_m, s80, np.float32, P)
                if it.available and it.layer_m is None:
                    out.append(f"{p}: correction {name} available but has no layer")
            if any(it.available for it in i.corrections.items.values()):
                caps.add("corrections")
            if i.corrections.residual_los_m is not None:
                _shape(out, f"{p}.corrections.residual_los_m", i.corrections.residual_los_m, s80, np.float32, P)
            if i.network.n_pairs != P:
                out.append(f"{p}.network.n_pairs {i.network.n_pairs} != {P}")
            if i.coherence_baseline is not None:
                caps.add("coherence_baseline")
                _shape(out, f"{p}.coherence_baseline.median", i.coherence_baseline.median, s80, np.float32)
            if deep:
                if np.nanmin(np.where(np.isfinite(i.sigma_m), i.sigma_m, 0)) < 0:
                    out.append(f"{p}: negative sigma_m")
                c = i.coherence[np.isfinite(i.coherence)]
                if c.size and (c.min() < 0 or c.max() > 1):
                    out.append(f"{p}: coherence outside [0, 1]")
                if np.any((i.conn_comp == 0) & np.isfinite(i.phase_rad)):
                    out.append(f"{p}: phase survives where conn_comp == 0 (gate broken)")
        o = s.offsets
        if o is not None:
            caps.add("M3")
            P = len(o.pairs)
            for n in ("range_m", "azimuth_m", "peak"):
                _shape(out, f"{p}.offsets.{n}", getattr(o, n), s80, np.float32, P)
        bs = s.backscatter
        if bs is not None:
            caps.add("M2")
            E = len(bs.epochs)
            times = [e.time for e in bs.epochs]
            if times != sorted(times):
                out.append(f"{p}.backscatter.epochs not ascending")
            if bs.current_index != E - 1:
                out.append(f"{p}.backscatter.current_index {bs.current_index} != {E - 1}")
            for pol, a in bs.gamma0_db.items():
                _shape(out, f"{p}.backscatter.gamma0_db[{pol}]", a, s20, np.float32, E)
            for pol, a in bs.sigma0_db.items():
                _shape(out, f"{p}.backscatter.sigma0_db[{pol}]", a, s20, np.float32, E)
            _shape(out, f"{p}.backscatter.valid", bs.valid, s20, np.bool_, E)
            _shape(out, f"{p}.backscatter.hv_hh_db", bs.hv_hh_db, s20, np.float32, E)
            _shape(out, f"{p}.backscatter.n_looks", bs.n_looks, s20, np.float32, E)
            for n in ("median_db", "mad_db", "sigma_db", "n"):
                for pol, a in getattr(bs.baseline, n).items():
                    _shape(out, f"{p}.backscatter.baseline.{n}[{pol}]", a, s20)
            for n in ("layover", "shadow", "low_incidence", "high_incidence"):
                _shape(out, f"{p}.backscatter.masks.{n}", getattr(bs.masks, n), s20, np.bool_)
            if not bs.baseline.cold_start:
                caps.add("baseline")
            if bs.baseline.n_epochs != len(bs.baseline.epoch_indices):
                out.append(f"{p}.backscatter.baseline: n_epochs != len(epoch_indices)")
            if any(j >= bs.current_index for j in bs.baseline.epoch_indices):
                out.append(f"{p}.backscatter.baseline uses the current or a later epoch")
            if deep:
                for pol, a in bs.baseline.sigma_db.items():
                    v = a[np.isfinite(a)]
                    if v.size and v.min() <= 0:
                        out.append(f"{p}.backscatter.baseline.sigma_db[{pol}] <= 0")
        g = s.geometry
        if g is not None and g.vectors_available:
            caps.add("geometry")
            if g.los_enu.g80 is not None:
                _shape(out, f"{p}.geometry.los_enu.g80", g.los_enu.g80, s80, np.float32, 3)
            if g.los_enu.g20 is not None:
                _shape(out, f"{p}.geometry.los_enu.g20", g.los_enu.g20, s20, np.float32, 3)
            if deep and g.los_enu.g80 is not None:
                n = np.sqrt((g.los_enu.g80.astype(np.float64) ** 2).sum(0))
                if np.nanmax(np.abs(n - 1.0)) > 1e-3:
                    out.append(f"{p}.geometry.los_enu is not a unit vector")
                if np.nanmin(g.los_enu.g80[2]) < 0:
                    out.append(f"{p}.geometry.los_enu points below the horizon")
        if s.stable is not None and s.stable.mask.available:
            caps.add("stable_mask")
            _shape(out, f"{p}.stable.mask.g80", s.stable.mask.g80, s80, np.bool_)
            _shape(out, f"{p}.stable.mask.g20", s.stable.mask.g20, s20, np.bool_)
        q = s.quality
        for n in ("coverage_g80", "coverage_g20", "valid_fraction_g80", "valid_fraction_g20"):
            if not (0.0 <= getattr(q, n) <= 1.0):
                out.append(f"{p}.quality.{n} outside [0, 1]")

    a = b.ancillary
    for name in ("elevation_m", "slope_deg", "aspect_deg", "curvature_profile", "curvature_plan", "hand_m", "landcover",
                 "water_occurrence_pct"):
        d: Dual = getattr(a, name)
        dt = np.uint8 if name == "landcover" else np.float32
        _shape(out, f"ancillary.{name}.g80", d.g80, s80, dt)
        _shape(out, f"ancillary.{name}.g20", d.g20, s20, dt)
    for name, d in a.context.items():
        _shape(out, f"ancillary.context[{name}].g80", d.g80, s80, np.float32)
        if d.g20 is not None:
            _shape(out, f"ancillary.context[{name}].g20", d.g20, s20, np.float32)
    if a.elevation_m.available:
        caps.add("terrain")
    if a.hand_m.available:
        caps.add("hand")
    if a.landcover.available:
        caps.add("landcover")
    if a.water_occurrence_pct.available:
        caps.add("water_occurrence")
    if caps != set(b.capabilities):
        out.append(f"capabilities {sorted(b.capabilities)} != what the data supports {sorted(caps)}")

    for f in b.flags:
        if f.severity not in SEVERITIES:
            out.append(f"flag {f.code}: severity {f.severity!r}")
    if b.routing is not None:
        r = b.routing
        for n, d in r.decisions.items():
            if d.applies not in APPLIES:
                out.append(f"routing[{n}]: applies {d.applies!r}")
            if d.pipeline != n:
                out.append(f"routing[{n}]: decision names {d.pipeline!r}")
            if d.applies == "unknown" and not d.needs:
                out.append(f"routing[{n}]: unknown without needs")
        if set(r.applicable) != {n for n, d in r.decisions.items() if d.applies == "yes"}:
            out.append("routing.applicable is not the set of 'yes' decisions")
        if set(r.unknown) != {n for n, d in r.decisions.items() if d.applies == "unknown"}:
            out.append("routing.unknown is not the set of 'unknown' decisions")
    d = b.depth
    if b.stacks:
        want = max((len(s.interferometric.pairs) if s.interferometric else 0) for s in b.stacks.values())
        if d.n_gunw_pairs != want:
            out.append(f"depth.n_gunw_pairs {d.n_gunw_pairs} != {want}")
    if not b.provenance.inputs_hash and b.provenance.inputs:
        out.append("provenance.inputs_hash empty")
    return out


def assert_bundle_contract(b: SceneBundle, deep: bool = False) -> None:
    problems = check_bundle(b, deep=deep)
    if problems:
        raise ContractViolation(problems)
