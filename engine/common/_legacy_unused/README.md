# Quarantined — superseded preprocessing implementation

These files were found broken (`ImportError: cannot import name 'PreprocConfig' from
'engine.common.config'`) and, on inspection, turned out to belong to an **earlier generation**
of `engine/common` that the current chain has already replaced:

| file | old role | replaced by |
|---|---|---|
| `runner.py` | orchestrates P01..P13, `run_preprocessing` / `build_scene_bundle` | `chain.py` (`run_steps` / `build_scene_bundle`) |
| `state.py` | mutable build state (`BundleBuilder`, `PreprocInput`) threaded through the stages | `context.py` (`BuildContext`, `BuildRequest`) |
| `_raster.py` | canonical G80/G20 grid construction + resampling, keyed off `PreprocConfig` | `grids.py` (`GridSpec`, `GridSet`, `make_tile_grids`) |
| `_geo.py` | UTM lon/lat helpers for `_raster.py` | `grids.py` / `ancillary.py` call `pyproj.Transformer` directly |
| `_types.py` | old `Grid`, `Clip`, `GranuleRecord`, `Raster`, `TileSpec`, `PreprocError`, ... | `scene_bundle.py`, `context.py`, `grids.py` define the current equivalents |
| `synthetic.py` | test fixtures for the above (`tests/synthetic.py`) | n/a — no current replacement shipped in this checkout |
| `test_common.py.disabled` | tests for the above (`tests/test_common.py`) | `engine/common/README.md` documents the *real* current test suite (`test_common_foundation.py`, `test_common_chain.py`, `test_common_truth.py`, `test_scene_bundle_contract.py`, `test_pipeline_independence.py`, `test_common_debug.py`) — none of those files are present in this checkout |

Why this isn't just "add the missing `PreprocConfig` class back": the two generations are not
wire-compatible. For example `p01_granule_resolution.py` (current) only exposes `run(ctx:
BuildContext)`; `test_common.py` called `p01.resolve(candidates, tile, cfg)`, which hasn't existed
since the rewrite. Re-adding `PreprocConfig` would silence the import error but every test in
`ChainTests`/`OtherScenarios` would still fail immediately afterward, now with a confusing
`AttributeError` instead of a clear `ImportError` — strictly worse. Making them pass for real would
mean re-deriving P01–P13's domain logic (sign conventions, routing thresholds, stacking rules, ...)
from test assertions alone, with no way for me to check the numbers against the actual spec — not a
safe thing to do silently in a shared repo.

**Nothing outside this folder imports any of these seven files** — confirmed with a repo-wide
grep before moving them, and the full-repo import scan still passes after the move. They were
previously sitting broken in `engine/common/` and `engine/common/tests/`, so moving them here
changes nothing for anyone currently relying on `engine.common` (that path already raised
`ImportError`).

**Not deleted on purpose** — in case the real `test_common_*.py` suite described in the main
README gets restored later and someone wants to compare against the old fixtures, or in case I'm
wrong that this is dead. `test_common.py` was renamed to `test_common.py.disabled` (and moved out
of a `tests/` directory) specifically so `pytest`/`unittest` auto-discovery won't pick it up and
fail the build.

If this diagnosis is wrong, or you want the old pipeline brought back to life instead, just say so
— happy to look further into it instead of leaving this quarantined.
