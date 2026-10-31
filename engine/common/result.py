"""Typed pipeline results — the one shape the UI (and verdict stages 4.1–4.5) consume.

Moved out of engine/registry.py per the port design ("typed PipelineResult and EvidenceItem live in
engine/common/result.py"); registry.py re-exports them so existing imports keep working. Pipelines may
import this module (it is on the independence allowlist): it is a data contract, never a behaviour.

Honesty rules baked into the shape (port design, "Rules that keep it honest"):
  * Missing input is a STATUS, never a default number: NOT_RUN / INSUFFICIENT_EVIDENCE / PENDING.
  * ERROR carries the exception type and a trimmed traceback in diagnostics; it never reads as "clear".
  * Every number shown in summary or evidence traces to a provenance entry or to a bundle field, by name.
  * raw_p is an uncalibrated, within-tier RANK SCORE in [0, 1] — never a probability; the UI shows
    the status tier and the evidence, not a percentage. confidence is None unless the pipeline can
    justify a number (calibration lands with verdict stage 4.2).
  * Determinism: seeds come from the bundle content hash, so the same bundle gives the same result.
"""
from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

# Statuses the REGISTRY itself synthesises (pipelines never return NOT_MODELLED; the registry adds
# PENDING when a heavy-lane pipeline is deferred to the jobs runner instead of being launched here).
REGISTRY_STATUSES = ("NOT_APPLICABLE", "NOT_RUN", "ERROR", "NOT_MODELLED", "PENDING")


@dataclass(frozen=True)
class EvidenceItem:
    """One reviewable claim. The UI renders these without parsing sentences."""

    id: str                                  # stable within the result, e.g. "forward_model.us6000..."
    kind: str                                # detection | forward_model | null_result | insufficient | ...
    claim: str                               # one sentence, self-contained
    support: dict | None = None              # {"quantity": .., "unit": .., "sigma": ..} or free numbers
    level: str = "info"                      # info | corroboration | decision | caveat
    source_ref: str | None = None            # provenance entry id or bundle field, by name

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass(frozen=True)
class LayerRef:
    """One evidence layer crossing the API: either an inline payload or a persisted artifact."""

    name: str
    kind: str = "raster"                     # raster | vector
    crs: str | None = None                   # e.g. "EPSG:32611" or "EPSG:4326"
    path: str | None = None                  # content-addressed file, when persisted
    content_hash: str | None = None          # sha256 of the artifact bytes
    payload: dict | None = None              # inline transport payload (b64 grid + bounds + hints)
    bounds_lonlat: tuple | None = None       # (W, S, E, N) geographic frame (plan 6.4)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass(frozen=True)
class Source:
    """Provenance for one external record a pipeline used (adapter, url/id, retrieved_at, digest)."""

    adapter: str                             # e.g. "r1_comcat"
    url_or_id: str
    retrieved_at: str                        # ISO-8601 UTC
    sha256: str | None = None                # digest of the response body / file bytes

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class PipelineResult:
    """What every pipeline returns. The registry normalises any legacy shape into this."""

    pipeline: str
    tile: str
    epoch: str | None
    status: str
    summary: str = ""                        # one line the verdict board shows
    raw_p: float | None = None               # uncalibrated within-tier rank score in [0, 1], or None
    confidence: float | None = None          # only when the pipeline can justify a number; else None
    evidence: list = field(default_factory=list)          # list[EvidenceItem | dict]
    layers: list = field(default_factory=list)            # list[LayerRef | dict | str]
    provenance: list = field(default_factory=list)        # list[Source | dict]
    data_used: list = field(default_factory=list)
    references_used: list = field(default_factory=list)
    diagnostics: dict = field(default_factory=dict)       # prep/model numbers a reviewer needs
    versions: dict = field(default_factory=dict)          # pipeline version, lib versions, config hash
    runtime_s: float = 0.0
    warnings: list = field(default_factory=list)
    min_epochs_met: bool | None = None
    bundle_id: str | None = None
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        out = dataclasses.asdict(self)
        out["evidence"] = [_plain_one(e) for e in self.evidence]
        out["layers"] = [_plain_one(l) for l in self.layers]
        out["provenance"] = [_plain_one(p) for p in self.provenance]
        return out


def _plain_one(v: Any) -> Any:
    if hasattr(v, "to_dict"):
        return v.to_dict()
    return v


def plain(v: Any) -> Any:
    """JSON-able conversion for anything a pipeline may put into a result.

    Non-finite floats become None: NaN is not JSON (and a NaN 'number' in the UI would read as a
    value); null is the honest rendering of 'no value'."""
    if isinstance(v, np.generic):
        v = v.item()
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, np.ndarray):
        return [plain(x) for x in v.tolist()]
    if hasattr(v, "to_dict"):
        return v.to_dict()
    if isinstance(v, dict):
        return {str(k): plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set)):
        return [plain(x) for x in v]
    return v


class JobCancelled(RuntimeError):
    """Raised (through the pipeline's progress callback) to stop a heavy job that exceeded its
    manifest deadline or was cancelled. The pipeline MUST re-raise it — swallowing it would turn
    a timeout into a "successful" verdict."""


__all__ = ["REGISTRY_STATUSES", "EvidenceItem", "JobCancelled", "LayerRef", "PipelineResult", "Source", "plain"]
