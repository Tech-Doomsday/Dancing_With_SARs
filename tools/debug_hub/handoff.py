"""DATA PROCESSING output -> pipelines.

`pipeline_feed` asks the real `engine.registry.Registry.plan()` what it would do with this bundle (route, gate,
launch) and then checks, per pipeline, every input its manifest declares against what the bundle really holds.
Nothing is simulated: a pipeline that cannot be fed says exactly which input is absent and why.
"""
from __future__ import annotations

from pathlib import Path

from .health import MEASUREMENT_NAMES, S_CHANNEL, S_NAMES, module_state, producers, REPO
from .util import plain

_BUNDLE_CAPS = {"M1": "M1", "M2": "M2", "M3": "M3", "P07": "corrections"}


def _anc_available(b, channel: str) -> bool:
    a = b.ancillary
    if channel.startswith("fact:"):
        return channel[5:] in a.facts
    direct = {"dem": a.elevation_m, "landcover": a.landcover, "water_occurrence": a.water_occurrence_pct}
    if channel in direct:
        return bool(direct[channel].available)
    d = a.context.get(channel)
    return bool(d is not None and d.available)


def _input_state(b, code: str, prod: dict) -> dict:
    name = {**MEASUREMENT_NAMES, **S_NAMES}.get(code, "")
    if code in _BUNDLE_CAPS:
        ok = _BUNDLE_CAPS[code] in b.capabilities
        return {"code": code, "name": name, "state": "present" if ok else "absent",
                "why": f"bundle capability '{_BUNDLE_CAPS[code]}' {'held' if ok else 'not held'}"}
    if code in ("M4", "M5", "M6", "M7"):
        p = prod.get(code, {})
        return {"code": code, "name": name, "state": "no_producer", "why": p.get("how", "no producer")}
    if code.startswith("S"):
        ch = S_CHANNEL.get(code)
        if ch is None:
            return {"code": code, "name": name, "state": "no_channel", "why": f"the bundle has no field that can carry {code}"}
        ok = _anc_available(b, ch)
        return {"code": code, "name": name, "state": "present" if ok else "absent",
                "why": f"ancillary '{ch}' " + ("available" if ok else "not available (nothing in data/ancillary)")}
    if code.startswith("R"):
        p = prod.get(code, {})
<<<<<<< HEAD
        return {"code": code, "name": name, "state": "present" if p.get("produced") else "no_producer",
                "why": f"the pipeline fetches this itself (own_fetch): {p.get('how', 'no adapter')}"}
=======
        return {"code": code, "name": name, "state": "no_channel" if p.get("produced") else "no_producer",
                "why": f"{p.get('how', 'no adapter')}; the bundle has no field for reference data"}
>>>>>>> 8200fa0 (Integrate MOTUS earthquake analysis into Dancing With SARs)
    return {"code": code, "name": name, "state": "absent", "why": "unknown code"}


def pipeline_feed(bundle, registry=None) -> dict:
    from engine.registry import Registry
    reg = registry or Registry()
    results, todo = reg.plan(bundle, reg.manifests)
    launch = {m.name for m in todo}
    prod = producers()
    rows = []
    for m in reg.manifests:
        d = bundle.routing.decisions.get(m.name) if bundle.routing else None
        r = results.get(m.name)
        uses = [_input_state(bundle, c, prod) for c in m.uses_codes]
        own = [_input_state(bundle, c, prod) for c in m.fetch_codes]
        vstate = module_state(m.path.parent / "verdict.py")
        missing = [u["code"] for u in uses if u["state"] != "present"]
        if m.name in launch:
            feed = "LAUNCHES_WITH_GAPS" if missing else "READY"
        elif r is not None:
            feed = {"NOT_APPLICABLE": "NOT_APPLICABLE", "NOT_RUN": "BLOCKED", "INSUFFICIENT_EVIDENCE": "INSUFFICIENT_EVIDENCE"}.get(r.status, r.status)
        else:
            feed = "UNKNOWN"
        rows.append({
            "name": m.name, "code": m.code, "sensor": m.sensor, "min_epochs": m.min_epochs,
            "routing": plain(d) if d is not None else None,
            "registry_status": r.status if r is not None else "WOULD_LAUNCH",
            "reasons": [e.get("reason") for e in (r.evidence if r is not None else []) if isinstance(e, dict)],
            "feed": feed, "uses": uses, "own_fetch": own, "missing_uses": missing,
            "implemented": bool(vstate.get("has_run")), "verdict_state": vstate["state"],
            "gate": r.detail.get("min_epochs") if r is not None and isinstance(r.detail, dict) else None,
        })
    tally: dict = {}
    for r in rows:
        tally[r["feed"]] = tally.get(r["feed"], 0) + 1
    return {"rows": rows, "tally": tally, "run_unknown": bool(reg.cfg.registry.run_unknown),
            "n_implemented": sum(r["implemented"] for r in rows), "scan_errors": reg.scan_errors}


def bundle_summary(b) -> dict:
    stacks = {}
    for k, s in b.stacks.items():
        i, bs = s.interferometric, s.backscatter
        pairs = list(i.pairs) if i else []
        stacks[k] = {
            "track": s.track, "direction": s.direction,
            "gunw_pairs": len(pairs),
            "pair_span": [min((p.ref_time for p in pairs if p.ref_time), default=None),
                          max((p.sec_time for p in pairs if p.sec_time), default=None)] if pairs else None,
            "gcov_epochs": len(bs.epochs) if bs else 0,
            "epoch_span": [bs.epochs[0].time, bs.epochs[-1].time] if bs and bs.epochs else None,
            "pols": sorted(bs.gamma0_db) if bs else [],
            "cold_start": bool(bs.baseline.cold_start) if bs else None,
            "offsets": s.offsets is not None,
            "geometry": bool(s.geometry is not None and s.geometry.vectors_available),
            "stable_mask": bool(s.stable is not None and s.stable.mask.available),
            "quality": plain(s.quality),
        }
    a = b.ancillary
    anc = {n: bool(getattr(a, n).available) for n in ("elevation_m", "slope_deg", "aspect_deg", "hand_m", "landcover", "water_occurrence_pct")}
    anc.update({f"context:{k}": bool(v.available) for k, v in a.context.items()})
    return {
        "bundle_id": b.bundle_id, "contract_version": b.contract_version, "status": b.status, "epoch": b.epoch,
        "tile": plain(b.tile), "request": plain(b.request),
        "grids": {"g80": {"shape": list(b.grids.g80.shape), "dx": float(b.grids.g80.dx), "epsg": int(b.grids.g80.epsg)},
                  "g20": {"shape": list(b.grids.g20.shape), "dx": float(b.grids.g20.dx), "epsg": int(b.grids.g20.epsg)}},
        "capabilities": sorted(b.capabilities), "depth": plain(b.depth), "stacks": stacks,
        "ancillary": anc, "ancillary_status": plain(a.status), "ancillary_facts": plain(a.facts),
        "routing_quality": plain(b.routing.quality) if b.routing else None,
        "landcover_hist": plain(b.routing.landcover_hist) if b.routing else None,
        "flags": [{"severity": f.severity, "step": f.step, "code": f.code, "message": f.message} for f in b.flags],
        "provenance": {"code_version": b.provenance.code_version, "params_hash": b.provenance.params_hash,
                       "inputs_hash": b.provenance.inputs_hash, "git_commit": b.provenance.git_commit,
                       "created_at": b.provenance.created_at, "n_inputs": len(b.provenance.inputs),
                       "inputs": plain(b.provenance.inputs), "assumptions": list(b.provenance.assumptions)},
    }
