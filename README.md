# nisar-hazard-engine

Global SAR (NISAR primary, Sentinel-1 fallback) hazard detection engine: retrieval → common preprocessing → 17 independent, parallel hazard pipelines → verdict → serving/UI, with validation and cross-cutting support.

Full design rationale, the current data-reality check, and the pipeline-by-pipeline open-source mapping live in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). The diagram below is the same shape as `SARs_full_workflow.html`.

## Current status — 2026-10-27 (earthquake + tsunami port)

The first two science pipelines are REAL and tested end to end; the plumbing around them is filled in:

* **3.01 `earthquake_coseismic`** — the TypeScript science core of the "Dancing with SARs" app,
  ported onto the SceneBundle contract: detection (k·σ rule + clusters + exploratory null-scene
  screen), ComCat event ranking (R1), Okada-85 forward screen (both nodal planes, the prediction
  referenced with the measurement's rule), UNCATALOGUED source fit (bounded global optimizer),
  within-tier `raw_p` (a rank score, never a probability). `run(bundle, providers=None)`.
* **3.17 `tsunami_ocean_evidence`** (NEW, heavy lane) — event-scoped ocean evidence: seismic
  screening, DART bottom-pressure detection (robust local-quadratic de-tide, k·σ trigger,
  seismic-phase mask extended by the eikonal ray time), Okada+Kajiura source on ETOPO1, NSWE
  simulation (HLL + Audusse hydrostatic reconstruction, MUSCL + SSP-RK2), station comparisons,
  5-level evidence classification. Shares `engine/lib/tsunami/` with 3.14 (later).
* **Shared science libs**: `engine/lib/okada.py` (benchmark-exact DC3D), `engine/lib/los.py` (the
  ONE LOS projection, sign pinned by gate G0 in `tests/test_g0_los_sign.py` — flip the bundle's
  LOS sign and the verdict MUST change), `engine/lib/geo.py`, `engine/lib/detect.py`.
* **Typed results**: `engine/common/result.py` (`PipelineResult`, `EvidenceItem`, `LayerRef`,
  `Source`) — the UI renders evidence without parsing sentences; `raw_p` is displayed as a tier +
  rank score, never a percentage.
* **Registry**: manifest fields `execution` (normal | heavy), `timeout_s`, `needs_sar`; status
  `PENDING` for heavy pipelines deferred to `engine/jobs` (own deadline, progress file,
  content-addressed result cache, cooperative cancel).
* **Verdict stages 4.1–4.5 filled**: collect → calibrate → link_events (event-level conclusions
  live HERE, never inside a pipeline) → select (honest abstention) → persist (`data/verdicts.db`).
* **API**: `/api/health`, `/api/pipelines`, `/api/bundles[/{id}]`, `/api/tiles[/{id}/verdicts]`,
  `/api/events`, `POST/GET /api/runs[/{id}]`, `/api/runs/{id}/layers/{name}`, `/api/jobs/{id}`.
* **Reference adapters real**: R1 ComCat (+provenance), R2 ShakeMap, R3 NGL GNSS, R11 tsunami
  catalog, R15 DART, R16 ETOPO1 (live-verified 2026-10-27). All failures are degraded results,
  never exceptions.
* **UI**: fresh minimal Next.js in `app-ui/` consuming the API only (zero science in TypeScript).
* **Debug hub (`tools/hub.py`)**: local four-tab debug layer over the real engine path — retrieval,
  chain, health, and (new) a **PIPELINES tab** showing the input and output of the featured
  pipelines (earthquake 3.01, hurricane 3.13 tropical_cyclone) run through the registry itself,
  with click-to-expand detail. **Start here: [`Instructions.md`](Instructions.md)** — setup,
  every test command, ready-made demos, the debug layer and the UI explained.

### Final-review pass — defects found by the external review, fixed and pinned (2026-10-27)

1. **False "uncatalogued" verdict** — a catalogued event in the window that publishes no
   mechanism now answers `INSUFFICIENT_EVIDENCE` (rule `events_unscreenable`: a data gap, not
   evidence); `UNCATALOGUED_CANDIDATE` requires a genuinely empty catalog AND a converged fit;
   an unconverged fit reads `NOT_SUPPORTED` (plan 8.4). Tests assert the exact status.
2. **Vertical-fault Okada crash** — the vectorised path raised `NameError: q2` on dip-90
   faults (the cd==0 branch of the part-b displacement), and the uncatalogued fit swallowed
   the exception into a 1e9 sentinel, so no fit could ever converge. Fixed + regression tests,
   including a model-level recovery test (`tests/test_uncatalogued.py`: known vertical
   strike-slip source recovered within 500 m / 8°).
3. **UI ↔ API contract** — the UI read `r.run.status` (the API returns the state FLAT),
   rendered `e.text` (the engine field is `claim`), and swallowed poll errors. Fixed and
   pinned by `test_run_state_is_flat_not_nested`; `next build` passes (`app-ui`: `npm run ci`).
4. **Run parameters were recorded, never applied** — they now ride the run's request triggers
   into the pipeline (`engine/api/jobs._apply_params`), unknown/out-of-range params → 400, and
   the UI has a parameters form; `detail.params` always shows what was applied.
5. **R1/R2 adapters** — `fetch_event_detail` unpacked a `(payload, sha)` tuple as a dict (the
   R2 lookup silently never worked); R2 results are now normalised (always carry `event_id`)
   with a tile-coverage check. Adapter suite: `tests/test_adapters_r1_r2.py` (network-guarded
   units + opt-in live marker: `EQ_LIVE=1 pytest -m live`).
6. **Smaller gaps** — evidence ids are content-derived (no per-process `hash()`); layer
   payloads carry `bounds_lonlat` (plan 6.4); every run writes replay artifacts
   `data/runs/<id>/` (plan 13; replay with `python tools/replay_run.py <run_id>`); `NO_SIGNAL`
   added for quiet tiles (no event + no cluster); UI gained BundlePicker / RunProgress /
   PipelineGrid / EventsPanel; no-science CI guard + Playwright smoke (`app-ui/scripts/`,
   `app-ui/tests-e2e/`).

**Intended structural changes** (raised by the review as "beyond the locked scope" — they ARE
the port design, see [`docs/PORT_DESIGN.md`](docs/PORT_DESIGN.md) decisions 7, 8, 9 and the
target-architecture table): `engine/lib/{los,geo,detect}.py` shared libraries; the tsunami
split into `engine/lib/tsunami/` + pipeline 3.17 (while 3.14 stays SAR-coastal, later); the
registry's heavy lane + `PENDING` + `result.py` (independence allow-list widened accordingly);
the 20 stray tests and mock servers from an unrelated project removed from `tests/`.

See `docs/IMPLEMENTATION_PLAN.md` (engineering spec), `docs/PORT_DESIGN.md` (the design review
the code cites), `docs/common_vs_eq_comparison.md` (engine vs app conventions) and
`docs/READINESS.md` (audit + 2026-10-27 addendum).

## Workflow

```mermaid
flowchart TD
    subgraph RET["1 · DATA RETRIEVAL — engine/retrieval/"]
        direction TB
        SCHED["1.2 Scheduler & Triggers<br/>granule_poller · event_pollers · trigger rules"]
        CLIENTS["1.3 Clients<br/>nisar_client · adapters/ · cache policy"]
        SOURCES["1.1 Sources<br/>M1–M7 measurements · S1–S13 ancillary · R1–R14 reference"]
        STORAGE["1.4 Storage<br/>clips/ · ancillary/ · reference/ · bundles/ · verdicts.db"]
        SCHED -->|decides what / when to fetch| CLIENTS
        CLIENTS <-->|pulls / polled| SOURCES
        CLIENTS -->|writes| STORAGE
    end

    RET -->|clips → bundles| PRE["2 · COMMON PREPROCESSING<br/>engine/common/ — P01…P13, sequential"]
    PRE --> BUNDLE[("SceneBundle<br/>read-only, shared by all pipelines")]

    BUNDLE --> PIPES
    REG["7 · Cross-cutting registry<br/>runs applicable pipelines in a process pool"] -.-> PIPES

    subgraph PIPES["3 · ALL PIPELINES — engine/pipelines/&lt;name&gt;/ (16, independent, parallel)"]
        direction LR
        p01["3.01 earthquake_coseismic"]
        p02["3.02 land_subsidence"]
        p03["3.03 volcanic_deformation"]
        p04["3.04 landslide"]
        p05["3.05 glacier_ice_flow"]
        p06["3.06 permafrost_thaw"]
        p07["3.07 flood_inundation"]
        p08["3.08 deforestation"]
        p09["3.09 wildfire_burn_scar"]
        p10["3.10 river_erosion"]
        p11["3.11 coastal_erosion"]
        p12["3.12 urban_damage"]
        p13["3.13 tropical_cyclone"]
        p14["3.14 tsunami_inundation"]
        p15["3.15 oil_spill"]
        p16["3.16 iceberg_calving_drift"]
    end

    PIPES -->|pipeline, tile, epoch, raw P, status, evidence| VERDICT["4 · VERDICT<br/>engine/verdict/<br/>4.1 collect → 4.2 calibrate → 4.3 link → 4.4 select → 4.5 persist"]
    VERDICT --> SERVE["5 · SERVING & UI<br/>engine/api/ + app/<br/>/tiles · /events · /scanner/status"]
    VALID["6 · VALIDATION & CALIBRATION<br/>engine/validation/<br/>labels, metrics, fixtures"] -.labels fit calibration.-> VERDICT
```

**Rules the diagram doesn't show but the folder structure enforces:** no pipeline ever reads another pipeline's verdict — shared logic only goes through `engine/lib/` (see `tests/test_pipeline_independence.py`). "Not applicable" is a distinct, valid result from "no event found."

### Common preprocessing chain (P01 → P13)

```mermaid
flowchart LR
    P01["P01<br/>granule<br/>resolution"] --> P02["P02<br/>clip"] --> P03["P03<br/>decode<br/>& gate"] --> P04["P04<br/>grid<br/>harmonization"] --> P05["P05<br/>geometry"] --> P06["P06<br/>phase<br/>to LOS"] --> P07["P07<br/>correction<br/>audit"] --> P08["P08<br/>backscatter<br/>prep"] --> P09["P09<br/>ancillary<br/>align"] --> P10["P10<br/>temporal<br/>stack"] --> P11["P11<br/>stable<br/>mask"] --> P12["P12<br/>tile quality<br/>+ routing"] --> P13["P13<br/>provenance"] --> SB[("SceneBundle")]
```

## Codebase map

What's actually in the repo, mapped onto the same 7 stages as the diagram above. Click a section to expand it — if a file's listed here, it exists in the repo at that path right now, nothing below is aspirational.

<details>
<summary><b>1 · DATA RETRIEVAL</b> — <code>engine/retrieval/</code></summary>

**scheduler/**

| File | Purpose |
|---|---|
| `granule_poller.py` | every 6h ASF/CMR query, diff vs ingested set |
| `triggers.py` | quake/storm/fire trigger rules → run list |
| `event_pollers/` | `comcat.py` · `firms.py` · `ibtracs.py` · `gfw.py` · `imerg.py` |

**clients/**

| File | Purpose |
|---|---|
| `nisar_client.py` | asf_search + HDF5 range-read clip |
| `sentinel1_client.py` | fallback sensor for ocean pipelines |
| `cache.py` | TTL disk cache, degraded-mode flag |
| `adapters/` | 14 files, one per R1–R14 reference source |

**sources/ & auth/**

| File | Purpose |
|---|---|
| `sources/` | static registry of M*/S*/R* definitions |
| `auth/secrets.py` | Earthdata login, CDS/FIRMS/GFW keys — centralized |

Writes to `data/clips/` · `ancillary/` · `reference/` · `bundles/` · `verdicts.db`.

</details>

<details>
<summary><b>2 · COMMON PREPROCESSING</b> — <code>engine/common/</code> (P01…P13, sequential chain)</summary>

| File | Purpose |
|---|---|
| `p01_granule_resolution.py` | pair graph, track/frame/direction |
| `p02_clip.py` | tile + 1px margin, requested layers only |
| `p03_decode_gate.py` | fill→NaN, cc≥1 gate, float32 |
| `p04_grid_harmonization.py` | canonical grids G80/G20 |
| `p05_geometry.py` | LOS vector, incidence, layover/shadow |
| `p06_phase_to_los.py` | phase → displacement, per-pixel sigma |
| `p07_correction_audit.py` | records corrections, never applies silently |
| `p08_backscatter_prep.py` | gamma0 dB, HV/HH ratio |
| `p09_ancillary_align.py` | DEM → slope/aspect/HAND onto grids |
| `p10_temporal_stack.py` | epoch table, median/MAD, `cold_start` flag |
| `p11_stable_mask.py` | high coherence / low slope / non-water |
| `p12_tile_routing.py` | tile quality → applicable pipeline list |
| `p13_provenance.py` | checksums, param hash → bundle id |
| `scene_bundle.py` | the read-only object P01–P13 build |

`engine/lib/` sits beside this — `okada.py` · `flood_detect.py` · `offset_tracking.py` · `changepoint.py` · `terrain.py` — the **only** code any pipeline is allowed to import from another pipeline's problem domain.

</details>

<details>
<summary><b>3 · ALL PIPELINES</b> — <code>engine/pipelines/&lt;name&gt;/</code> (16, independent, parallel)</summary>

Every folder has the same 8 files. Pipelines never import each other — only `engine/lib/`.

**example: `earthquake_coseismic/`**

| File | Purpose |
|---|---|
| `manifest.yaml` | applies/uses/own_prep/own_fetch/method/output, pre-filled from the spec |
| `prep.py` | far-field referencing, cycle-jump mask |
| `fetch.py` | R1 ComCat, R2 ShakeMap, S5 faults, R3 GNSS |
| `model.py` | Okada85 + optimizer, both nodal planes |
| `verdict.py` | returns P, status, evidence[] |
| `lib_local.py` | pipeline-only helpers, not shared |
| `tests/` | contract tests |
| `README.md` | human notes, generated from manifest |

**all 16 (+ `_template/` to copy for a new one)**

| # | Pipeline | Sensor | Core algorithm |
|---|---|---|---|
| 3.01 | `earthquake_coseismic` | NISAR | Okada85 |
| 3.02 | `land_subsidence` | NISAR | SBAS WLS |
| 3.03 | `volcanic_deformation` | NISAR | Mogi / dike |
| 3.04 | `landslide` | NISAR | stats + logistic prior |
| 3.05 | `glacier_ice_flow` | NISAR | offset tracking |
| 3.06 | `permafrost_thaw` | NISAR | seasonal regression |
| 3.07 | `flood_inundation` | NISAR | adaptive thresholds |
| 3.08 | `deforestation` | NISAR | CUSUM change detection |
| 3.09 | `wildfire_burn_scar` | NISAR | backscatter Δ |
| 3.10 | `river_erosion` | NISAR | bank-line transects |
| 3.11 | `coastal_erosion` | NISAR | waterline transects |
| 3.12 | `urban_damage` | NISAR | coherence damage proxy |
| 3.13 | `tropical_cyclone` | Sentinel-1 primary | eye/eyewall pattern |
| 3.14 | `tsunami_inundation` | NISAR | reuses `engine/lib/` (3.01 + 3.07) |
| 3.15 | `oil_spill` | Sentinel-1 primary | CFAR dark-spot |
| 3.16 | `iceberg_calving_drift` | NISAR or Sentinel-1 | CFAR + tracking |
| — | `_template/` | — | copy me for a 17th |

</details>

<details>
<summary><b>4 · VERDICT</b> — <code>engine/verdict/</code> (4.1 → 4.5)</summary>

| File | Purpose |
|---|---|
| `collect.py` | 4.1 gathers every pipeline's return fields |
| `calibrate.py` | 4.2 isotonic/Platt once labels exist, else status tiers |
| `link_events.py` | 4.3 quake→landslide→tsunami→urban; exclusivity groups |
| `select.py` | 4.4 best verdict per tile, abstain if below threshold |
| `persist.py` | 4.5 verdicts.db + GeoJSON/COG layers |

</details>

<details>
<summary><b>5 · SERVING &amp; UI</b> — <code>engine/api/</code> + <code>app/</code></summary>

| File | Purpose |
|---|---|
| `api/main.py` | FastAPI entrypoint |
| `api/routes_tiles.py` | `/tiles`, `/tiles/:id/verdicts` |
| `api/routes_pipelines.py` | `/pipelines` — drawer built from manifests |
| `api/routes_events.py` | `/events` — linked events from 4.3 |
| `api/routes_scanner.py` | `/scanner/status` |
| `api/routes_datasets.py` | `/datasets` |
| `app/` | existing Next.js — map, panel, time slider |

</details>

<details>
<summary><b>6 · VALIDATION &amp; CALIBRATION</b> — <code>engine/validation/</code> (supporting, not in the data flow)</summary>

| File | Purpose |
|---|---|
| `labels.py` | ComCat, FIRMS, GFW, DFO/EMS, COOLR, GNSS… |
| `metrics.py` | stratified F1, precision-recall, Brier, false-alarm rate |
| `fixtures/` | seeded synthetic scenes per hazard + null scene |

</details>

<details>
<summary><b>7 · CROSS-CUTTING</b> — <code>engine/registry.py</code> + top-level <code>tests/</code></summary>

| File | Purpose |
|---|---|
| `engine/registry.py` | scans `pipelines/*`, reads manifests, runs applicable ones in a process pool |
| `tests/test_pipeline_independence.py` | static check: no cross-pipeline imports |
| `tests/test_scene_bundle_contract.py` | fields every pipeline can rely on |
| `tests/test_common_truth.py` | engine/common against analytic truth (terrain, HAND, UTM zones, fills, ramps) |
| `tools/debug_hub/` | **unified local debug layer** (`python tools/hub.py`): DATA RETRIEVAL · HEALTH · DATA PROCESSING, live over the real engine path, see `tools/debug_hub/README.md` |
| `tools/sar_debug/` | standalone HTML debug report for retrieval, files and the engine checkout (`cd tools && python -m sar_debug --demo`); engine-independent by design |
| `config/tiling.yaml` | grid scheme — still an open decision, see docs |
| `docs/ARCHITECTURE.md` | full vision, data-reality check, OSS library map |

</details>
