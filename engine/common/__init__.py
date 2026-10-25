"""2 · COMMON PREPROCESSING — P01..P13, sequential, producing one read-only SceneBundle shared by every pipeline.

    from engine.common import build_scene_bundle          # run the chain for one tile
    from engine.common import SceneBundle, load_bundle    # what a pipeline (or its worker process) needs

Pipelines import only `engine.common.scene_bundle` types (and may call `load_bundle`); they never import the chain.
Names are resolved lazily so importing the contract does not pull in the chain's dependencies.
"""
from __future__ import annotations

import importlib

_LAZY = {
    "build_scene_bundle": ".chain", "run_steps": ".chain", "assemble": ".chain", "STEPS": ".chain",
    "load_common_config": ".config", "CommonConfig": ".config",
    "save_bundle": ".bundle_io", "load_bundle": ".bundle_io", "bundle_path": ".bundle_io",
    "assert_bundle_contract": ".contract", "check_bundle": ".contract", "ContractViolation": ".contract",
    "SceneBundle": ".scene_bundle", "CONTRACT_VERSION": ".scene_bundle",
    "AncillaryProvider": ".ancillary", "DirectoryAncillary": ".ancillary", "NullAncillary": ".ancillary",
}

__all__ = sorted(_LAZY)


def __getattr__(name: str):
    mod = _LAZY.get(name)
    if mod is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(mod, __name__), name)
    globals()[name] = value
    return value
