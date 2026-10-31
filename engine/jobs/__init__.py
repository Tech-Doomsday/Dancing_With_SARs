"""Heavy-job lane (port design decision 9).

Heavy pipelines (manifest `execution: heavy` — the wave-solver class of work) do not run in the
registry's spawn pool: their deadline is their own (manifest `timeout_s`), their progress is a
file the UI can poll, and their results are content-addressed so re-running an identical job is
free. The registry answers PENDING for such pipelines; this runner turns PENDING into a verdict.
"""
from __future__ import annotations

from .runner import HeavyJobRunner

__all__ = ["HeavyJobRunner"]
