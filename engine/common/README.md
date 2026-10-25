# engine/common — common preprocessing (P01 → P13) → `SceneBundle`

Drop-in for `engine/common/`. numpy is the only hard dependency (pyproj is used lazily, only for CRSs other than WGS84/UTM).
It imports nothing from `engine.retrieval`, `engine.pipelines`, `engine.lib`, `engine.verdict`, `engine.api`, `engine.registry`
or `engine.validation` — enforced by `tests/test_common.py::BoundaryTests`.

```python
from engine.common import build_scene_bundle, run_preprocessing, PreprocInput, PreprocConfig, TileSpec

inp = PreprocInput(tile=TileSpec("T123", (w, s, e, n)),
                   candidates=granule_records,          # list[GranuleRecord] from the poller / ASF query
                   clip_source=my_adapter,              # implements ClipSource.read_clip (see below)
                   ancillary={"dem": dem_raster, "water": water_raster})
bundle = build_scene_bundle(inp, PreprocConfig.from_mapping(cfg_dict))    # raises PreprocError
result = run_preprocessing(inp)                                           # status/failed_stage/stage_log, never raises PreprocError
bundle.save("data/bundles")                                               # -> data/bundles/<bundle_id>/{layers,stack}.npz + meta.json
```

## The one seam to retrieval
Implement `ClipSource.read_clip(granule, bbox_wgs84, layers) -> Clip` in a thin adapter next to `nisar_client` (this package never opens files).
Return only canonical layer names, each as a `Raster(data, Grid)` on its native grid, plus `Clip.attrs`
(`wavelength_m`, `corrections_applied`, `degraded_mode`, `heading_deg`, `fill_values`).

| canonical name | typical GUNW source (verify against the product spec) | notes |
|---|---|---|
| `unw_phase`, `coherence`, `conncomp` | unwrappedPhase, coherenceMagnitude, connectedComponents | **required** |
| `iono_phase`, `wet_tropo_phase`, `hydro_tropo_phase`, `solid_earth_tide_phase` | phase screens | optional; audited, never applied |
| `los_x`, `los_y`, `incidence` | radarGrid LOS unit vector X/Y, incidenceAngle | optional; else `heading_deg` attr + incidence |
| `mask`, `layover_shadow`, `rg_offset`, `az_offset` | product mask, pixel offsets | optional |
| `gamma0_hh/hv/vv/vh` | GCOV backscatter (linear power) | optional; enables P08/G20 |

## What each stage guarantees
P01 pair graph, dedupe by processing version, bounded history · P02 canonical grids (tile-derived, snapped, identical across epochs) + clip request with 1-px margin ·
P03 fill→NaN, `conncomp ≥ 1` gate on phase (coherence is deliberately **not** gated), float32 · P04 resample to G80 (GUNW) / G20 (GCOV), area-mean for continuous, nearest for categorical ·
P05 LOS E/N/U (target→sensor), incidence, layover/shadow · P06 LOS displacement (**positive toward sensor**) + Cramér-Rao σ, unreferenced ·
P07 correction audit (`engine_applied` is always false) + residual ramp + elevation correlation · P08 γ⁰ dB, HV/HH ratio (explicit skip when no GCOV) ·
P09 DEM → slope/aspect/HAND on padded grids, water mask · P10 epoch/pair tables, per-pixel median/MAD, `cold_start` · P11 stable mask · P12 tile quality + per-pipeline applicability **with reasons** ·
P13 checksums + param hash → deterministic `bundle_id` → frozen bundle (read-only arrays, `MappingProxyType`).

Flags (`bundle.flags`): `SINGLE_PAIR_ONLY, PAIR_NETWORK_DISCONNECTED, COLD_START, BETA_DATA_IN_STACK, DEGRADED_INPUT, GEOMETRY_3D_MISSING, INCIDENCE_INCONSISTENT,
LAYOVER_SHADOW_NOT_AVAILABLE, DEM_MISSING, WATER_MASK_MISSING, BACKSCATTER_UNAVAILABLE, HAND_IS_PROXY, TILE_QUALITY_LOW, HISTORY_PAIRS_SKIPPED`.

## Assumptions to confirm (all are config or documented, none hidden)
1. **Sign conventions**: `phase_means_range_increase=True` and `los_convention="target_to_sensor"`. Flip in `PreprocConfig` if your GUNW says otherwise (tests cover both).
2. **G80/G20** are read as 80 m / 20 m posting. `config/tiling.yaml` is not read; pass its values via `PreprocConfig.from_mapping` / `TileSpec`.
3. `n_looks=100`, `stable_min_coherence=0.6`, `max_temporal_baseline_days=36`, `stack_min_samples=4` are starting points, not calibrated values.
4. **HAND is a local-minimum proxy** (flagged). Plug a routing-based HAND in via `p09_ancillary_align.hand_provider`.
5. `p12_tile_routing.DEFAULT_REQUIREMENTS` is a fallback from `docs/ARCHITECTURE.md`; pass real manifest `applies/uses` as `requirements=`.
6. GUNW-only today ⇒ P08 is a logged skip and backscatter pipelines get `MISSING_BACKSCATTER` until a GCOV clip is supplied.

## Cost
G80 memory ≈ 6 B × n_pairs × pixels (coh float16 + disp float32); G20 backscatter stacks are float16. Measured on a 60 km tile (847×781 G80, 3388×3124 G20, 9 GUNW + 7 GCOV):
~31 s, ~1.1 GB above input. History is re-processed per call — caching processed history is the obvious next optimisation.

## Tests
`python -m unittest discover -s engine/common/tests -t .` (from repo root; stdlib unittest, pytest also works). Synthetic scenes with known truth live in `tests/synthetic.py` and double as fixtures.
