"""Shared API singletons (runs store + job runner), initialised lazily."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from engine.common.config import data_root
from engine.registry import Registry

from .jobs import JobRunner
from .store import RunsStore


@lru_cache(maxsize=1)
def get_runner() -> JobRunner:
    data_dir = Path(data_root())
    store = RunsStore(data_dir / "verdicts.db")
    return JobRunner(store=store, bundles_dir=Registry().bundles_dir, data_dir=data_dir)
