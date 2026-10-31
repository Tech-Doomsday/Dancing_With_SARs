"""4.1 collect — normalise what the registry produced into verdict rows.

One segment = one artifact: collect turns a TileRun (or a bare result mapping) into plain verdict
rows the later stages (calibrate -> link_events -> select -> persist) and the UI consume without
knowing the registry's internals.
"""
from __future__ import annotations

from engine.common.result import PipelineResult, plain


def result_to_row(r: PipelineResult) -> dict:
    """PipelineResult -> verdict row (JSON-able, evidence items flattened to dicts)."""
    d = r.to_dict() if isinstance(r, PipelineResult) else dict(r)
    return {
        "pipeline": d.get("pipeline"),
        "tile": d.get("tile"),
        "epoch": d.get("epoch"),
        "bundle_id": d.get("bundle_id"),
        "status": d.get("status"),
        "summary": d.get("summary") or "",
        "raw_p": d.get("raw_p"),
        "confidence": d.get("confidence"),
        "evidence": [plain(e) for e in (d.get("evidence") or [])],
        "layers": [plain(l) for l in (d.get("layers") or [])],
        "provenance": [plain(p) for p in (d.get("provenance") or [])],
        "data_used": list(d.get("data_used") or []),
        "references_used": list(d.get("references_used") or []),
        "warnings": [str(w) for w in (d.get("warnings") or [])],
        "runtime_s": float(d.get("runtime_s") or 0.0),
        "min_epochs_met": d.get("min_epochs_met"),
        "detail": plain(d.get("detail") or {}),
        "versions": plain(d.get("versions") or {}),
    }


def collect(tile_run) -> list:
    """Every pipeline result of one TileRun -> verdict rows (registry order preserved)."""
    return [result_to_row(r) for r in tile_run.results.values()]


__all__ = ["collect", "result_to_row"]
