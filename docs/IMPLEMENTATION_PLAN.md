# Implementation Plan — Porting the Earthquake Science Core into `nisar-hazard-engine` + One UI

**Deliverable set for this session:** this plan **plus** `skeleton/` — a runnable, tested first slice of the port (Okada library, science kernels, pipeline wiring, API, UI scaffold) that drops into the main repo.

**Scope decisions (locked with the team):**

| Decision | Choice |
|---|---|
| Deliverable | Plan + skeleton (implementation continues after review) |
| Plan format | Markdown, repo-ready |
| UI | **Fresh minimal Next.js** consuming the engine's FastAPI only; old `app/` kept as reference until parity |
| Port fidelity | **Idiomatic port** — NumPy/SciPy idiom, same stages, same thresholds, behavior-level tests (no bit-parity contract) |
| Phasing | `earthquake_coseismic` first, then `tsunami_inundation` (second wave, outlined in §14) |
| Run model (UI v1) | **Dataset + event run**: pick an ingested `SceneBundle` → pipeline derives the pair window → ComCat events fetched by the pipeline itself → verdict + evidence |
| EQ v1 scope | Coseismic event mode (app stages A–E **minus** SBAS time-series stacking, which belongs to `land_subsidence` later) |
| Plan depth | Full engineering spec |

---

## 1. Ground truth — what actually exists today

This audit is the basis for every decision below. Verified by direct code inspection (October 2026).

### 1.1 Main repo (`nisar-hazard-engine`) — what is REAL vs what is a STUB

| Component | State | Notes |
|---|---|---|
| `engine/retrieval/clients/*` (NisarClient, RangeIO, cache, decode) | **Real, tested** | ASF/CMR search, HDF5 range-read clips, Zarr ClipStore, TTL caches |
| `engine/retrieval/clients/adapters/r1..r14` | **Stubs** (1 comment line each) | Implied signature `fetch(bbox, t0, t1) -> typed table/raster`; we implement R1/R2 in this effort |
| `engine/retrieval/scheduler/*` | Mostly stub | `granule_poller`/`triggers`/`comcat` poller are scaffolds |
| `engine/common/` P01→P13 chain, `SceneBundle`, `contract.py`, `bundle_io`, grids | **Real, contract-clean, heavily tested** | The measurement path works end-to-end on synthetic + real extracted GUNW (`engine/common/debug/eq_sample.py` serves the app's extracted LA sample through the real retriever protocol) |
| `engine/registry.py` | **Real, tested** | Scan/manifests/route/gate/spawn-pool/deadline/collect all implemented |
| `engine/pipelines/*` (all 16) | **Comment-only scaffolds** | `verdict.py` files contain no `run()`; registry reports `NOT_MODELLED`. **The EQ port is the engine's first real pipeline.** |
| `engine/lib/*` (okada, changepoint, flood_detect, terrain, offset_tracking) | **Stubs** | Nothing vendored; we fill `okada.py` (shared with tsunami) |
| `engine/verdict/*` (collect→persist) | Stubs | The *contract* is defined: pipelines return `PipelineResult`-shaped data; 4.2–4.5 are future work |
| `engine/api/*` | Stubs (comment lines) | FastAPI is **not** a declared dependency yet |
| `tools/debug_hub` | **Real** | Runs the live path `NisarClient → P01..P13 → Registry.plan`; reports per-pipeline readiness via `handoff.pipeline_feed`; `earthquake_coseismic` already shows **READY** |
| Tests | **Extensive for common/registry/retrieval** | `test_pipeline_independence.py` (AST import rules), `test_scene_bundle_contract.py` (13 scene kinds + 24 tamper cases), `test_common_truth.py` (analytic truth) |
| `docs/common_vs_eq_comparison.md` | **Referenced but missing** | Must be authored as part of M2 |

Key engine facts the port must respect:

- **`los_m` is metres, positive TOWARD the sensor, later date minus earlier date** (`scene_bundle.py` conventions; P06: `los_m = sign · phase_sign · λ/(4π) · phase` with `phase_sign = -1`).
- `sigma_m` is the per-pixel 1σ Cramér-Rao σ (coherence+looks), NaN below `radar.min_coherence = 0.02`.
- `iono_floor_m` is a per-pixel layer (product ionosphere screen uncertainty, metres).
- `conn_comp` uint16: `0` = no usable unwrapping (already gated: `phase_rad` is NaN where `conn_comp == 0`).
- G80 grid = **127×127** for a 10 km tile with `halo_px_g80=1`; **tile proper = core slice `[1:126, 1:126]`** (125×125); row 0 is north; pixel centres; UTM CRS of the tile.
- `stack().geometry.los_enu` is the **ground→sensor** unit vector (E,N,Up), consistent with `incidence_deg`; `heading_deg` is clockwise from grid north.
- Config (`config/common.yaml`): `wavelength_m: 0.2384`, `gunw_effective_looks: 100`, `stable.coherence_min: 0.5`, registry `task_timeout_s: 900`, `start_method: spawn`, `run_unknown: false`.
- Pipeline contract: `engine/pipelines/<name>/verdict.py` must define **`run(bundle: SceneBundle)`** returning a `PipelineResult`, a mapping (`status`, `raw_p`/`P`, `evidence`, `layers`, `data_used`, `references_used`, `warnings`, `min_epochs_met`, `detail` — unknown keys are folded into `detail`), or `(raw_p, status[, evidence])`. Statuses must be in `manifest.status_values ∪ {NOT_APPLICABLE, NOT_RUN, ERROR}`.
- Routing (`P12.r_earthquake_coseismic`): `yes` iff ≥ 10 % coherent land **and** (scan mode: any pair | event mode: ≥1 pair with `ref_time ≤ event_start` and `sec_time ≥ event_end`). Missing M1 ⇒ `unknown` ⇒ registry answers `NOT_RUN`.
- Import rules enforced by `tests/test_pipeline_independence.py`: from `engine.common` only `scene_bundle, contract, frozen, bundle_io, layers, ancillary, config`; retrieval **only** through `engine.retrieval.clients.adapters.*`; never another pipeline's package. (A pipeline's own `tests/` are exempt.)
- **Nothing calls `fetch.py` today.** `docs/READINESS.md` prescribes the pattern: *"`run(bundle)` has a fixed signature, so each `fetch.py` needs an injectable provider (module-level seam) so tests run offline"* and *"failure must be a result, not an exception: a failed or empty fetch should map to `INSUFFICIENT_EVIDENCE` with the reason, never to 'no event'"*.
- S5 (faults) has **no bundle channel** and no adapter (confirmed by debug_hub `health.py` channel map) — deferred (§9).
- `pyproject.toml` declares **no dependencies**; `requirements-retrieval.txt` pins numpy, h5py, aiohttp, zarr, pyproj, shapely, pyyaml, python-dotenv, asf_search, scipy. Python ≥ 3.11.

### 1.2 The app (`app.zip`) — what gets ported

- `src/lib/science/` — a **pure, I/O-free** TS science core: stages **A** validation → **B** mask+referencing → **C** measurement → **D** uncertainty+detection → **E** attribution, plus Okada DC3D (validated against `tools/dc3d_reference.py` at rel < 1e-8), Snyder UTM, statistics, fixtures, verdict narrative. 42 tests in `tests/science/` pin the behaviors.
- `src/lib/tsunami/` — NSWE finite-volume solver (HLL + Audusse hydrostatic reconstruction, MUSCL+SSP-RK2), eikonal travel times, DART/ETOPO clients, detection, evidence classifier, runner with a queued→running→ok|error state machine. **Second wave** (§14).
- `src/components/app/` + `src/app/api/` — the reference UI (verdict badges, evidence cards, Mercator-exact raster warp) and the run/cache/persistence patterns we consciously re-design around the engine API.
- **Not in the extract:** `src/lib/data/datasets.ts` / `usgs.ts` (loader + USGS client). Immaterial for the engine port — the loader's job (extracted layers on disk) is superseded by the SceneBundle; the USGS client's job is superseded by the R1 adapter (§9).

---

## 2. Data-model mapping — `ScienceDataset` → `SceneBundle`

The app's pipeline starts from raw extracted rasters; the engine hands the pipeline a **pre-converted, gated, frozen** bundle. Several app stages collapse or vanish. This table is the single source of truth for the mapping.

| App (`ScienceDataset`) | Engine (`SceneBundle`) | Consequence for the port |
|---|---|---|
| `phase` (Float32, radians) + `geometry.wavelength` | `stack.interferometric.los_m` — **already converted** by P06 | **Stage C conversion is superseded.** Pipeline consumes `los_m` directly. Never re-convert from `phase_rad`. |
| `coherence` | `interferometric.coherence` | Same role; gate at `params.coherence_threshold` (default 0.5 = engine `stable.coherence_min`). |
| `components` Uint16 (reject `0` and `65535`) | `interferometric.conn_comp` uint16 (`0` = unusable; 65535 fill handled at P03) | Re-assert `conn_comp != 0` in the pipeline mask (cheap, defensive). |
| `numberOfLooks` (e.g. 119) | Already inside `sigma_m` via Cramér-Rao (`gunw_effective_looks` config) | App's `phaseSigmaRad/combinedSigmaM` retained only for the **reference-window σ** combination (§8). |
| `ionoScreen` stats → iono floor | `interferometric.iono_floor_m` (per-pixel, metres) | Floor logic becomes `max(k·σ_eff, detection_floor, iono_floor_m)` per pixel. |
| `meta` GridMeta (x/y start/spacing, EPSG) | `bundle.grids.g80` (`GridSpec.x/y` axes, `.core_slices()`, `.transform`) | All arrays indexed on G80 core; bounds for layers come from the grid axes. |
| `provenance.referenceTime/secondaryTime` | `pair.ref_time` / `pair.sec_time` (ISO strings, `ref < sec`) | Event window per pair; `flipped` pairs already normalised by P01. |
| `footprintPolygon`, `center` | `bundle.tile.bbox_lonlat`, `.center_lonlat` | Event ranking distance = to footprint centroid ≈ tile centre. |
| `truth` (synthetic validation) | not in bundle | Test-side only: tests recompute the truth field from the fixture spec (`los_truth`). |
| `mode: 'quantitative' \| 'evidence'` | bundle `status` (`ok/partial/empty`) + `capabilities` | "Evidence mode" becomes: partial bundle or missing M1 → honest `INSUFFICIENT_EVIDENCE` / registry `NOT_RUN`. No separate UI mode. |
| Stage A `validateDataset` | `check_bundle(deep=True)` + P01–P13 gates | Replaced by the executable contract + a small set of pipeline-local sanity checks (§8.1). |

**What the pipeline may read** (and nothing else): `bundle.stack()` (or `stacks`), `bundle.request.{event_window, triggers, t0, t1}`, `bundle.tile`, `bundle.grids`, `bundle.ancillary`, `bundle.flags`, `bundle.depth`, `bundle.routing`. Importing the chain (`build_scene_bundle`/`STEPS`/...) from pipeline code is **forbidden** (independence test); tests build bundles via `tests/common_fixtures.py`.

---

## 3. Sign & unit conventions — the "no-bug" table

The single highest-risk area of this port is convention drift between the app, the engine, and Okada's fault frame. Everything below is pinned by tests (§12, T-1..T-4).

| Quantity | App convention | Engine convention | Port rule |
|---|---|---|---|
| LOS displacement sign | `+` = **away** from satellite (range increase); `d = λφ/(4π)` | `los_m` `+` = **toward** the sensor (range decrease), later − earlier | Pipeline works natively in the **engine** convention. `los_engine = −los_app`. Detection class labels: `1 = toward`, `2 = away` (app used the opposite). |
| LOS projection of a 3-D displacement | `projectDisplacementToLOS(d, û_gs) = −(d·û_gs)` (+ = away) | predicted toward-LOS = `+(d·û_gs)` with `û_gs = geometry.los_enu` (already ground→sensor) | Forward model computes `pred_toward = d_enu · los_enu` — **no negation anywhere**. Cross-checked against the fixture truth (`dlos` = toward-sensor peak). |
| LOS unit vector | `losUnitVector(inc, heading, look)` computed from heading+look side | stored per-pixel `geometry.los_enu` (3,n,n), unit, Up ≥ 0 (deep contract check) | Never recompute from heading inside the pipeline; use the stored vector. Fixtures generate self-consistent cubes (`GeomSpec.los_points="ground_to_sensor"`). |
| Heading | `NOMINAL_HEADING {ascending: -12.5, descending: -167.5}` | `heading_deg` clockwise from **grid** north (per tile) | Only used for narrative text; not for math. |
| Okada U1/U2/U3 | U1+ = sinistral, U2+ = normal (hanging wall down), U3+ = opening; rake map `u1 = s·cos(rake)`, `u2 = −s·sin(rake)` | *(none yet — we are authoring it)* | `engine/lib/okada.py` ports the app's validated conventions **verbatim**; the 24 DC3D benchmark rows (rel < 1e-8) + sign tests move to `earthquake_coseismic/tests/test_okada.py`. Tsunami will call the same module — do not "fix" signs there. |
| Fault-frame geometry | `geoFaultDisplacementENU`: centroid offset `(W/2)·cos(dip)` up-dip; `topDepth = max(500, centroidDepth − (W/2)·sin(dip))`; along-strike `(sin s, cos s)`, dip-direction `(cos s, −sin s)` | — | Ported verbatim into `engine/lib/okada.py`. |
| Coordinates | UTM metres (app Snyder series, EPSG 326xx/327xx) | UTM metres (pyproj already a retrieval dependency) | **Drop `utm.ts`**; use pyproj. Keep the two behaviours: force-zone projection for epicentres near a zone edge; non-UTM EPSG (3413/3031) ⇒ skip forward model with an explicit `skipReason`. |
| Wavelength | `NISAR_L_BAND_WAVELENGTH = 0.2360571 m` (exact 1.27 GHz) | `config.common.radar.wavelength_m = 0.2384` (rounded config value) | Bundle-derived quantities (`los_m`, `sigma_m`, `iono_floor_m`) already embed the engine value — the pipeline uses **no wavelength** in v1 (only the cycle-jump heuristic reads `radar.wavelength_m` from config). Never mix the two constants. |
| Distance | haversine, R = 6371.0088 km | — | Ported as-is into `lib_local.py`. |
| Time | ISO-8601 UTC strings / epoch ms | ISO-8601 UTC strings | Parse with `datetime.fromisoformat` (engine `common.context.parse_time` is chain-internal — do not import). |
| NaN | universal no-data sentinel; aggregators skip NaN | NaN for floats, `0` for `conn_comp` | Identical philosophy; port the NaN-skipping aggregators explicitly (no `np.nanmedian` silent differences: NaN-skipping `median` over an empty set returns NaN, matching app). |

---

## 4. Target architecture for pipeline 3.01

### 4.1 Where things live

```
engine/
├── lib/
│   └── okada.py                      # FILLED by this effort — shared physics (EQ + tsunami later):
│                                     #   vectorised DC3D surface displacements, geo-fault placement,
│                                     #   Mw/area/slip scaling, rake→dislocations. ONLY engine/lib code
│                                     #   that crosses the EQ/tsunami boundary.
├── pipelines/earthquake_coseismic/
│   ├── manifest.yaml                 # updated (see §6.6) — the pipeline's data sheet
│   ├── verdict.py                    # run(bundle) — orchestration + status decision (the ONLY entry the registry calls)
│   ├── prep.py                       # own_prep: pair selection, mask, far-field referencing, cycle-jump screen, detrend
│   ├── detection.py                  # ported detection kernel: threshold rule, clusters, null-scene screen, summary
│   ├── model.py                      # forward-model screen (Okada LOS), event ranking input, status mapping, raw_p score
│   ├── fetch.py                      # own_fetch: injectable event provider + live R1/R2 adapters; S5/R3 deferred
│   ├── lib_local.py                  # ported pure kernels: statistics, haversine, LOS projection helper,
│   │                                 #   mulberry32, cluster BFS, layer encoding
│   ├── types.py                      # EqParams (defaults = app constants), EventRecord, PairAnalysis, LayerPayload
│   ├── README.md                     # generated from manifest + human notes
│   └── tests/
│       ├── fixtures_eq.py            # synthetic Okada scenes, fake event provider payloads (dependency-injection)
│       ├── test_lib_local.py         # ported core.test.ts behaviors
│       ├── test_okada.py             # 24 DC3D benchmark rows (rel<1e-8) + sign/scaling tests (ported okada.test.ts)
│       ├── test_detection.py         # ported detection.test.ts behaviors
│       ├── test_model.py             # event ranking, FM screen, status decisions (ported attribution.test.ts behaviors)
│       └── test_pipeline.py          # end-to-end on the engine chain (FakeRetriever + truth bowl) + registry integration
├── retrieval/clients/adapters/
│   ├── r1_comcat.py                  # REAL: USGS FDSN ComCat query → EventRecord rows (no auth needed)
│   └── r2_shakemap.py                # REAL: ShakeMap extent via ComCat product contents (evidence-only use)
└── api/                              # FILLED: FastAPI + job runner + runs store (§10)
    ├── main.py  ├── jobs.py  ├── store.py
    ├── routes_pipelines.py  ├── routes_bundles.py
    └── routes_runs.py  └── routes_events.py
app-ui/                               # fresh minimal Next.js (§11) — consumes the API above, contains ZERO science
```

**Module-placement rules** (mirroring the engine's independence doctrine):

1. Anything needed by **two or more pipelines** goes to `engine/lib/` (today: `okada.py`). Detection kernels stay in the pipeline folder until a second consumer exists — then promote, never cross-import.
2. Pipeline code reads **only** the bundle + its own package + `engine.lib` + `engine.common.{scene_bundle, contract, frozen, bundle_io, layers, ancillary, config}` + `engine.retrieval.clients.adapters.*`.
3. **Fetch seam** (READINESS-mandated): `fetch.py` exposes a module-level provider:

```python
# fetch.py
_provider = None                      # set by tests / API to an offline or alternate source
def set_event_provider(fn): global _provider; _provider = fn
def fetch_events(bbox, t0, t1) -> EventsResult:
    if _provider is not None:
        return _provider(bbox, t0, t1)          # dependency injection — the ONLY seam
    return adapters.r1_comcat.fetch_events(bbox, t0, t1)   # real network path
```

`EventsResult` is a dataclass with `events: list[EventRecord]`, `source: str`, `degraded: bool`, `error: str | None` — **a fetch failure is a result, never an exception**, and maps to `INSUFFICIENT_EVIDENCE` (§8.4). In a spawn-pool worker the module re-imports fresh, so `_provider` is `None` and the live adapter runs — the seam is a test/debug facility only, which is exactly the intended semantics.

### 4.2 `run(bundle)` control flow (v1, coseismic event mode)

```
run(bundle)
 ├─ 0 sanity: M1 stack present? else INSUFFICIENT_EVIDENCE ("no usable NISAR data")
 ├─ 1 pair selection (prep.select_pairs)
 │     event mode: bundle.pairs_spanning(event_window) → 1..N pairs (all evaluated, best kept, all reported)
 │     scan mode:  newest connected pair of the primary stack
 ├─ 2 per pair → prep.analyse_pair(bundle, pair) -> PairAnalysis
 │     mask (finite los ∧ finite σ ∧ coh ≥ thr ∧ conn_comp ≠ 0)
 │     valid < 100 → per-pair INSUFFICIENT, skip
 │     far-field reference (12 % margin on core slice; refCount < 50 → full-frame fallback + warning)
 │     refOffset = median, refSigma = madSigma (std fallback + warning when degenerate)
 │     disp = los_m − refOffset (masked); sigma_eff = √(sigma_m² + refSigma²)
 │     cycle-jump screen (|los| > 0.5·λ ⇒ flagged `cycle_jump_suspect`, excluded from reference stats)
 │     optional planar detrend (default OFF — flag recorded when applied)
 ├─ 3 detection.detection_pass(analysis, params) -> DetectionSummary
 │     thr = max(k·sigma_eff, detection_floor, iono_floor_m)  (per pixel; NaN σ ⇒ statistical term 0)
 │     detected ∈ {0,1=away-toward per engine sign: 1=toward, 2=away}; clusters (BFS 4-neigh, ≥4 px)
 │     null-scene screen (8 scenes, corrLen 8 px, seed 20261021)  ·  credible area = max(1, 4·pixel_area_km2)
 ├─ 4 events: fetch_events(tile bbox + 0.5°, ref−1 d, sec+1 d) → rank (≤400 km of tile centre,
 │     strict window (ref, sec), magnitude ↓, distance ↑, mechanism presence, id tie-break)
 ├─ 5 model.forward_screen(pair_analysis, event) for top-3 ranked events
 │     Okada LOS prediction on strided subgrid ≤ 400 000 points, epicentre-anchored, pyproj projection,
 │     BOTH nodal planes when available (better Pearson r wins)
 │     screen: amplitude ratio ∈ (1/3, 3) ∧ Pearson r > 0.25
 ├─ 6 status decision (§8.4 table) + raw_p within-tier score (§8.5)
 └─ 7 return mapping {status, raw_p, evidence[], layers[], data_used, references_used, warnings, detail}
       layers: detected_u8, displacement_f32, threshold_f32, residual_f32 (vs best event) — b64, core-grid,
               each with bounds_lonlat + units + colour-scale hints (§6.4)
```

Performance envelope: G80 core is 125×125 = 15 625 px — the full flow (detection + null screen + Okada on ≤400 k strided points) is milliseconds-to-seconds, far inside the 900 s registry deadline; no vectorisation heroics required beyond NumPy array ops.

---

## 5. File-by-file port map (TypeScript → Python)

| # | TS source | Python destination | Mode | Notes |
|---|---|---|---|---|
| 1 | `science/statistics.ts` | `lib_local.py` | **full port** | `median`, `mad_sigma` (1.4826·MAD, NaN-skipping), `mean`, `std` (**ddof=1**), `quantile` (linear), `planar_fit` (3×3 Cramer, n<6 or \|det\|<1e-12 → None), `pearson` (NaN-pair skipping, n<3 → NaN) |
| 2 | `science/phase.ts` | *superseded* by P06; conversions kept in `lib_local.py` for tests/docs | partial | `phase_to_displacement = λφ/4π`; wrap convention (−π, π] |
| 3 | `science/los-projection.ts` | `lib_local.py` (`project_los_enu`) | partial | Only the projection helper survives; the vector itself comes from `geometry.los_enu` |
| 4 | `science/uncertainty.ts` | `lib_local.py` (`combined_sigma`) | partial | `σ_eff = √(σ_px² + σ_ref²)`; CRLB part superseded by `sigma_m` |
| 5 | `science/detection.ts` | `detection.py` | **full port** | `run_detection` (rule, stats, weighted anomaly PCA), `detected_cluster_stats` (BFS 4-neigh), `run_null_scene_screen` (mulberry32 + Box–Muller + box filter, 8 scenes / corrLen 8 / seed 20261021) |
| 6 | `science/okada.ts` | `engine/lib/okada.py` | **full vectorised port** | DC3D surface terms (dccon0/dccon2/partA/partB, 4-corner superposition), `geo_fault_displacement_enu`, top-edge variant, `moment_from_mw`, `rupture_area_from_mw`, `slip_from_moment`, `rake_to_dislocations` — conventions verbatim (§3) |
| 7 | `science/attribution.ts` | `model.py` + `fetch.py` | **restructured** | `rank_events` → model.py (pure, takes `EventRecord`s); `forward_screen` → model.py; heuristics `atmosphericPeakMm=30, amplitudeRatioFactor=3, patternCorrelation=0.25` → `types.py` |
| 8 | `science/utm.ts` | *dropped* — pyproj | — | Keep the two behaviours: force-zone projection; non-UTM EPSG ⇒ skip FM with `skipReason` |
| 9 | `science/validation.ts` | `prep.py` (sanity block) + engine contract | partial | Shape/time checks superseded by `check_bundle`; keep EPSG-UTM guard + "pair spans window" check |
| 10 | `science/pipeline.ts` | `verdict.py` | **restructured** | Orchestration moves to `run()`; transport downsample (640) replaced by G80 core resolution (already ≤ 640) |
| 11 | `science/verdict.ts` | `verdict.py` (narrative + credible-area rule) | full port | `credible_area = max(1, 4·pixel_area_km2)`; tone mapping → engine statuses (§8.4) |
| 12 | `science/types.ts` | `types.py` | partial | `EqParams`, `EventRecord`, `DetectionSummary`, `LayerPayload` dataclasses |
| 13 | `science/constants.ts` | `types.py` defaults + `config/common.yaml` | partial | Radar constants live in engine config; algorithm constants in `EqParams` (Appendix A) |
| 14 | `science/fixtures.ts` | `tests/fixtures_eq.py` | full port (test-only) | `mulberry32`, Okada-synthetic scene builder for FM/status tests |
| 15 | `science/gnss.ts` | *deferred* (EQ validation wave, M6) | — | R3 adapter + `validate_against_gnss` port |
| 16 | `science/atmosphere.ts` | *deferred* (`land_subsidence` wave) | — | Stratified troposphere/ramp removal used by the SBAS path only |
| 17 | `science/bayes.ts` | *deferred* (`land_subsidence`) | — | Staged Bayesian causal engine (cluster attribution) |
| 18 | `science/pelt.ts` | *deferred* (`land_subsidence`; engine `lib/changepoint.py` will wrap `ruptures` per ARCHITECTURE §6) | — | |
| 19 | `science/sbas.ts`, `sbas-pipeline.ts`, `stacks.ts` | *deferred* (`land_subsidence` wave, §14) | — | |
| 20 | `app/api/*` (analyses, datasets, external) | `engine/api/*` | restructured | Run lifecycle moves server-side into the engine (§10); result cache replaced by the runs store |
| 21 | `components/app/raster-render.ts` (Mercator warp) | *simplified* — API serves `bounds_lonlat`, UI renders flat | partial | Sub-pixel UTM warp is a documented M5+ nice-to-have (server-side PNG warp via pyproj) |
| 22 | `components/app/{map-panel, results-panel, evidence-viewer}` | `app-ui/` components | re-inspired | Verdict badge colours/status vocabulary inherited (§11) |
| 23 | `lib/tsunami/*` (11 modules) | *wave 2* — `engine/pipelines/tsunami_inundation/` + `engine/lib` additions | outline §14 | Solver/eikonal/detection/evidence; solver test suite (lake-at-rest, dam-break, Ritter, Kajiura) is the port's acceptance spec |
| 24 | `tools/dc3d_reference.py` | *kept as-is* in the app; benchmark rows hard-coded into `test_okada.py` | — | 24 rows, rel < 1e-8 |

---

## 6. Interface specifications

### 6.1 Entry point (`verdict.py`)

```python
def run(bundle: SceneBundle) -> dict:
    """Registry contract: mapping form. All numpy scalars converted to Python natives
    (the registry _plain()s them, but we do it anyway for direct callers)."""
    return {
        "status": "SUPPORTED" | "NOT_SUPPORTED" | "INSUFFICIENT_EVIDENCE" | "UNCATALOGUED_CANDIDATE",
        "raw_p": float | None,          # within-tier score in [0,1] — NOT a calibrated probability (§8.5)
        "evidence": [ {"kind": "...", "text": "...", ...}, ... ],   # JSON-able, every claim linked
        "layers": ["detected", "displacement", "threshold", "residual"],
        "data_used": ["M1 GUNW los/sigma/coherence/conn_comp/iono_floor (pair <id>)", "R1 ComCat (n events)", ...],
        "references_used": ["Okada (1985,1992) DC3D", "Wells & Coppersmith (1994)", ...],
        "warnings": ["Reference region is very small (n=37) — used the full frame", ...],
        "detail": { ...EqDetail... },   # machine-readable summary (§6.3), incl. layer payloads (§6.4)
    }
```

Params (coherence threshold, k, floor, detrend…) come from `EqParams` defaults; the API run request may override them (validated in §10), passed to `run()` via a module-level `set_params_override()`-free design: **the registry signature is fixed**, so overrides enter as a `triggers["eq_params"]` entry on bundle build (`Registry.run_tile(..., triggers={...})` → `bundle.request.triggers`), and `run()` reads `params = EqParams.from_triggers(bundle.request.triggers)`. This keeps the pipeline pure w.r.t. the bundle.

### 6.2 Core dataclasses (`types.py`)

```python
@dataclass(frozen=True)
class EqParams:                         # defaults == app DEFAULT_PARAMS + engine manifest floors
    coherence_threshold: float = 0.5
    reference_strategy: str = "farField"        # or "fullFrameMedian"
    far_field_margin: float = 0.12
    detrend: bool = False
    detection_floor_m: float = 0.01             # manifest: 10 mm
    significance_k: float = 3.0                 # manifest: 3σ
    min_valid_pixels: int = 100
    min_reference_pixels: int = 50
    cluster_min_pixels: int = 4
    max_event_distance_km: float = 400.0        # manifest R1 radius (app ranking used 300, API 400 — engine manifest wins)
    event_buffer_days: float = 1.0
    forward_subgrid_max_points: int = 400_000
    credible_pixel_factor: int = 4
    cycle_jump_fraction: float = 0.5            # |los| > fraction · λ ⇒ suspect

@dataclass(frozen=True)
class EventRecord:                      # from R1 ComCat (adapter) or fixtures
    id: str; magnitude: float | None; place: str; time: str; lat: float; lon: float; depth_km: float | None
    url: str | None = None
    nodal_planes: tuple = ()            # ((strike, dip, rake), (strike, dip, rake)) — 0, 1 or 2 planes

@dataclass
class PairAnalysis:                     # output of prep.analyse_pair
    pair_index: int; granule_ids: tuple; ref_time: str; sec_time: str
    mask: np.ndarray                    # bool (n,n) core
    disp: np.ndarray                    # f32, referenced (NaN off-mask), toward-positive
    sigma_eff: np.ndarray               # f32
    threshold: np.ndarray               # f32 = max(k·sigma_eff, floor, iono_floor)
    ref_offset_m: float; ref_sigma_m: float
    warnings: list[str]; flags: list[dict]

@dataclass
class DetectionSummary:                 # engine-relevant subset of the app ChangeSummary
    valid_pixels: int; detected_pixels: int
    affected_area_km2: float; toward_area_km2: float; away_area_km2: float
    max_toward_m: float; max_away_m: float; threshold_median_m: float
    pixel_area_km2: float; credible_area_km2: float
    clusters: dict                      # {count, largest_km2, sizes_km2[], min_pixels}
    null_screen: dict                   # {scenes, detected_fraction_mean, detected_fraction_max, scenes_with_clusters, note}
    anomaly: dict                       # centroid/orientation/elongation
```

### 6.3 `EqDetail` (the machine-readable payload inside `detail`)

```jsonc
{
  "pairs_analysed": [ {"granule_ids": [...], "ref_time": "...", "sec_time": "...",
                       "valid_pixels": 15001, "ref_offset_m": -0.0021, "ref_sigma_m": 0.0038,
                       "detected_pixels": 402, "affected_area_km2": 39.2, "clusters": {...},
                       "null_screen": {...}, "selected": true} ],
  "events_considered": [ {"id": "us6000...", "magnitude": 6.1, "distance_km": 84.2, "time": "..."} ],
  "forward_screens": [ {"event_id": "...", "nodal_plane": 0, "amplitude_ratio": 1.4,
                        "pattern_correlation": 0.61, "predicted_peak_m": 0.031, "measured_peak_m": 0.044,
                        "skipped": false, "skip_reason": null} ],
  "decision": {"rule": "screen_pass", "credible_area_km2": 1.0, "best_event_id": "..."},
  "layers": { "detected": {...LayerPayload...}, "displacement": {...}, "threshold": {...}, "residual": {...} }
}
```

### 6.4 `LayerPayload` — how evidence rasters cross the API

```jsonc
{ "name": "displacement", "dtype": "<f4", "shape": [125, 125],
  "data_b64": "...",                      // little-endian, row-major, row 0 = north, NaN = no-data
  "bounds_lonlat": [[w,s],[e,n]],          // core-slice corners via pyproj (server-computed once)
  "units": "m (LOS, + = toward sensor)", "colormap": "diverging_teal_terracotta",
  "vmin": -0.05, "vmax": 0.05 }            // hints; UI snaps to a nice scale
```

Layers live only inside the run record (API response / runs store). They are derived, deterministic functions of `bundle_id + params` — same reproducibility contract the app had ("full grids are not persisted, recomputed on demand"), with the runs store keeping summary + layer payloads for v1 convenience (bounded, see §10).

### 6.5 Dependency / requirements additions

```
# requirements-engine.txt (new, applied to the repo root env)
fastapi>=0.115
uvicorn[standard]>=0.30
```

scipy already present via `requirements-retrieval.txt` (used by the uncatalogued-branch optimizer, §8.6). No other new deps: pyproj covers UTM, sqlite3 is stdlib, `httpx`/`urllib` not needed — adapters use stdlib `urllib.request` with the timeout/retry knobs from `settings.net`.

### 6.6 `manifest.yaml` update (delta from the scaffold)

- `own_prep`: append "cycle-jump screen (|los| > 0.5·λ suspect-flag), reference-window σ combination".
- `method`: add "status decision table + within-tier score (see docs/IMPLEMENTATION_PLAN §8.4–8.5)".
- `fetch_codes`: keep `[R1, R2, S5, R3]` but annotate in `own_fetch` that v1 implements R1+R2; S5 has no bundle channel; R3 deferred (both degrade honestly).
- `status_values`: unchanged (`UNCATALOGUED_CANDIDATE` already whitelisted).
- Keep `min_epochs: null`.

---

## 7. `engine/lib/okada.py` — the shared physics library

**Why here:** `tsunami_inundation`'s design explicitly calls `engine/lib/okada.py` (ARCHITECTURE §6, 3.14) — this is the only engine-wide library this effort writes.

**Public API (final):**

```python
okada_surface_displacement_raw(x, y, depth, dip_deg, al1, al2, aw1, aw2, u1, u2, u3, alpha=0.5)
    -> tuple[ux, uy, uz] | singular flag        # scalar reference path — MUST stay exact (benchmarks)
okada_surface_displacement(x, y, fault, alpha=0.5)
    # friendly wrapper: along-strike [-L/2, +L/2], down-dip aw=[-W, 0]; fault = OkadaFault dataclass
# vectorised twin: okada_surface_grid(east_m, north_m, fault, ...) -> (uE, uN, uU) arrays
#   — vectorisation strategy: evaluate dccon0 once per fault, broadcast the closed-form dccon2 terms
#     over the grid, keep the 4-corner superposition and the singularity rejection per point.
geo_fault_displacement_enu(fault: GeoFault, centroid_depth_m, east_m, north_m) -> (e, n, up, singular)
geo_fault_displacement_enu_top_edge(fault, top_edge_depth_m, east_m, north_m)   # tsunami will use this
moment_from_mw(mw) = 10**(1.5·mw + 9.1)          # Hanks & Kanamori
rupture_area_from_mw(mw) = 10**(0.91·mw − 3.49) · 1e6    # Wells & Coppersmith 1994, m²
slip_from_moment(moment_nm, area_m2, shear_modulus=3.0e10)
rake_to_dislocations(slip_m, rake_deg) -> (u1, u2, u3)   # u1 = s·cos(rake), u2 = +s·sin(rake)  (kernel: +U2 = thrust)
```

**Faithfulness gotchas** (each one pinned by a named test in `test_okada.py`):

1. `nearlyZero` snapping at `1e-5`; `kxi`/`ket` branch switches with the `r+xi < 1e-6` thresholds.
2. Singularity rejection: `q == 0 ∧ ((xi0·xi1 ≤ 0 ∧ et0·et1 == 0) ∨ (et0·et1 ≤ 0 ∧ xi0·xi1 == 0))` → NaN + `singular=True`.
3. Dip clamping when `|cos dip| < 1e-10` (vertical faults).
4. Four-corner sign summation `sgn = (j+k == 1 ? −1 : +1)` and image-source (part B) superposition.
5. `aw = [−W, 0]` convention (positive aw = up-dip; top edge at `fault.depth`).
6. U1+ = sinistral / U2+ = normal / U3+ = opening (empirical sign tests, §12 T-3).
7. `geoFaultDisplacementENU` places the fault so the **centroid** sits at `centroid_depth`: horizontal offset `(W/2)·cos(dip)` up-dip along `(cos strike, −sin strike)`, `top_depth = max(500, centroid_depth − (W/2)·sin(dip))`.

**Benchmarks:** the 24 rows of `tests/science/okada.test.ts` (generated from `tools/dc3d_reference.py`, a port of Okada's official DC3D Fortran) — every component must match at **rel < 1e-8**, `singular == False` for all. These rows are hard-coded into `earthquake_coseismic/tests/test_okada.py`; if a future `okada_wrapper`/pyrocko swap happens (ARCHITECTURE §6 recommends it), this test file is the gate.

**No mock rule:** no fallback "approximation mode" — if the exact DC3D terms are wrong the tests fail, loudly.

---

## 8. Detection, screening & scoring spec

### 8.1 Pipeline-local sanity checks (replacing Stage A)

Executed inside `run()` before anything else (the deep bundle contract already ran at build time):

1. `bundle.stacks` non-empty and `bundle.stack()` has `interferometric` (else `INSUFFICIENT_EVIDENCE`, reason "no usable NISAR data (M1) for this tile/window").
2. EPSG is UTM (32601–32660 / 32701–32760) else warning + forward-model disabled (pyproj still fine, but we keep the app's honesty guard).
3. `bundle.check warnings` — engine flags scanned for `los_convention_suspect`, `pairs_flipped` (surfaced into evidence).

### 8.2 Mask & reference (Stage B/C ported onto the bundle)

- Mask: `isfinite(los) ∧ isfinite(coh) ∧ coh ≥ params.coherence_threshold ∧ (conn_comp != 0)` on the **core slice**.
- Abort per pair: valid < `min_valid_pixels` (100) → pair skipped, contributes `INSUFFICIENT_EVIDENCE` evidence.
- Reference: far-field margin `max(2, round(0.12·n))` rows/cols of the core slice; `refCount < 50` → fallback to all masked pixels **+ warning** (ported verbatim).
- `ref_offset = median(los[ref])`, `ref_sigma = mad_sigma(los[ref])` with the **std fallback + warning** when MAD is degenerate.
- `disp = los − ref_offset` on mask; `sigma_eff = sqrt(sigma_m² + ref_sigma²)`.
- Cycle-jump screen (engine-manifest addition absent from the app): `|los| > params.cycle_jump_fraction · λ` → pixel flagged `cycle_jump_suspect`, excluded from **reference statistics** and reported with a count in evidence; it stays in detection (a real 0.12 m offset signal is exactly what we hunt, and `σ`/floor still gate it) — the flag is diagnostic, never silently corrective (principle 3 of ARCHITECTURE).
- Optional planar detrend (default off): `planar_fit` over reference pixels (n ≥ 6, |det| > 1e-12), removal + warning recorded; degenerate fit → skipped + warning (ported).

### 8.3 Detection kernel (Stage D ported)

- Threshold per masked pixel: `thr = max(params.significance_k · σ_eff, params.detection_floor_m, iono_floor_m)`; NaN σ ⇒ statistical term 0 (floor governs) — ported from the app's exact rule.
- `detected = |disp| > thr → (disp > 0 ? TOWARD : AWAY)` — **engine sign labels** (§3).
- Clusters: BFS 4-neighbourhood on detected pixels, `min_pixels = 4`, sizes descending, km² from `|dx·dy|/1e6`.
- Null-scene screen: 8 synthetic scenes, correlation length 8 px, master seed `20261021`, per-scene seed `+s·7919`, mulberry32 + Box–Muller, box filter with the exact √(1/kernelN) normalisation, same threshold rule — output `{scenes, detected_fraction_mean/max, false_area_km2_mean, scenes_with_clusters, note: "exploratory — NOT a calibrated false-alarm rate"}` (note text ported verbatim; honesty is a feature).
- Anomaly stats: |disp|-weighted centroid, principal-axis orientation (degrees from east), elongation `√(λmax/λmin)` (∞ when λmin = 0).

### 8.4 Status decision table (app hypotheses → engine statuses)

| Situation | Status | Evidence carries |
|---|---|---|
| Screen pass on the best ranked event (ratio ∈ (1/3, 3) ∧ r > 0.25) | `SUPPORTED` | event id, Mw, ratio, r, cluster stats, null-screen note |
| Detection present, event present, screen fails (either criterion) | `NOT_SUPPORTED` | the screen numbers + which criterion failed + "correlation is not causation" note |
| Detection present, **no catalog event** in window, cluster area ≥ credible area, quadtree fit converged | `UNCATALOGUED_CANDIDATE` | fit parameters + residuals + candidate extent |
| Detection present, no catalog event, fit not converged / area < credible | `NOT_SUPPORTED` | "isolated or sub-credible signal; no catalog event within 400 km" |
| No detection above threshold | `NOT_SUPPORTED` | null-hypothesis language ported verbatim: "no detection above threshold — NOT that the ground did not move" |
| No usable data (M1 missing / < 100 valid px / every pair skipped) | `INSUFFICIENT_EVIDENCE` | per-pair reasons |
| Event provider failed or unset → events unknown | `INSUFFICIENT_EVIDENCE` | provider error verbatim — **never** silently reported as "no event" (READINESS rule) |
| (Registry-synthesised) routing says no / unknown / not launched | `NOT_APPLICABLE` / `NOT_RUN` | routing reasons — not the pipeline's concern |

`NOT_MODELLED` is registry-synthesised only (no `run()`), never returned by us.

### 8.5 `raw_p` — within-tier score (documented, honest)

Until calibration exists (engine 4.2 will fit isotonic/Platt on labels), `raw_p` is a **within-tier score in [0, 1]**, never a probability. Formula (deterministic, documented in evidence as such):

```
s_amp  = min(ratio, 1/ratio)                          # ∈ (0, 1], 1 when prediction == measurement
s_corr = clamp((r − 0.25) / 0.75, 0, 1)               # Pearson margin above the screen floor
s_area = clamp((affected_km2 − credible_km2) / (10·credible_km2), 0, 1)
s_null = 1 − clamp(null_screen.detected_fraction_mean / 0.05, 0, 1)
raw_p  = 0.40·s_amp + 0.25·s_corr + 0.20·s_area + 0.15·s_null
```

For `UNCATALOGUED_CANDIDATE`, `s_amp/s_corr` use the fit's residual-based equivalents (1 − normalised RMS / peak). For `NOT_SUPPORTED` tiers `raw_p` still carries the same score so the UI can *order* results inside a tier.

### 8.6 Uncatalogued branch (v1, honest simplifications)

Trigger: detected clusters ≥ credible area ∧ no catalog event in window. Fit: one rectangular source on a quadtree-subsampled field (subdivide until ≤ 400 000 points, keep high-gradient cells), bounded global optimizer `scipy.optimize.differential_evolution` over `(east, north, depth, log10_length, log10_width, slip)` with strike from the cluster principal axis, dip fixed 90°, both rake signs (±180) evaluated. **v1 simplifications are recorded in evidence** (dip fixed; strike from PCA; uniform half-space). Convergence check: best residual RMS < 0.7 × field robust σ, else no candidate.

### 8.7 Referencing the prediction — fix discovered during the port (M0 outcome)

The TS `forwardModelCheck` compares the *referenced* measurement against the *raw* Okada field, whose elastic-model datum is arbitrary. On a 10 km tile the datum (and the strong opposite-sign lobe) dominates the Pearson screen — the skeleton's first end-to-end run produced r ≈ −0.4 for a physically consistent source. **Rule adopted:** the prediction is referenced with the *measurement's* rule (median of the prediction over the same reference region removed) before ratio/correlation, in both `forward_screen` and the residual layer (`model._reference_prediction`). This asymmetry fix is ported into the contract ("both fields referenced identically, or the screen measures the datum, not the signal") and is pinned by the synthetic-truth test (T-8 SUPPORTED case).

---

## 9. Retrieval adapters (R1/R2 now, R3/S5 deferred)

All adapters live in `engine/retrieval/clients/adapters/` (the only retrieval imports a pipeline may make), use **stdlib `urllib.request`** with `settings.net` timeouts/retries, and cache responses via the existing `SearchCache` (TTL 21600 s) keyed by the canonical query — a request-keyed cache that also absorbs duplicate R1 calls if other pipelines declare R1 later.

### 9.1 `r1_comcat.py` — REAL implementation

```
GET https://earthquake.usgs.gov/fdsnws/event/1/query
    ?format=geojson&starttime=<ref−1 d>&endtime=<sec+1 d>
    &minmagnitude=4&latitude=<c_lat>&longitude=<c_lon>&maxradiuskm=400&orderby=magnitude&limit=200
```

- Parse features → `EventRecord` (id, mag, place, time ISO, lat/lon, depth km, url).
- Nodal planes: prefer `properties.products["focal-mechanism"][0]` (both planes when present), else moment-tensor `properties["nodal-plane-1-{strike,dip,rake}"]` / `nodal-plane-2-…`.
- Failure modes: HTTP error / timeout / malformed JSON → `EventsResult(degraded=True, error=...)` — the pipeline answers `INSUFFICIENT_EVIDENCE` with the verbatim reason.
- No credentials required; a 15-minute disk cache absorbs UI re-runs.

### 9.2 `r2_shakemap.py` — REAL, evidence-only

Fetches the ShakeMap product `extent.json` (via the same FDSN query's `products["shakemap"][0].contents["download/extent.json"].url`) for the primary event; used **only** as corroboration evidence ("expected felt extent covers/does not cover the tile"). Absence of a ShakeMap product is a note, never a failure.

### 9.3 Deferred with contracts

- **R3 GNSS** (`r3_gnss.py`, Nevada Geodetic Lab step-1 offsets) — milestone M6; the pipeline treats its absence as a warning only (validation is optional evidence).
- **S5 faults** — no bundle channel and no adapter today (confirmed via debug_hub `health.py`); deferred to the ancillary wave; the manifest annotation keeps this visible.

---

## 10. Engine API + run store (the "5 · SERVING" slice)

### 10.1 Endpoints (all JSON, `{ok: true, ...}` / `{ok: false, error}` envelope — the app's convention, kept)

| Method & path | Purpose | Notes |
|---|---|---|
| `GET /api/health` | process + data-dir presence | `{engine_root, data_dir, bundles_dir, ok}` — presence-only, like the app's `/api/engine/status` |
| `GET /api/pipelines` | registry catalogue | `Registry().describe()` + `implemented: bool` (module has `run()`) + `status_values` |
| `GET /api/bundles` | list ingested bundles | scan `<data>/bundles/*/bundle.json`: id, tile, epoch, status, depth, routing summary, created_at |
| `GET /api/bundles/{id}` | one bundle's meta + per-pipeline routing decisions | no arrays — metadata only |
| `GET /api/events?lon&lat&days&min_magnitude` | live ComCat context (R1 adapter) | for the UI's context display; 400 on bad params, 502 on upstream failure (error surfaced verbatim) |
| `POST /api/runs` | start a run | body `{bundle_id, pipelines: ["earthquake_coseismic"], params?: {coherence_threshold?, significance_k?, detection_floor_m?, detrend?}}` → `{ok, run_id}`; 404 unknown bundle; 400 invalid params; 409 a run is already active (single-flight, like the app's tsunami runner) |
| `GET /api/runs` | recent runs | from the runs store |
| `GET /api/runs/{id}` | run + results | `{status: queued|running|ok|error, progress, results: [PipelineResult.to_dict() + detail], error}` |
| `GET /api/runs/{id}/layers/{name}` | one evidence layer | `LayerPayload` (b64 + bounds + hints) |

### 10.2 Job lifecycle (`engine/api/jobs.py`)

- `ThreadPoolExecutor(max_workers=1)` + module-level run table (`{id: RunState}`) — mirrors the app's single-flight tsunami runner (several-hundred-MB arrays make concurrency a memory hazard; one run at a time is the honest v1).
- State machine `queued → running → ok | error` with `progress = {stage, pair, t0}`; **DB write failures never block science** (best-effort audit, like the app).
- Execution path: `load_bundle(data/bundles/<id>, mmap=True)` → `Registry().run_tile(tile, retriever=None, bundle=bundle, names=[...], workers=0)` — **inline mode**: the process pool is for the background scanner; UI runs are single-user and inline keeps artifacts, flags and the fetch seam observable in-process. (Scaling note: §14.)
- The registry routes/gates exactly as in production — the UI shows `NOT_APPLICABLE`/`NOT_RUN` verdicts for the other 15 pipelines honestly (the drawer is manifest-driven from day one).

### 10.3 Runs store (`engine/api/store.py`, stdlib sqlite3)

```sql
CREATE TABLE IF NOT EXISTS runs(
  id TEXT PRIMARY KEY, created_at TEXT, bundle_id TEXT, tile TEXT,
  pipelines TEXT, params TEXT, status TEXT, error TEXT, duration_s REAL);
CREATE TABLE IF NOT EXISTS verdicts(
  run_id TEXT, pipeline TEXT, tile TEXT, epoch TEXT, raw_p REAL, status TEXT,
  result TEXT,                       -- full PipelineResult.to_dict() JSON (incl. detail.layers)
  PRIMARY KEY(run_id, pipeline));
```

This is deliberately the first concrete slice of `engine/verdict/persist.py` (4.5): when the verdict stages land, `persist.py` takes ownership of `data/verdicts.db` and the API store narrows to run bookkeeping only. Bounded memory: completed runs evicted oldest-first beyond 10 in the in-memory table (results remain queryable from sqlite).

---

## 11. The UI — fresh minimal Next.js on the engine API

### 11.1 Principles

1. **Zero science in TypeScript.** No Okada, no statistics, no projections. A CI guard (simple grep in the UI package for `okada|unwrapped|los_m|sigmoid`) keeps it that way. If it isn't an API call, it doesn't belong.
2. **The engine is the single source of truth** — pipeline catalogue, statuses, evidence, layers all come from `/api/*`. The UI renders honest states it doesn't interpret.
3. **Inherited honesty conventions** from the reference app (they cost nothing and are the product's soul): status names verbatim from the engine; "within-tier score, not a probability" label on `raw_p`; evidence lists with linked claims; limitations paragraph on every result view; degraded states rendered as information, never as errors hidden behind a spinner.
4. Minimal stack: **Next.js (App Router) + TypeScript + Tailwind 4 + Leaflet + lucide-react**. No state library; server data via a tiny typed client with self-scheduling polling (the tsunami panel's proven pattern: next poll only after the previous settles, ≥ 1.5 s).

### 11.2 Design tokens (inherited from the reference app for continuity)

Page `#FAF9F6`, ink `#1C1A17`, muted `#8A837B`, teal `#2C5F58`, terracotta `#9E3B2C`, gold `#B08D57`, borders `#E7E2DA`. Diverging displacement scale: terracotta ↔ teal (toward/away — **engine sign**, legend says so explicitly).

### 11.3 Pages & components

```
app-ui/
├── src/app/page.tsx                  # overview: health strip, pipeline grid (manifest drawer),
│                                     # bundle picker cards, run launcher, recent runs
├── src/app/runs/[id]/page.tsx        # run view: progress, verdict card, evidence, layer map, warnings
├── src/components/
│   ├── StatusBadge.tsx               # engine status → colour/label (table below)
│   ├── BundlePicker.tsx              # bundles from /api/bundles: tile, epoch, pairs, routing badges
│   ├── RunLauncher.tsx               # params form (coherence_threshold, significance_k, detection_floor_m, detrend)
│   ├── RunProgress.tsx               # queued/running card, aria-live, elapsed timer
│   ├── VerdictCard.tsx               # status badge + raw_p tier bar + headline narrative + best event
│   ├── EvidenceList.tsx              # evidence items (icon per kind), data_used, references_used
│   ├── WarningsPanel.tsx             # amber warnings + limitations paragraph
│   └── LayerMap.tsx                  # Leaflet + ImageOverlay(bounds_lonlat) + canvas colourmap + legend + hover readout
├── src/lib/api.ts                    # typed client (fetch wrapper, error envelope, polling helper)
└── src/lib/types.ts                  # API response types only (mirror §10 shapes)
```

**Status badge map (engine statuses → UI):**

| Status | Colour | Label |
|---|---|---|
| `SUPPORTED` | solid teal | "supported by evidence" |
| `UNCATALOGUED_CANDIDATE` | outline teal | "uncatalogued candidate" |
| `NOT_SUPPORTED` | neutral gray | "not supported by evidence" |
| `INSUFFICIENT_EVIDENCE` | gold | "not enough evidence" |
| `NOT_APPLICABLE` / `NOT_RUN` | light gray | "did not run here" / "not launched" |
| `NOT_MODELLED` | dashed gray | "not modelled yet" |
| `ERROR` | terracotta | "run failed" |

**LayerMap contract (deliberately projection-free):** the API provides `bounds_lonlat` + b64 float grid + colour/`vmin`/`vmax` hints; the client decodes to a canvas (NaN → alpha 0), colourises (displacement diverging / threshold sequential / residual diverging / detected binary), and places it as an `L.imageOverlay` on the provided bounds. Sub-pixel UTM warping is deferred (§5 note 21) — at 10 km tile scale the lonlat-bbox rectangle is visually exact; the known approximation is stated in the map footer ("raster shown on its geographic bounding box").

### 11.4 What the UI deliberately does NOT do (v1)

No map-click AOI runs (needs the task-queue decision, ARCHITECTURE §10), no event picker driving runs (events are derived from the pair window by the pipeline — the picker is display-only context), no calibration UI, no time slider (one bundle = one epoch set). Each of these is an M5+ item behind an API extension, not a UI hack.

---

## 12. Test strategy & the no-mock policy

### 12.1 Policy (worth stating precisely because the team asked for "no mock things")

- **Production code paths are always real.** There is no fake data mode, no synthetic fallback, no "demo" switch anywhere in `engine/`.
- The **only** seams are (a) the fetch provider injection in `fetch.py` (READINESS-mandated, exists so tests run offline and so the API can point runs at alternate sources) and (b) pytest fixtures building synthetic scenes — the engine's own established pattern (`tests/common_fixtures.py` exists exactly for this; synthetic *validation* scenes are also the ARCHITECTURE §10 remedy for the young archive).
- Degradation is always a **status + reason**, never invented data: provider failure → `INSUFFICIENT_EVIDENCE` with the provider's error string; missing ShakeMap → a note; missing GNSS → a warning.
- Every numeric kernel is pinned by behavior tests ported from the app's suite; every convention decision in §3 is pinned by a test that would catch a sign flip.

### 12.2 Test matrix (ported behaviors → new files)

| ID | Source (app) | New test | What it pins |
|---|---|---|---|
| T-1 | `core.test.ts` statistics block | `test_lib_local.py` | median (even/odd/NaN/empty), `mad_sigma` ×1.4826, std ddof=1 ≈ 2.138 on the canonical sample, quantile linear interpolation, planar_fit recovery `0.5i − 0.25j + 3` (9 dp) + degenerate→None, pearson ±1 / alternating < 0.9 |
| T-2 | `core.test.ts` phase/LOS block | `test_lib_local.py` | `phase↔displacement` round-trip; wrap convention; LOS unit norm; projection sign (`+up ⇒ toward-positive` in engine convention) |
| T-3 | `okada.test.ts` | `test_okada.py` | **24 benchmark rows rel < 1e-8**; buried-fault ≈ 2-D screw analytic (±20 %); U1 sinistral / U2 normal / U3 opening jump signs; far-field decay < 0.01 m @ 200 km; Mw/area/slip scaling bounds |
| T-4 | `okada.test.ts` geo placement | `test_okada.py` | `geo_fault_displacement_enu` centroid placement: top depth floor `max(500, …)`, up-dip offset, ENU↔fault-frame rotation round-trip on symmetric faults |
| T-5 | `detection.test.ts` | `test_detection.py` | masking vs classification counts (8×8 case: 61/3/4), NaN σ → floor governs (no crash, thr ≈ floor), clusters 4-neighbourhood (diagonal ≠ connected, min-px rule, sizes km²), null-screen determinism (same seed ⇒ same fractions; corrLen 16 ⇒ note text), all-masked → `scenes: 0` + note |
| T-6 | `attribution.test.ts` ranking | `test_model.py` | window/distance/mechanism ranking, magnitude-None sorts last but kept, exclusion reasons carry "window"/"km from the footprint centroid", deterministic `id` tie-break |
| T-7 | `attribution.test.ts` FM | `test_model.py` | epicentre shift moves predicted argmax; grid-origin invariance (identical prediction at same geographic point); non-UTM EPSG → skipped with reason; missing nodal plane → skipped with reason |
| T-8 | `pipeline.test.ts` (integration) | `test_pipeline.py` | on the **engine chain**: EQ scene (`dlos=+0.05` truth bowl) + fixture event → `SUPPORTED`, affected area > 0, residual layer present; null scene (`dlos=0`) → `NOT_SUPPORTED` with null-hypothesis evidence; `detection_floor` unreachable (coh 0.97-like) → `INSUFFICIENT_EVIDENCE`; provider failure → `INSUFFICIENT_EVIDENCE` with verbatim reason |
| T-9 | engine suite | `test_pipeline.py` | `Registry.run_tile(workers=0, names=["earthquake_coseismic"])` returns `PipelineResult` with manifest-legal status; `test_pipeline_independence.py` + `test_scene_bundle_contract.py` stay green with the new code |
| T-10 | app UI smoke | *not ported* | UI verified by one Playwright pass in M5 (launch run → verdict renders); no TS unit tests |

**Fixture plan:** `test_pipeline.py` reuses `tests/common_fixtures.py` — `make_scene(tile, pairs=((0,1),(1,2),(2,3)), dlos=±0.05)` → `FakeRetriever` → `build_scene_bundle(..., event_window=(days[2], days[3]))` → `assert_bundle_contract(bundle, deep=True)` → `run(bundle)` with the injected fixture provider (`fixtures_eq.py` returns real-shaped `EventRecord`s whose fault geometry matches the truth bowl's location). The truth field `los_truth` gives the residual assertion (MAE < 10 mm on the bowl, mirroring the app's synthetic acceptance).

### 12.3 CI

`pytest tests/ engine/pipelines/earthquake_coseismic/tests/ -q` (repo root) — the independence/contract tests run alongside the pipeline tests, so a convention violation anywhere fails the build. Live-adapter tests (R1/R2 against USGS) are marked `@pytest.mark.live` and excluded by default; one scheduled job exercises them (rate-limit friendly, cached).

---

## 13. Debuggability & observability (the "segmented" requirement)

1. **One segment = one artifact.** Every run writes `data/runs/<run_id>/`: `params.json`, `result.json` (verdict + detail), `layers/*.npy`, and the bundle id — so any verdict can be replayed offline (`load_bundle` + `run(bundle)`) without the API.
2. **Bundle flags are the log.** The pipeline adds `Flag`-shaped entries to evidence (not the bundle — it's frozen): cycle-jump counts, reference fallback, detrend applied, iono floor basis. debug_hub keeps working unchanged; `handoff.pipeline_feed` will show 3.01 as READY→RUN once `run()` exists.
3. **The observer hook** (`chain.run_steps(observer=...)`) stays the chain-level debug channel; `engine.common.debug.trace.traced_build` + `python -m engine.common.debug --sample …` produce the HTML report — and **M2 delivers the missing `docs/common_vs_eq_comparison.md`** comparing engine P01–P13 outputs against the app's extracted LA sample layers (the reference that doc's absence has been pointing at).
4. **`detail` is the machine-readable truth** — every decision carries its numbers (§6.3), so "why did it say NOT_SUPPORTED" is answerable from the run record alone.
5. Time-travel debugging: `bundle_id` is content-addressed (params + inputs hash) — re-running after a code change with the same bundle id proves a verdict change came from code, not data.

---

## 14. Scalability & the next pipelines

- **Background scans** keep the existing spawn-pool path (`Registry.run_tile(workers=None)`); the EQ pipeline is spawn-safe (no threads, no globals at import beyond the fetch seam that intentionally resets per worker).
- **UI click-to-run** uses inline mode now; the queue decision (ARCHITECTURE §10: "a proper task queue in front of it") is the only structural piece pending — the API's run table is already the queue's state shape, so swapping the inline executor for a worker process later is a drop-in.
- **`land_subsidence` (next in line after tsunami):** the deferred SBAS modules (`sbas/sbas-pipeline/stacks/pelt/bayes/atmosphere/gnss`) port into that pipeline folder with the same layout; `pelt` should wrap `ruptures` via `engine/lib/changepoint.py` instead of the TS DP port; the L-curve/Morozov selection and the 0.1 m interval-displacement prior carry over unchanged.
- **Tsunami wave 2 (outline):** port `engine/lib` additions (`okada` top-edge variant already there) + pipeline modules `source.py` (FFM parser, uniform scaling, Tanioka–Seno slope term, Kajiura filter), `fv_solver.py` (HLL + Audusse, MUSCL+SSP-RK2, Manning semi-implicit, sponge, tangent-plane geometry — acceptance tests: lake-at-rest < 1e-6 m/s, dam-break mass drift < 1e-12, Ritter front speed ∈ [0.6, 1.1]·2√(gh), Kajiura mode attenuation ≤ 2 %, eikonal ≤ 4 %), `traveltime.py` (Godunov fast sweeping), `observations.py` (DART parse + robust detide + k·σ trigger), `evidence.py` (5-level classifier). R11 (`tsunami_db`) and R14 (tide) adapters already exist as stubs to fill. The tsunami pipeline consumes `engine.lib.okada` — never the EQ pipeline's verdict.
- **Adding a 17th pipeline** stays a folder copy of `_template/` — nothing in this plan touches the registry or the other 15 manifests.

---

## 15. Milestones & acceptance criteria

| Milestone | Content | Acceptance criteria |
|---|---|---|
| **M0 — Skeleton merged** (this session's deliverable) | `engine/lib/okada.py`, pipeline package (prep/detection/model/fetch/verdict/types/lib_local), API + UI skeleton, tests T-1..T-9 | `pytest engine/pipelines/earthquake_coseismic/tests -q` green; independence + bundle-contract suites green; pipeline appears READY in debug_hub; `docs/IMPLEMENTATION_PLAN.md` committed |
| **M1 — Kernels hardened** | Vectorised Okada grid path; null-screen perf; cycle-jump tuning on the LA sample bundle | T-1..T-5 green + one real-bundle smoke via `engine.common.debug.eq_sample`; no kernel > 1 s on a 10 km tile |
| **M2 — Pipeline on synthetic truth** | Uncatalogued branch (§8.6), multi-pair evaluation, `docs/common_vs_eq_comparison.md` authored | T-8 table fully green incl. UNCATALOGUED path; EQ scene → SUPPORTED, null → NOT_SUPPORTED, provider-fail → INSUFFICIENT_EVIDENCE; comparison doc reviewed against extracted LA layers |
| **M3 — Live adapters** | R1 + R2 live, disk cache, rate-limit behaviour | `@live` tests green against USGS; failure drill (offline) yields INSUFFICIENT_EVIDENCE with verbatim error; cache hit on second call |
| **M4 — API** | All §10 endpoints + runs store + single-flight | curl-level integration test per endpoint; 409 single-flight; verdicts persisted and re-readable |
| **M5 — UI parity** | All §11 components + Playwright smoke | Launch run from bundle card → verdict card + evidence + layer map render; statuses/badges match the §11 table; zero science imports in UI (CI guard) |
| **M6 — EQ validation wave** | R3 GNSS adapter + `validate_against_gnss` port; calibration hooks read | GNSS comparison appears as optional evidence; calibration stage (4.2) can consume EQ `raw_p` history |
| **M7 — Tsunami wave 2** | §14 outline into a full plan at tsunami start | solver acceptance tests (§14) green; `tsunami_inundation` READY→RUN in debug_hub; quake→tsunami linking wired in verdict 4.3 |

---

## 16. Risk register

| # | Risk | L×I | Mitigation |
|---|---|---|---|
| R1 | Sign/convention flip (los direction, Okada U-terms, heading frames) | M×H | §3 table + T-2/T-3/T-4 sign tests + truth-bowl end-to-end (a flipped sign makes T-8 fail immediately) |
| R2 | `los_m` gating assumptions (NaN where `conn_comp == 0`) subtly differ from the app's components semantics | M×M | Pipeline re-asserts `conn_comp != 0` in its own mask; T-8 uses the fixture's planted garbage patch (`cc[bad]=0`) proving the gate |
| R3 | Okada port drift vs DC3D | L×H | 24-row benchmark at rel < 1e-8 in CI; `tools/dc3d_reference.py` remains the arbiter |
| R4 | Spawn-pool worker lacks injected provider → surprising live calls in tests | M×M | Provider is reset per import (documented §4.1); tests use inline mode (`workers=0`); live marks excluded from default CI |
| R5 | USGS rate limits / latency spikes | M×L | 15-min disk cache, timeouts from `settings.net`, degraded→INSUFFICIENT_EVIDENCE path |
| R6 | Wavelength mismatch (0.2384 vs 0.2360571) leaks into physics | L×M | Pipeline v1 uses no wavelength; the single consumer (cycle-jump screen) reads `config.common.radar.wavelength_m` only |
| R7 | scipy missing in deployment env breaks uncatalogued branch | L×M | scipy pinned in `requirements-retrieval.txt` already; runtime guard → INSUFFICIENT_EVIDENCE + explicit warning (never a silent skip) |
| R8 | UI scope creep / science re-implementation in TS | M×M | §11.1 principle 1 + CI grep guard; API contract fixed (§10) before UI build |
| R9 | Archive depth → mostly INSUFFICIENT_EVIDENCE tiles | H×L | By design (engine statuses); UI renders it as information; synthetic-truth suite keeps correctness provable independent of the archive |
| R10 | `engine/verdict` stages land later and change the record shape | M×M | Pipeline returns the registry mapping form (the stable contract); detail carries everything, so 4.1/4.5 adoption is additive |

---

## 17. This session's `skeleton/` — contents & how to apply

```
skeleton/
├── README.md                          # apply instructions + what runs today
├── engine/
│   ├── lib/okada.py                   # full DC3D port (scalar-exact + vectorised grid), scaling, geo placement
│   ├── pipelines/earthquake_coseismic/
│   │   ├── manifest.yaml              # updated data sheet
│   │   ├── verdict.py prep.py detection.py model.py fetch.py types.py lib_local.py README.md
│   │   └── tests/  (fixtures_eq, test_lib_local, test_okada, test_detection, test_model, test_pipeline)
│   ├── retrieval/clients/adapters/ r1_comcat.py r2_shakemap.py   # real HTTP implementations
│   └── api/ main.py jobs.py store.py routes_pipelines.py routes_bundles.py routes_runs.py routes_events.py
├── requirements-engine.txt
└── app-ui/                            # fresh minimal Next.js (package.json, configs, pages, components, typed API client)
```

**Apply:** copy the tree over the repo root (it only adds/overwrites the files listed); `pip install -r requirements-engine.txt`; run `pytest engine/pipelines/earthquake_coseismic/tests -q` (kernel + okada + detection tests run offline; `test_pipeline.py` additionally needs the engine env incl. pyproj). **Real today:** Okada (benchmark-exact), all detection/statistics kernels, prep/detect/screen/decide flow, registry integration, R1/R2 adapters, API + store, UI scaffold with real API calls. **Not yet (honest gaps, see milestones):** uncatalogued optimizer is implemented with v1 simplifications, R3/S5 deferred, UI layer-warp niceties deferred.

---

## Appendix A — Constants table (port-authoritative values)

| Constant | Value | Home in engine |
|---|---|---|
| Coherence threshold | 0.5 | `EqParams.coherence_threshold` (= engine `stable.coherence_min`) |
| Reference strategy / margin | farField / 0.12 | `EqParams` |
| Detection floor | 0.01 m | `EqParams.detection_floor_m` (manifest "10 mm") |
| Significance k | 3 | `EqParams.significance_k` (manifest "3σ") |
| Min valid pixels / min reference | 100 / 50 | `EqParams` (ported abort/fallback rules) |
| Cluster min pixels | 4 | `EqParams` |
| Credible area | max(1, 4·pixel_area_km2) km² | `verdict.py` (ported) |
| Event distance / buffer | 400 km / ±1 d | `EqParams` (manifest R1 spec) |
| FM subgrid cap | 400 000 points | `EqParams` |
| Null screen | 8 scenes · corrLen 8 px · seed 20261021 (+s·7919) | `detection.py` |
| Screening heuristics | ratio factor 3 · pattern r > 0.25 · atmospheric peak 30 mm | `types.py` |
| Shear modulus / Okada α | 3.0e10 Pa / 0.5 | `engine/lib/okada.py` |
| Hanks–Kanamori / Wells–Coppersmith | M0 = 10^(1.5Mw+9.1) · log10 A(km²) = 0.91Mw − 3.49 | `engine/lib/okada.py` |
| Haversine radius | 6371.0088 km | `lib_local.py` |
| MAD→σ factor | 1.4826 | `lib_local.py` |
| Engine radar config | λ 0.2384 m · phase_sign −1 · looks 100 · min_coherence 0.02 | `config/common.yaml` (bundle already embeds these) |
| Cycle-jump fraction | 0.5·λ | `EqParams` (engine addition) |
| raw_p weights | 0.40 / 0.25 / 0.20 / 0.15 | `model.py` (§8.5, documented in evidence) |

## Appendix B — Traceability

- Engine contracts: `engine/registry.py` (read in full), `engine/common/{scene_bundle,contract,chain,config}.py`, `docs/READINESS.md`, `tests/test_pipeline_independence.py`.
- App behaviors: `src/lib/science/*` + `tests/science/*.test.ts` (42 tests), `docs/architecture.md`, `docs/science.md`.
- Session exploration logs: `/home/z/my-project/worklog.md` (tasks 1–3, per-module inventories incl. full stage orchestration, thresholds, test behaviors, API shapes).



