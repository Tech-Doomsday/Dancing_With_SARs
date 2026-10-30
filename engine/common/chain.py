"""The P01 -> P13 chain and the assembly of the frozen SceneBundle.

    bundle = build_scene_bundle(tile, retriever, as_of=..., ancillary=..., active_codes=..., event_window=..., triggers=...)

Steps run strictly in order and share one mutable `BuildContext`. A step that raises does not take the build down:
the failure becomes an `error` flag and a `StepRecord(status="error")`, later steps run on whatever exists, and the
bundle's `status` says `partial`. `ChainError` is raised only when the chain cannot even start (bad tile / config).
A step is skipped (recorded, not an error) when none of the context attributes it `needs` holds anything.

The retriever is used only through `search()` and `fetch_tile()` - the retrieval layer itself is never modified.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np

from . import (p01_granule_resolution as p01, p02_clip as p02, p03_decode_gate as p03, p04_grid_harmonization as p04,
               p05_geometry as p05, p06_phase_to_los as p06, p07_correction_audit as p07, p08_backscatter_prep as p08,
               p09_ancillary_align as p09, p10_temporal_stack as p10, p11_stable_mask as p11, p12_tile_routing as p12,
               p13_provenance as p13)
from .ancillary import AncillaryProvider
from .config import CommonConfig, load_common_config
from .context import BuildContext, BuildRequest, ChainError, Step, Timer, iso, parse_time
from .frozen import Dual, FrozenDict
from .grids import make_tile_grids
from .scene_bundle import (CONTRACT_VERSION, AncillaryLayers, Baseline, CorrectionAudit, DataDepth, InterferometricData,
                           OffsetData, PairNetwork, Provenance, RequestInfo, Routing, SceneBundle, StackBundle,
                           StableMask, TileInfo, TileQuality, BackscatterData, BackscatterMasks, Geometry,
                           STATUS_EMPTY, STATUS_OK, STATUS_PARTIAL)
from .scene_bundle import StepRecord
from .terrain_set import TerrainSet  # noqa: F401  (re-export for tests)

# `needs`: the step runs when ANY of these context attributes is non-empty (empty tuple = always)
STEPS: tuple = (
    Step("P01", "granule_resolution", p01.run),
    Step("P02", "clip", p02.run, ("resolved",)),
    Step("P03", "decode_gate", p03.run, ("clips",)),
    Step("P04", "grid_harmonization", p04.run, ("decoded",)),
    Step("P05", "geometry", p05.run, ("harm_geometry",)),
    Step("P06", "phase_to_los", p06.run, ("harm_pairs",)),
    Step("P07", "correction_audit", p07.run, ("ifg",)),
    Step("P08", "backscatter_prep", p08.run, ("harm_epochs",)),
    Step("P09", "ancillary_align", p09.run),
    Step("P10", "temporal_stack", p10.run, ("ifg", "cov")),
    Step("P11", "stable_mask", p11.run, ("ifg", "cov")),
    Step("P12", "tile_routing", p12.run),
    Step("P13", "provenance", p13.run),
)


def _needs_met(ctx: BuildContext, step: Step) -> bool:
    if not step.needs:
        return True
    for a in step.needs:
        v = getattr(ctx, a, None)
        if v is not None and (not hasattr(v, "__len__") or len(v) > 0):
            return True
    return False


def run_steps(ctx: BuildContext, observer=None, on_start=None) -> None:
    """`observer(step, ctx, record)` is called after every step (skipped ones too). It is read-only debugging
    plumbing (engine.common.debug.trace uses it) and never changes what a step does.
    `on_start(step, ctx)` is called BEFORE each step runs, so a progress display can name the step that is running now
    (the observer only fires once a step is over). A failing on_start never fails the build."""
    for step in STEPS:
        if on_start is not None:
            try:
                on_start(step, ctx)
            except Exception:                                                # progress plumbing only
                pass
        if not _needs_met(ctx, step):
            ctx.steps.append(StepRecord(step.code, step.name, "skipped", 0.0, "nothing to work on"))
            if observer is not None:
                observer(step, ctx, ctx.steps[-1])
            continue
        with Timer() as t:
            try:
                step.run(ctx)
                rec = StepRecord(step.code, step.name, "ok", 0.0)
            except Exception as ex:                                          # a broken step must not hide the rest
                ctx.flag("step_failed", "error", step.code, f"{type(ex).__name__}: {ex}", exception=type(ex).__name__)
                rec = StepRecord(step.code, step.name, "error", 0.0, f"{type(ex).__name__}: {ex}")
        ctx.steps.append(StepRecord(rec.code, rec.name, rec.status, round(t.seconds, 4), rec.message))
        if observer is not None:
            observer(step, ctx, ctx.steps[-1])


# ---------------------------------------------------------------------------------------------
# assembly
# ---------------------------------------------------------------------------------------------
def _stack_meta(ctx: BuildContext, key: str):
    w, c = ctx.ifg.get(key), ctx.cov.get(key)
    if w is not None and w.pairs:
        return w.pairs[0].track, w.pairs[0].direction
    if w is not None and w.offsets is not None and w.offsets[0]:
        return w.offsets[0][0].track, w.offsets[0][0].direction
    if c is not None and c.epochs:
        return c.epochs[0].track, c.epochs[0].direction
    return None, None


def _interferometric(w) -> InterferometricData | None:
    if w is None or w.los_m is None:
        return None
    return InterferometricData(
        pairs=tuple(w.pairs), los_m=w.los_m, sigma_m=w.sigma_m, coherence=w.coherence, conn_comp=w.conn_comp,
        phase_rad=w.phase_rad, iono_floor_m=w.iono_floor_m, corrections=w.corrections or CorrectionAudit(),
        network=w.network or PairNetwork(n_pairs=len(w.pairs)), coherence_baseline=w.coherence_baseline)


def _offsets(w) -> OffsetData | None:
    if w is None or w.offsets is None:
        return None
    pairs, r, a, p = w.offsets
    return OffsetData(tuple(pairs), r, a, p)


def _backscatter(c) -> BackscatterData | None:
    if c is None or not c.epochs:
        return None
    base = c.baseline
    if base is None:                                            # P10 failed: say so with an empty, cold baseline
        base = Baseline(FrozenDict(), FrozenDict(), FrozenDict(), FrozenDict(), 0, (), 0.0, True, False, None)
    return BackscatterData(c.epochs, FrozenDict(c.gamma0_db), FrozenDict(c.sigma0_db), c.hv_hh_db, c.n_looks, c.valid,
                           c.masks or BackscatterMasks(None, None, None, None), base, c.current_index)


def _capabilities(stacks: dict, anc: AncillaryLayers) -> frozenset:
    caps = set()
    for s in stacks.values():
        if s.interferometric is not None:
            caps.add("M1")
            if s.interferometric.coherence_baseline is not None:
                caps.add("coherence_baseline")
            if any(it.available for it in s.interferometric.corrections.items.values()):
                caps.add("corrections")
        if s.offsets is not None:
            caps.add("M3")
        if s.backscatter is not None:
            caps.add("M2")
            if not s.backscatter.baseline.cold_start:
                caps.add("baseline")
        if s.geometry is not None and s.geometry.vectors_available:
            caps.add("geometry")
        if s.stable is not None and s.stable.mask.available:
            caps.add("stable_mask")
    if anc.elevation_m.available:
        caps.add("terrain")
    if anc.hand_m.available:
        caps.add("hand")
    if anc.landcover.available:
        caps.add("landcover")
    if anc.water_occurrence_pct.available:
        caps.add("water_occurrence")
    return frozenset(caps)


def _fallback_depth(stacks: dict) -> DataDepth:
    """Counts only, for when P10 did not run: the registry's min_epochs gate must never see zeros for data that exists."""
    pairs = max((len(s.interferometric.pairs) for s in stacks.values() if s.interferometric), default=0)
    eps = max((len(s.backscatter.epochs) for s in stacks.values() if s.backscatter), default=0)
    return DataDepth(n_gunw_pairs=pairs, n_gcov_epochs=eps, cold_start=True)


def assemble(ctx: BuildContext) -> SceneBundle:
    stacks: dict = {}
    newest: list = []
    for key in sorted(set(ctx.stack_keys) | set(ctx.geometry)):
        w, c = ctx.ifg.get(key), ctx.cov.get(key)
        if w is None and c is None:
            continue
        track, direction = _stack_meta(ctx, key)
        inter, off, bs = _interferometric(w), _offsets(w), _backscatter(c)
        if inter is not None:
            newest += [p.sec_time for p in inter.pairs if p.sec_time]
        if bs is not None:
            newest.append(bs.epochs[-1].time)
        stacks[key] = StackBundle(key, track, direction, inter, off, bs, ctx.geometry.get(key),
                                  ctx.stable.get(key, StableMask(Dual(), (), ())), p12.stack_quality(ctx, key))
    anc = ctx.ancillary or AncillaryLayers()
    rq = ctx.request
    request = RequestInfo(iso(rq.as_of), iso(rq.t0), iso(rq.t1),
                          tuple(iso(x) for x in rq.event_window) if rq.event_window else None, FrozenDict(rq.triggers or {}),
                          tuple(sorted(rq.active_codes)) if rq.active_codes is not None else None)
    routing = ctx.routing
    depth = ctx.depth or _fallback_depth(stacks)
    caps = _capabilities(stacks, anc)
    has_error = any(f.severity == "error" for f in ctx.flags) or any(s.status == "error" for s in ctx.steps) or \
        bool(ctx.resolved and ctx.resolved.search_degraded)
    status = STATUS_EMPTY if not stacks else (STATUS_PARTIAL if has_error else STATUS_OK)
    parts = ctx.provenance_parts
    prov = Provenance(ctx.bundle_id or f"{ctx.tile.id}-unhashed", CONTRACT_VERSION, parts.get("code_version", ""),
                      parts.get("params_hash", ""), parts.get("inputs_hash", ""), parts.get("inputs", ()), tuple(ctx.steps),
                      parts.get("assumptions", tuple(ctx.assumptions)), parts.get("git_commit"),
                      parts.get("created_at", iso(datetime.now(timezone.utc))))
    return SceneBundle(CONTRACT_VERSION, prov.bundle_id, status, TileInfo.from_tile(ctx.tile), request,
                       max(newest) if newest else None, ctx.grids, FrozenDict(stacks), anc, routing, depth, caps,
                       tuple(ctx.flags), prov)


# ---------------------------------------------------------------------------------------------
def build_scene_bundle(tile, retriever, as_of: datetime | str | None = None, *, ancillary: AncillaryProvider | None = None,
                       active_codes: set | None = None, event_window: tuple | None = None, triggers: dict | None = None,
                       cfg: CommonConfig | None = None, lookback_days: float | None = None, observer=None,
                       on_start=None) -> SceneBundle:
    """Run P01..P13 for one tile and return the frozen bundle.

    as_of         the instant the bundle describes (default: now). Fixing it makes the build reproducible.
    active_codes  measurement codes the active manifests need ("M1", "M2", ...); None = all.
    event_window  (start, end) of the triggering event; excludes that period from baselines and lets P12 test spans.
    triggers      scheduler context passed to routing, e.g. {"quake": {...}, "storm": True, "wind_ms": 6.1}.
    """
    cfg = cfg or load_common_config()
    try:
        grids = make_tile_grids(tile, cfg.grids)
        if grids.g20.shape[0] * grids.g20.shape[1] > cfg.grids.max_g20_pixels:
            raise ValueError(f"canonical G20 grid {grids.g20.shape} exceeds grids.max_g20_pixels={cfg.grids.max_g20_pixels}")
    except Exception as ex:
        raise ChainError("setup", ex) from ex
    as_of_dt = parse_time(as_of) if as_of is not None else datetime.now(timezone.utc).replace(microsecond=0)
    lb = cfg.window.lookback_days if lookback_days is None else lookback_days
    ew = None
    if event_window:
        ew = (parse_time(event_window[0]), parse_time(event_window[1] if len(event_window) > 1 and event_window[1] is not None
                                                      else event_window[0]))
    req = BuildRequest(tile, as_of_dt, as_of_dt - timedelta(days=lb), as_of_dt, ew, dict(triggers or {}),
                       set(active_codes) if active_codes is not None else None)
    ctx = BuildContext(req, cfg, grids, retriever, ancillary)
    run_steps(ctx, observer, on_start)
    return assemble(ctx)
