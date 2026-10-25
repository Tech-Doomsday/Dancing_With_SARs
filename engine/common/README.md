# engine/common - common preprocessing (P01 -> P13) -> `SceneBundle`

One chain, one contract. Everything a pipeline may read comes from the frozen `SceneBundle`; the chain itself is never imported by pipelines.

```python
from engine.common import build_scene_bundle, load_bundle, save_bundle
bundle = build_scene_bundle(tile, retriever, as_of="2026-09-20T00:00:00Z", ancillary=DirectoryAncillary("data/ancillary"))
save_bundle(bundle, "data/bundles")                     # content-addressed, arrays as read-only memory maps on load
```

| file | role |
|---|---|
| `chain.py` | `STEPS` (P01-P13), `run_steps` (optional read-only `observer`), `assemble`, `build_scene_bundle` |
| `context.py` | mutable `BuildContext` threaded through the steps, work records, flags |
| `scene_bundle.py` / `contract.py` | the frozen bundle types / the executable contract (`check_bundle(b, deep=True)`) |
| `p01..p13_*.py` | the steps, see the docstring at the top of each |
| `grids.py`, `_terrain.py`, `terrain_set.py` | canonical G80 / G20 grids and resampling; slope, aspect, curvature, HAND, local incidence |
| `layers.py`, `ancillary.py`, `config.py`, `bundle_io.py`, `frozen.py` | layer roles, ancillary providers, config (`config/common.yaml`), persistence, immutability helpers |
| `debug/` | `python -m engine.common.debug`: HTML report of a build on a real extracted product (see `docs/common_vs_eq_comparison.md`) |

Tests: `tests/test_common_foundation.py`, `test_common_chain.py` (planted signals), `test_common_truth.py` (analytic truth: terrain
facing, HAND, neighbouring UTM zone, fills, ramps), `test_scene_bundle_contract.py`, `test_pipeline_independence.py`, `test_common_debug.py`.
Retrieval-level debugging of files and searches lives in `tools/sar_debug/`.
