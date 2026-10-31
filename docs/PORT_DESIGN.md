# Porting earthquake and tsunami detection into the Python engine

Oct 27, 2026 · @h

Port the earthquake science into `earthquake_coseismic` as a bundle-fed, tile-scale pipeline, and the tsunami science into shared libs plus one new event-scoped pipeline. Neither is a straight copy: the app analyses whole frames and one event at a time, while the engine feeds every pipeline a 10 km tile bundle, so the detection logic, the LOS sign convention and the pipeline-to-data seam all need explicit design first.

## Decisions

These ten calls shape everything below. Each has a reason grounded in the code. Decisions 4, 6 and 8 were confirmed with you on 27 October; the others are my proposals, and the end of the document lists the status of every one.

| # | Decision | Why |
| --- | --- | --- |
| 1 | The Python engine owns all science. The Next.js app becomes a thin client of `engine/api`. One store (`verdicts.db`), Prisma dropped. | Two science cores (TypeScript and Python) will drift. The engine already has the bundle, registry and process pool. |
| 2 | The earthquake pipeline works per tile on the SceneBundle. No far-field margin referencing of a 10 km tile. The same nuisance removal (offset, optional plane) is applied to the measurement and to the model prediction. | The app assumes a whole frame. On a tile the whole field can sit inside the deformation, and the app compares a referenced measurement with an unreferenced prediction. |
| 3 | Event-level conclusions (many tiles, one earthquake) are built in `verdict/link_events.py`, never by one pipeline reading another. | The engine's independence rule is enforced by a test. |
| 4 | `raw_p` is an uncalibrated rank score, not a probability (confirmed). For 3.01 it is the correlation of the better fault plane; the UI shows the status tier and the evidence, never a percentage. The manifest wording "P(coseismic)" changes. | Engine principle 4 (calibration does not exist yet) and the app's own honesty contract agree. |
| 5 | The pipeline uses only `los_m` and `geometry.los_enu` from the bundle. The app's heading/incidence/look-side reconstruction is deleted. A gate test (G0) pins the sign convention against a real product before any verdict is trusted. | The two codebases disagree on LOS direction (finding F1). |
| 6 | Reference data is retrieved once, centrally (confirmed). Sources that two or more pipelines declare (R1 quake catalog, R2 ShakeMap, R3 GNSS, R11 tsunami records) are fetched by the retrieval stage for the region and window, frozen into the bundle as `bundle.references` with source, retrieval time and hash, and read from there. Static layers such as S5 faults arrive as bundle ancillary layers. Sources only one pipeline needs (DART buoys, sea-floor depth) stay in that pipeline's own `fetch.py`, through injected providers. A failed fetch becomes `INSUFFICIENT_EVIDENCE` with the reason, never "no event". | Your rule: common data is processed once and handed over as one struct; extra data only one pipeline needs it retrieves itself. R1 is declared by five manifests, so fetching it per pipeline would repeat the same request. Today nothing calls any `fetch.py`, and the registry docstring contradicts `own_fetch`. |
| 7 | `PipelineResult` already exists in `engine/registry.py`; it keeps its fields and gains `summary` and the status `PENDING`. Typed `EvidenceItem`, `LayerRef` and `Source` live in `engine/lib/result_schema.py`, which pipelines may already import, so the independence test's allowlist does not change. | The UI must render evidence without parsing sentences. |
| 8 | Tsunami splits in two. Shared code goes in `engine/lib/tsunami/`. Pipeline 3.14 stays the SAR coastal-inundation pipeline its manifest describes. The app's DART plus shallow-water work becomes a new pipeline `3.17 tsunami_ocean_evidence` (confirmed). | They answer different questions at different scales (finding F7). |
| 9 | Heavy jobs (the wave solver) run in a separate lane with their own timeout, a progress file and a content-addressed result cache. The registry gains a `PENDING` status. | The app's Tohoku hindcast took 946 s; the registry kills tasks at 900 s. |
| 10 | Not ported now: SBAS, the Bayesian engine, PELT, GNSS stacks, atmosphere models. They belong to `land_subsidence` and similar pipelines. | They are not earthquake detection, and porting them now would blur the segments you asked for. |

## Starting point

The engine has a finished measurement path and an empty science layer; the app has finished science but a different shape. I read both archives in full for the files named here; I did not run either test suite (no pytest, pyproj, h5py or bun in my sandbox, and I could not install them).

| Area | Engine repo (`Dancing_With_SARs-main`) | App (`app.zip`) |
| --- | --- | --- |
| Language and shape | Python 3.11+, per-tile, `SceneBundle` fed to 16 independent pipelines | TypeScript (Next.js 16, React 19, Prisma/SQLite), per dataset or per event |
| Preprocessing | P01 to P13 and `SceneBundle` built; contract checks in `engine/common/contract.py`; `READINESS.md` (2026-10-25) reports them clean | Its own: coherence and connected-component mask, far-field 12% referencing, sigma, ionosphere floor, in `pipeline.ts` (528 lines) |
| Earthquake science | `earthquake_coseismic` has a manifest and five comment-only files; `engine/lib/okada.py` is one comment line | Okada port (`okada.ts`, 549), detection (400), attribution and event ranking (810), verdict sentences (132), null-scene screen |
| Tsunami science | `tsunami_inundation` manifest describes a SAR coastal-strip pipeline; code is comment-only | About 3,500 lines in `src/lib/tsunami/`: DART detection, ETOPO1, Okada plus Kajiura source, shallow-water solver (771), eikonal travel time, evidence levels, job runner |
| Data access | `NisarClient` and `granule_poller` real; all 14 reference adapters `r1` to `r14` are comment-only; no event pollers | Live USGS, NASA CMR, NOAA DART and ETOPO1 calls inside route handlers and libs |
| Verdict and API | `engine/verdict/*` (5 files) and `engine/api/*` (6 files) are one-line stubs | Per-analysis verdict text in `verdict.ts`, REST routes, Prisma audit tables |
| Registry | `engine/registry.py` (475 lines): scan manifests, route, gate, spawn pool, timeouts, crash isolation | Reads the engine's manifests to show an overview (`src/lib/engine/registry.ts`) |
| Tests | `tests/` has contract, independence, common-chain, truth and retrieval tests | README claims 86 science tests; the tsunami tests are listed in `tests/tsunami/` |
| What is out of scope here | The other 14 pipelines | SBAS (`sbas.ts`, `sbas-pipeline.ts`), Bayesian engine (`bayes.ts`), PELT, GNSS, stacks, atmosphere: about 4,000 lines that belong to land subsidence and similar pipelines |

The app zip is incomplete. Five files import `@/lib/data/cmr`, `datasets`, `pairs`, `references` and `usgs`, and none of those exist in the archive, nor does the `data/` folder (derived rasters, evidence caches). So the app cannot build or run its real-data tests as shipped, and its USGS client, the source of the event list and moment-tensor parsing, is not available to port. The only live copy of that logic in the zip is `enrichEvents` in `src/lib/tsunami/events.ts`.

## Findings that change the design

Each row is something I found in the code that a straight port would carry into the engine as a bug or a contradiction. F1 is the one to settle first.

| # | Finding | Evidence | What the plan does about it |
| --- | --- | --- | --- |
| F1 | **LOS direction is described two opposite ways.** The engine says `los_enu` points ground to sensor and `los_m` is positive toward the sensor. The app says the product stores sensor to ground and uses away-positive. Its `losUnitVector` builds the right-looking vector from heading plus 90 degrees, which is the radar's look direction (sensor to ground), yet labels it ground to sensor. `projectDisplacementToLOS` then returns the negative of that dot product, so the model prediction is toward-positive while the measured field is away-positive. By my reading a real coseismic signal would then correlate negatively with the model, and the synthetic tests cannot notice because the fixtures use the same function to make the truth. | `los-projection.ts` lines 7 to 25 and 57 to 72; `pipeline.ts` line 225; `attribution.ts` lines 356 to 391; `tests/science/core.test.ts` line 66; engine `p05_geometry.py` docstring says flipping both signs at once is invisible to its check | Do not port the app's geometry. Use the bundle's vectors only, put one projection function in `engine/lib/los.py`, and add gate G0: verify the sign on a real product from its orbit state vectors and `lookDirection` metadata. Metamorphic test: flip the bundle's LOS sign and the verdict must change. |
| F2 | **NISAR is left-looking** ([ASF NISAR intro](https://hyp3-docs.asf.alaska.edu/nisar-docs/nisar-intro)). The app hard-codes right-looking in `datasets/[id]/route.ts` line 65, `stacks.ts` line 475 and `fixtures.ts` line 59. | Same files; engine `config/common.yaml` has `look_side: null` | Never infer look side from constants. Read it from product metadata when available and fall back to the bundle's own vectors. |
| F3 | **Scale mismatch.** The app analyses a whole frame (millions of pixels) with a far-field margin and a credible-area rule of 1 km2 or more. The engine gives each pipeline a 10 km tile. A magnitude 7 field is tens of kilometres wide, so a tile often sees one smooth ramp, and correlation with a ramp is weak evidence. The app also compares a referenced measurement against an unreferenced prediction peak. | `pipeline.ts` lines 192 to 221; `attribution.ts` lines 374 to 391; `config/tiling.yaml` | Same nuisance operator on both sides, a predicted-signal gate, and event-level aggregation (see Earthquake pipeline). |
| F4 | **No code calls a pipeline's `fetch.py`.** The registry docstring says a pipeline sees only the bundle, while manifests declare `own_fetch`. The independence test already allows imports from `engine.retrieval.clients.adapters.*` only. All 14 adapters are stubs. | `engine/registry.py` line 28; `docs/READINESS.md` gap 1 and "Registry contract gap"; `tests/test_pipeline_independence.py` | Define the seam explicitly (see Pipeline contract). Correct the docstring. |
| F5 | **Vocabulary clash.** The manifest says `P(coseismic)`; engine principle 4 says no probabilities before calibration; the app says consistency is never causation. The app also returns `not-supported` when no catalogued event exists, while the manifest has an `UNCATALOGUED_CANDIDATE` branch for exactly that case. | `manifest.yaml`; `docs/ARCHITECTURE.md` principle 4; `attribution.ts` line 596 | Decisions 4 and the status mapping table below. |
| F6 | **App bug that its test hides.** `verdict.ts` looks for hypothesis id `earthquake`, but attribution emits `coseismic`, so the earthquake sentence never appears. The UI test builds its own fixture with id `earthquake`, so it passes. | `verdict.ts` line 83; `attribution.ts` line 599; `tests/ui/features.test.ts` line 89 | Port behaviour from the spec in `verdict.ts` comments, not from the code, and test against real pipeline output. |
| F7 | **Different tsunami questions.** The app asks "did this earthquake move the ocean?" for one event, using DART buoys and a simulation. The manifest asks "did the sea flood this coastal strip?" for one tile, using SAR backscatter and coherence. | `runner.ts`; `tsunami_inundation/manifest.yaml` | Decision 8. |
| F8 | **Solver runtime exceeds the registry timeout.** Tohoku 2-minute hindcast: 946 s in TypeScript. `registry.task_timeout_s` is 900 and a timeout becomes `ERROR`. | `docs/validation.md` line 180; `config/common.yaml` | Heavy lane, progress, result cache, benchmark gate before committing to numpy-only. |
| F9 | **Constants differ.** Looks: product value 119 in the app, assumed 100 in the engine. Wavelength: 0.236 m (ALOS-1 sample) versus 0.2384 m (NISAR). Coherence gate 0.5 in the app, only the connected-component gate in the engine. Ionosphere floor: app takes the larger of the screen's spatial variation and its uncertainty; the bundle carries a per-pixel uncertainty layer plus the screen as a diagnostic. | `science.md` sections 1 and 5.1; `config/common.yaml`; `engine/common/debug/compare.py` | Parity tests pin the app's constants first, then each difference is listed and justified in the pipeline's config. |
| F10 | **The app's other hypotheses cross segment boundaries.** "Slow surface motion", "atmospheric residual" and "unwrapping artefact" are other pipelines' territory or confounders. | `attribution.ts` lines 612 to 755 | Keep atmosphere, ionosphere and unwrapping as confounder evidence items. Slow motion is answered by land\_subsidence and landslide, and combined only in the verdict link stage. |
| F11 | **Housekeeping in the engine.** `common/runner.py`, `state.py` and `_raster.py` import a class that no longer exists and fail to import. `pyproject.toml` lists no dependencies. README mentions `docs/common_vs_eq_comparison.md`, which is not in the archive. `READINESS.md` reports one failing retrieval test and one uncollectable test file. | `docs/READINESS.md` hygiene section | Phase 0 cleanup. |

## Target architecture

The flow keeps your rule intact: common processing runs once and is handed over as one frozen struct; each pipeline then does its own extra preparation and its own extra data retrieval, and returns one typed result.

```text
 tile + as_of + triggers (event window, quake id)
          |
  [retrieval]  NisarClient -> clips
               reference sources named by the manifests (R1, R2, R3, R11) -> one snapshot per tile and window
          |
  [common]     P01 .. P13  ->  SceneBundle (frozen, memory-mapped, content-addressed)
               + bundle.references (the snapshot)  + bundle.ancillary (static layers, incl. S5 fault distance)
          |
  [registry]   P12 routing (yes / no / unknown)  +  min_epochs gate  +  lane choice
          |            normal lane (spawn pool, 900 s)      heavy lane (own timeout, progress)
          v
  +-------------------------------+      +----------------------------------+
  | earthquake_coseismic (3.01)   |      | tsunami_ocean_evidence (3.17)    |  ... 14 more, same shape
  |  prep.py   bundle -> field    |      |  prep.py   tile, event, coast    |
  |  (reads references R1,R2,R3   |      |  fetch.py  own sources only:     |
  |   and fault layer S5 from the |      |            DART, bathymetry,     |
  |   bundle; no fetch of its own)|      |            finite-fault product  |
  |  model.py   lib.okada + LOS   |      |  model.py  lib.tsunami solver    |
  |  verdict.py run(bundle, ...)  |      |  verdict.py run(bundle, ...)     |
  +---------------+---------------+      +----------------+-----------------+
                  +------------------+--------------------+
                                     v
  [verdict]  collect -> calibrate (status tiers) -> link_events -> select -> persist (verdicts.db)
                                     |
  [api]      FastAPI: /pipelines  /tiles  /tiles/{id}/verdicts  /events  /runs  /layers
                                     |
  [app]      one map, one verdict board for every pipeline, per-pipeline detail drawers
```

Files that are new or change (everything else in the engine stays as it is):

```text
engine/
  lib/result_schema.py              NEW     EvidenceItem, LayerRef, Source typed dicts + build_result()
  retrieval/references.py           NEW     reads the manifests, fetches each shared code once per tile and window
  common/scene_bundle.py           CHANGED adds `references` (default empty); contract version bumped; contract.py checks it
  common/ancillary.py              CHANGED adds the fault_distance_km layer (S5)
  tools/make_fault_layer.py         NEW     producer for that layer (nothing writes ancillary layers today)
  registry.py                       CHANGED PipelineResult gains summary; REGISTRY_STATUSES gains PENDING;
                                            manifest fields execution / timeout_s / needs_sar; heavy lane
  lib/okada.py                      FILLED  surface displacement, scaling laws, rake to dislocation
  lib/los.py                        NEW     the one ENU to LOS projection, sign fixed by gate G0
  lib/geo.py                        NEW     epicentre projection into the tile CRS (pyproj), distances
  lib/detect.py                     NEW     noise-aware detection rule, clusters, correlated-noise null screen
  lib/tsunami/                      NEW     source.py kajiura.py swe.py eikonal.py gauges.py dart_detect.py
  retrieval/clients/adapters/       FILLED  r1_comcat r2_shakemap r3_gnss r11_tsunami_db (shared); NEW r15_dart r16_bathymetry (local)
  pipelines/earthquake_coseismic/   FILLED  manifest prep model verdict lib_local tests (no fetch of its own in v1)
  pipelines/tsunami_inundation/     later   SAR coastal strip, reuses lib.okada and lib.tsunami.source
  pipelines/tsunami_ocean_evidence/ NEW     3.17
  jobs/                             NEW     heavy-job runner, progress, result cache (SQLite WAL, like retrieval/state.py)
  verdict/  api/                    FILLED  the five verdict stubs and the API stubs (each is a comment today)
  validation/fixtures/              FILLED  synthetic Okada scenes with analytic truth, seeded
app/                                UI only; science and Prisma removed at cutover
```

## Pipeline contract

The registry already defines the seam: it calls `verdict.run(bundle)` in a spawned worker, and `coerce_result` turns whatever comes back (a mapping, a `(P, status, evidence)` tuple, or a `PipelineResult`) into one `PipelineResult` dataclass, or into status `ERROR` if it is malformed. We keep that entry point and that dataclass. What changes is that the items inside its lists get a fixed shape, so the UI never has to guess. Nothing below changes how the other pipelines are called.

**Inputs a pipeline may use**

| Input | Where it comes from | Rule |
| --- | --- | --- |
| `bundle` | Common chain P01 to P13, frozen | Read-only. A pipeline never writes to it and never re-runs a common step. |
| `bundle.request` | Tile, `as_of`, triggers, event window | The only source of "which quake" and "which dates". |
| `bundle.routing` | P12 yes / no / unknown | The registry gates on it; a pipeline still re-checks its own manifest conditions. |
| `bundle.references` (new) | Shared reference snapshots (R1, R2, R3, R11), fetched once by the retrieval stage | Read-only, each with source, retrieval time and hash. A pipeline never fetches a code on the shared list. |
| `bundle.ancillary` | Static layers: DEM, land cover, and the fault-distance layer (S5) | Read-only. |
| Extra data | The pipeline's own `fetch.py` | Only for sources no other pipeline declares. Only through `engine.retrieval.clients.adapters.*`. Never HTTP in `prep.py`, `model.py` or `verdict.py`. |
| Extra processing | The pipeline's own `prep.py` and `lib_local.py` | Pure functions of `bundle` and fetched data. No hidden state. |

Which codes are shared is a list in `config/retrieval.yaml`, and a test fails if any code is declared by two or more manifests but missing from it, so the rule cannot drift.

**The five-file shape, and what each file may do**

| File | Job | May not |
| --- | --- | --- |
| `manifest.yaml` | Declares id, applicability, `status_values`, execution lane, timeout, data needs (`fetch_codes`) | Contain logic |
| `fetch.py` | Fetches this pipeline's own single-use sources through adapters and returns typed records plus provenance (source, retrieved\_at, hash) | Fetch a shared code; do science; swallow errors |
| `prep.py` | Turns bundle, references and fetched records into the model's inputs | Fetch; mutate the bundle |
| `model.py` | The science: deterministic, seeded, unit-tested against analytic cases | Touch the network or the filesystem |
| `verdict.py` | `run(bundle, providers=None)` wires the four above, maps to status tiers, returns the result mapping | Catch broad exceptions and turn them into a quiet "no event" |

**`PipelineResult` (exists in `engine/registry.py`; one field added)**

```text
PipelineResult
  pipeline, tile, epoch, bundle_id   filled by coerce_result from the manifest and bundle
  raw_p            float | None      uncalibrated rank score in [0,1] for ordering inside a status tier; never a percentage
  status           str               manifest status_values + NOT_APPLICABLE / NOT_RUN / ERROR + PENDING (new)
  summary          str               NEW, one line the verdict board shows
  evidence         list[EvidenceItem]  id, claim, quantity, unit, sigma, level, source_ref
  layers           list[LayerRef]      name, kind (raster/vector), crs, path, sha256
  data_used        list[Source]        bundle inputs consumed (stack, epoch, grid) by name
  references_used  list[Source]        every external record: adapter, id/url, retrieved_at, sha256
  runtime_s        float
  warnings         list[str]           degraded inputs, skipped checks, fallbacks that fired
  min_epochs_met   bool | None
  detail           dict                diagnostics and versions (pipeline, libs, config hash); not shown by default
```

The three item shapes (`EvidenceItem`, `LayerRef`, `Source`) are typed dicts in a new `engine/lib/result_schema.py`, with a `build_result()` helper that pipelines call and `coerce_result` uses to validate. They live in `engine/lib` on purpose: pipelines may already import `engine.lib`, so the independence test needs no change, and a pipeline never imports `engine.registry`. Pipelines return a plain mapping; keys the dataclass does not know land in `detail`, which is what `coerce_result` does today.

**Rules that keep it honest**

1. Missing input is a status, never a default. A failed fetch (ShakeMap, catalogue, DART) gives `INSUFFICIENT_EVIDENCE` with the reason, and so does an absent SAR pair (that is what the registry's gate already returns when a bundle has no stacks). Neither is ever reported as "no event" or filled with a substitute number.
2. `ERROR` carries the exception type and a trimmed traceback in `detail`; it never reads as "clear".
3. Every number in `summary` or `evidence` traces to a `references_used` entry or to a bundle field, by name.
4. Determinism: seeds derive from `bundle_id`, so the same bundle gives the same result and a rerun can be diffed.
5. The registry stays the only place that knows about pools, timeouts and lanes. Pipelines declare `execution: normal | heavy` and `timeout_s` in the manifest; they do not manage processes.
6. No invented probability (decision 4). `raw_p` is a documented, uncalibrated rank score or `None`, which matches `calibrate.py`: before labels exist, status tiers and a score within the tier, no probability claim. The UI shows the tier and the evidence.
7. The registry docstring says a pipeline sees only the bundle while manifests declare `own_fetch` (F4). The docstring is corrected to say the pipeline calls its own `fetch.py`, through adapters only.

## Earthquake pipeline (3.01 earthquake\_coseismic)

The manifest, the method notes and the five status values already exist in the engine; the five code files are one-line stubs. The job is to port the app's science into them, file by file, change what does not fit a 10 km tile (decisions 2 and 5, findings F1 and F3), and leave out what the common chain already owns.

**Port map: app TypeScript to engine Python**

| App file | Goes to | What changes in the port |
| --- | --- | --- |
| `okada.ts` (DC3D, `rakeToDislocations`, `geoFaultDisplacementENU`, `momentFromMw`, `ruptureAreaFromMw`, `slipFromMoment`) | `engine/lib/okada.py` | Vectorised NumPy. Same equations; checked against the app's frozen benchmark values and analytic limits. |
| `los-projection.ts` | `engine/lib/los.py` | Not a copy (F1, F2). One function projects ENU displacement onto `bundle.geometry.los_enu`, so a positive prediction means toward the sensor, same as `los_m`. The heading-plus-90-degrees right-look reconstruction is deleted. Gate G0 checks the sign on a real product. |
| `detection.ts` (`runDetection`, `detectedClusterStats`, `runNullSceneScreen`) | `engine/lib/detect.py` | Rule kept: flag a pixel when the absolute value exceeds the larger of k times sigma and the floor. Clusters via `scipy.ndimage.label` (4-neighbourhood). Null screen keeps correlated noise, seeded from the bundle id instead of the TS `mulberry32`. |
| `attribution.ts` (`rankEventsForPair`, `faultFromEvent`, `forwardModelCheck`, `evaluateAttribution`) | `prep.py` (event ranking), `model.py` (forward model and screen) | The three screening numbers (amplitude factor 3, correlation 0.25, atmospheric peak 30 mm) become named manifest constants, still labelled screening values, not calibrated statistics. |
| `gnss.ts` | `model.py` (GNSS check, uses R3) | A validation term only, never used to tune the fit. |
| `verdict.ts` (plain-language sentence) | `verdict.py` `summary` | Ported from the behaviour its comments specify, not from the code: the code looks for hypothesis id `earthquake` while attribution emits `coseismic`, so its earthquake sentence never appears, and the UI test hides it with its own fixture (F6). Honesty rules kept: never "no movement", never "earthquake confirmed", and when the ionosphere floor exceeds the signal that comparison is the headline. |
| `utm.ts` | `engine/lib/geo.py` | Replaced by `pyproj`. |
| `bayes.ts` (quake / subsidence / slide posterior) | Not in 3.01 | Cross-hypothesis reasoning belongs to the verdict layer (decision 10, F10). |
| `atmosphere.ts`, `stacks.ts`, `sbas*.ts`, `pelt.ts`, `phase.ts`, `uncertainty.ts`, `validation.ts` | Common chain or other pipelines | The common chain already produces sigma, coherence and the `corrections` audit; re-implementing them here would break "preprocess once". |

**Fitting a frame-scale method onto a tile (F3, decision 2)**

The app judges a whole frame. A magnitude 7 deformation field is tens of kilometres wide, so one 10 km tile often sees only a smooth ramp, and correlation with a ramp is weak evidence. The app also compares a referenced measurement with an unreferenced predicted peak. Three rules fix this:

1. **Same nuisance operator on both sides.** One operator N removes the weighted best-fit constant (and a plane, if the manifest switches it on) over the same valid pixels. The screen compares N(measured) with N(predicted). The 12 percent far-field margin referencing in the manifest's `own_prep` is replaced, and that manifest line is updated.
2. **Predicted-signal gate.** If N(predicted) is below the detection floor, or the tile holds too few effectively independent pixels, the model cannot be tested here. The result is `INSUFFICIENT_EVIDENCE` with the numbers, not `NOT_SUPPORTED`. The minimum effective sample size is set from null-scene runs in Phase 3, not guessed now.
3. **Event-level conclusions are not drawn by one tile.** Each tile result carries the event id, Mw and its own metrics. `verdict/link_events.py` combines tiles of one earthquake (decision 3). A pipeline never reads another tile's result.

**Stage by stage inside `run(bundle, providers=None)`**

1. **Gate.** Check manifest conditions: land tile, valid-coherence fraction above the floor, `request.event_window` present. Otherwise `NOT_APPLICABLE` (condition not met) or `INSUFFICIENT_EVIDENCE` (data missing) with the reason, before any other work.
2. **Read references (no fetching here).** From `bundle.references`: events within the window plus or minus one day, magnitude 4 or more, within 400 km, with moment tensor and both nodal planes (R1; the retrieval stage queries the loosest parameters any pipeline declares and this pipeline filters down); the ShakeMap expected extent (R2); coseismic GNSS offsets (R3). From `bundle.ancillary`: distance to the nearest mapped fault (S5), used as an evidence item and a prior, never as a gate. A missing or failed snapshot gives `INSUFFICIENT_EVIDENCE` with the reason, never "no event" (decision 6).
3. **prep.py**: build the valid-pixel set, apply N to the measurement, cycle-jump mask, detection floor = max(3 sigma, 10 mm, ionosphere floor). The bundle carries a per-pixel ionosphere uncertainty layer and the screen as a diagnostic; which of the two feeds the floor is pinned by a parity test against the app first and recorded in config (F9). Event ranking follows the app's rule: inside the window, then magnitude, then distance, then mechanism available; excluded events keep a reason.
4. **model.py**: Mw to moment (Hanks-Kanamori), area (Wells-Coppersmith, 2:1), uniform slip; DC3D on a strided subgrid of at most 400k points; both nodal planes evaluated, better one kept; project with `lib.los`; apply N; run the predicted-signal gate and then the consistency screen. The GNSS offsets, when present, are compared with the model as a validation term only. The `UNCATALOGUED` branch fits one rectangular source to a quadtree-subsampled field with a bounded global optimizer, and runs only when a coherent cluster exists and no catalogued event qualifies.
5. **verdict.py**: map to a status, write the one-line summary, attach evidence items, layers (detected mask, residual map, predicted LOS), `data_used` and `references_used`. Atmosphere, ionosphere and unwrapping are carried as confounder evidence items, not as competing hypotheses (F10).

**Status mapping (definitions proposed here; the five names are the manifest's)**

| Status | Condition |
| --- | --- |
| `SUPPORTED` | A qualifying in-window event exists, the predicted-signal gate passes, a cluster above threshold exists, and for at least one nodal plane the amplitude ratio is within a factor of 3 and Pearson r exceeds 0.25. |
| `NOT_SUPPORTED` | The gate passes, candidate events were modelled, and both nodal planes fail the screen. |
| `INSUFFICIENT_EVIDENCE` | Fetch failed; too few valid or independent pixels; ionosphere floor at or above the largest signal; or the predicted signal in this tile is below the floor. |
| `NOT_MODELLED` | An event qualifies but cannot be modelled (no magnitude or no mechanism). |
| `UNCATALOGUED_CANDIDATE` | Coherent cluster, no qualifying catalogue event; a source was fitted. Never reported as `SUPPORTED`. |

**`raw_p` (decision 4)**

The manifest line `P(coseismic)` is reworded (confirmed). `raw_p` is an uncalibrated rank score used to order results inside a status tier, never shown as a percentage. For 3.01 it is the Pearson r of the better nodal plane on the N-matched fields, clipped to 0 to 1, and `None` when no model was fitted. It is a measured quantity, not a claimed probability.

**Constants that differ between the two codebases (F9)**

The first parity run pins the app's values (k = 3, floor 10 mm, coherence gate 0.5, amplitude factor 3, r 0.25). Each difference from the engine (looks 119 versus 100, wavelength 0.236 versus 0.2384 m, how the ionosphere floor is formed, the coherence gate) is then listed and justified in the pipeline config, so none changes silently.

**One clean scene needs a name**

A scene with no event and no cluster fits none of the five manifest names. Confirmed: `NO_SIGNAL` is added to this manifest's `status_values`, so the board can say "nothing happened here" without implying a quake was tested and rejected.

**Gaps on the engine side**

- `r1_comcat.py`, `r2_shakemap.py` and `r3_gnss.py` are one-line stubs, so all three adapters are real work. They are called by the new retrieval step (`retrieval/references.py`), not by the pipeline.
- **S5 faults (confirmed, build now).** `docs/READINESS.md` says S5 has no bundle channel and nothing writes ancillary layers at all. The precedent is that shoreline distance and volcano distance already have channels, so S5 becomes a `fault_distance_km` layer on the tile grid, plus a producer script. The fault database to build it from is still open; GEM Global Active Faults is a candidate, and I have not checked its current license.
- **Bundle contract change.** Adding `references` bumps the contract version (now 1.0) and extends the checks in `engine/common/contract.py`. The field defaults to empty, so the other pipelines and their synthetic bundles are unaffected; the contract test proves it.
- **GNSS (R3)** is now a shared reference, because `land_subsidence` declares it too. The data provider is still to be named.

## Tsunami

The app's tsunami code and the engine's `tsunami_inundation` (3.14) do different jobs, and merging them would break the modular rule. 3.14 is a SAR pipeline: coastal strip (2 km, elevation 30 m or less), wash-zone backscatter and coherence loss, with Okada seafloor displacement only as a plausibility prior. The app's code never touches SAR. It asks whether an ocean wave was physically plausible and whether an ocean sensor saw one. So it becomes its own pipeline.

**Split**

| Piece | Home | Why |
| --- | --- | --- |
| Okada vertical displacement, Tanioka-Seno slope correction | `engine/lib/okada.py` and `engine/lib/tsunami/source.py` | Shared. 3.14's manifest already says the Okada prior is "a library function, not the earthquake pipeline's verdict". |
| Kajiura filter, NSWE solver, eikonal travel time, gauge extraction | `engine/lib/tsunami/{kajiura,swe,eikonal,gauges}.py` | Pure numerics, no I/O, unit-testable alone. |
| DART de-tide and trigger detection | `engine/lib/tsunami/dart_detect.py` | Pure function over a time series. |
| Seismic screen and five-level classifier | `pipelines/tsunami_ocean_evidence/model.py` | Pipeline logic, not shared. |
| SAR coastal inundation | `pipelines/tsunami_inundation/` (3.14) | Stays a separate, later piece of work. It imports nothing from 3.17; both use the libs. |

**New pipeline: 3.17 `tsunami_ocean_evidence`**

- `applies`: a trigger exists in `bundle.request.triggers` that passes the seismic screen's inputs (magnitude, depth, mechanism available). The tile only anchors where the user is looking; the simulation domain comes from the source, not from the 10 km tile.
- `needs_sar: false` and `execution: heavy`. When `gate_min_epochs` is on, the registry's `_gate` returns `INSUFFICIENT_EVIDENCE` ("no usable NISAR data for this tile") for any bundle without stacks, which would wrongly stop this pipeline. A manifest field fixes that without special cases in code.
- `status_values`: `INSUFFICIENT`, `NONE_DETECTED`, `POSSIBLE`, `CONSISTENT`, `CONFIRMED_OFFICIAL`, the app's five levels one for one, plus `NOT_APPLICABLE` and `PENDING` from the registry.

**Data flow**

1. R1 ComCat event detail from `bundle.references` (shared): magnitude, depth, mechanism, and the link to the finite-fault product when it exists. The finite-fault product itself is only used here, so 3.17's own `fetch.py` downloads it. Without one, the app's uniform-fault fallback from scaling laws stays, and the result says which was used.
2. R15 NDBC DART (new adapter, pipeline-local): station list within range of the source, and the water-level series around the origin time. The app's hard-coded `DART_STATIONS` list goes away; stations come from the adapter, with provenance.
3. R16 bathymetry (new adapter, pipeline-local): ETOPO grid for the domain, with the grid's resolution and source version recorded.
4. R11 tsunami database from `bundle.references` (shared with 3.14): official record matching the event, used only for the top level.

**What the app has that must not be copied**

- `TSUNAMI_EVENTS` is a curated list of historical events with blurbs and hindcast notes. In the engine, events come from the trigger. The list survives only as test and validation data (Tohoku 2011, Maule 2010 and the others) with real archived records, labelled as such.
- Job state in a module-global map and SQLite through Prisma. Replaced by `engine/jobs/` (below), so there is one job mechanism for every heavy pipeline.

**Evidence levels**

| Level | Rule carried over from the app |
| --- | --- |
| `INSUFFICIENT` | Seismic screen not met and no sensor data, or inputs missing. |
| `NONE_DETECTED` | Sensors available, no trigger in the de-tided series. |
| `POSSIBLE` | Screen met, or a sensor trigger without a matching simulation. |
| `CONSISTENT` | Observation and simulation both exist, and at least one station agrees on arrival (15 min) and amplitude (factor 3). |
| `CONFIRMED_OFFICIAL` | An R11 record matches by time and place; the record id is cited, never asserted from the simulation. |

The screening numbers (M 6.5, depth 50 km, dip-slip fraction 0.3, 15 minutes, factor 3) are the app's own and are described there as screening values, not physical laws. They move into the manifest under those names. `contributing` evidence is kept as a list and is never presented as independent confirmations.

**Heavy-lane behaviour**

The solver is the only part that takes real time, and it already exceeds the registry limit. The app's Tohoku hindcast (2 arc-minute grid of 470 by 481 cells over 16 by 22.4 degrees, 4 simulated hours, second order) took 946 s in TypeScript over 6,041 steps (`docs/validation.md`), while `registry.task_timeout_s` is 900 s and a timeout becomes `ERROR` (F8). A NumPy port can be faster or slower and is not measured yet, so Phase 4 starts with a benchmark gate on that same configuration, recording time and peak memory. If plain NumPy misses the target, the options are a compiled kernel for the stencil loop (Numba or a small C extension) or coarser default grids; the choice is made on the measurement, not before. The runner reports honest states (`PENDING` with progress while running, `ERROR` with the message), and finished grids are cached by content hash, so a rerun with the same source, bathymetry and parameters returns instantly.

**Correctness checks the port must pass before it is trusted**

- Lake at rest stays at rest (well-balanced scheme).
- Wave speed matches the square root of g times depth on a flat basin.
- Dam-break and Green's-law shoaling cases against analytic results.
- Mass conservation with absorbing boundaries off.
- The TS implementation's outputs on fixed inputs, as a regression reference while both exist.
- Hindcast against archived DART records for the validation events; those archived records live in the repo as fixtures with their NDBC provenance.

## Shared libraries and adapters

Two layers carry everything reusable, and each has one rule. `engine/lib` holds pure numerics. `engine/retrieval/clients/adapters` holds every outside read. A pipeline may import both and nothing else outside `engine.common` (the existing independence test already enforces this).

**`engine/lib` rules**

- Pure functions of arrays and numbers. No network, no filesystem, no globals, no clock, no unseeded randomness.
- Units in parameter names (`slip_m`, `depth_km`, `strike_deg`), NaN for invalid, float64 inside, float32 only at the storage edge.
- Each module ships with tests against analytic or published reference values, plus (during the port) the TypeScript outputs on fixed inputs.
- Dependencies stay small: NumPy, SciPy, `pyproj`. Nothing that cannot install from the standard registries.

**Libraries**

| Module | Status | Contents | Used by |
| --- | --- | --- | --- |
| `okada.py` | stub now | DC3D surface displacement (E, N, U), geographic fault wrapper, rake to dislocation, Hanks-Kanamori, Wells-Coppersmith area, uniform slip | 3.01, 3.14, 3.17, later 3.11 volcanic |
| `los.py` | new | ENU displacement to LOS using `bundle.los_enu`; one sign convention (positive toward sensor) with a test that fails if it flips | 3.01 and any pipeline that compares a model to LOS |
| `geo.py` | new | lat/lon to tile CRS via `pyproj`, great-circle distance, footprint distance | 3.01, 3.17 |
| `detect.py` | new | noise-aware threshold rule, 4-neighbour clusters, cluster stats, correlated-noise null screen | 3.01 first; others later |
| `tsunami/source.py` | new | finite-fault parsing, uniform-fault fallback, subfault deformation, Tanioka-Seno | 3.17, 3.14 |
| `tsunami/kajiura.py` | new | Kajiura filter | 3.17 |
| `tsunami/swe.py` | new | NSWE finite-volume solver (HLL, MUSCL + SSP-RK2, Manning friction, sponge layers) | 3.17 |
| `tsunami/eikonal.py` | new | travel-time field and point lookup | 3.17 |
| `tsunami/gauges.py`, `dart_detect.py` | new | gauge extraction; local quadratic de-tide and trigger detection | 3.17 |
| `result_schema.py` | new | typed result items and `build_result()` | every pipeline |
| `changepoint.py`, `flood_detect.py`, `offset_tracking.py`, `terrain.py` | existing | Not touched by this work | other pipelines |

**Adapter contract (one shape for every reference source)**

Today every adapter file is one comment line saying `fetch(bbox, t0, t1) -> typed table/raster`. We keep that call and make the return value and failure behaviour explicit, mirroring what `FetchResult` already does for NISAR clips (`errors` and `degraded` are never silent):

```text
fetch(bbox, t0, t1, *, provider=None, **query) -> RefResult
  records     typed rows or arrays, units in field names
  source      adapter id (e.g. "R1"), endpoint, product version
  retrieved_at, sha256 of the raw payload
  degraded    True if served from stale cache or a fallback endpoint
  errors      dict of what failed and why; never an empty success after an exception
```

- `provider` is the injection point. Production passes `None` and the adapter uses its HTTP provider (timeouts, retries and TTL come from `config/retrieval.yaml` through `settings.py`, with the existing search cache). Tests inject a provider that replays a recorded, hashed response from `tests/data/` (see Tests). A pipeline's `fetch.py` accepts a `providers` dict and passes entries through, so the registry's plain `run(bundle)` call still works.
- A network failure becomes `errors` and a status in the pipeline, never a guessed value and never a retry loop inside the pipeline.

**Adapters needed**

| Code | File | Status | Notes |
| --- | --- | --- | --- |
| R1 | `r1_comcat.py` | stub | Events with magnitude, depth, time, mechanism and both nodal planes; finite-fault product link when present. |
| R2 | `r2_shakemap.py` | stub | Expected-extent polygon (and intensity grid if useful) for the event. |
| R3 | `r3_gnss.py` | stub | Coseismic offsets with sigma. The data source is not named anywhere in the repo or the app; choose and record it in `config/retrieval.yaml` before building. |
| R11 | `r11_tsunami_db.py` | stub | Official tsunami record lookup by time and place. |
| R14 | `r14_tide.py` | stub | Not needed for these pipelines. |
| R15 | `r15_dart.py` | new | NDBC DART station list and water-level series. |
| R16 | `r16_bathymetry.py` | new | Bathymetry subset for a bounding box, with grid resolution and product version recorded. |
| S5 | none | missing | `docs/READINESS.md` says S5 (faults), S6 and S13 "have no channel at all". The earthquake manifest and README name S5 as a fetch. Either build a static fault-layer loader or remove S5 from v1 and let the pipeline say so. |

Every adapter gets a live smoke check in `retrieval/checks.py` style (reachable, parsed, schema as expected) so a broken endpoint shows up as a failed check, not as a silent empty table.

## Verdict layer, API and UI

The verdict package (`collect`, `calibrate`, `link_events`, `select`, `persist`) and the API files (`main`, `routes_pipelines`, `routes_tiles`, `routes_events`, `routes_datasets`, `routes_scanner`) are each one comment line today. Their intended jobs are already written in those comments, so this section follows them and only builds what "one UI showing every verdict" needs first.

**Verdict layer: what gets built now and what waits**

| Module | Now | Later |
| --- | --- | --- |
| `collect` | Gathers `PipelineResult`s for a tile and epoch, checks each against its manifest, and records which pipelines were `NOT_APPLICABLE`, `NOT_RUN` or `PENDING` so the board shows the whole picture, not only the hits. | Nothing more. |
| `persist` | One `verdicts.db` row per (tile, epoch, pipeline, bundle\_id, versions) plus the layer files, keyed by content hash. Same inputs overwrite nothing; they return the stored row. | GeoJSON and COG export for evidence layers. |
| `calibrate` | Status tiers with a rank score inside the tier and no probability claim (its own comment says exactly this). | Isotonic or Platt once labels exist. |
| `link_events` | Two jobs: (1) combine the tiles of one earthquake into an event-level conclusion by event id (decision 3, F3), and (2) one edge, quake to tsunami, using the shared trigger id from `request.triggers`. Pipelines stay independent; both links live only here. | The rest of the chain (landslide, urban damage; storm to flood). |
| `select` | Best verdict per tile plus everything above the threshold, null pipeline always scored, abstain when nothing clears. With two pipelines this is trivial, but the rule is fixed now so adding the other fourteen changes nothing. | Exclusivity groups. |

**API (FastAPI, versioned under `/v1`)**

| Route | Purpose |
| --- | --- |
| `GET /v1/pipelines` | Manifests plus implementation state, replacing the app's file-reading `src/lib/engine/registry.ts`. |
| `GET /v1/tiles`, `GET /v1/tiles/{id}/verdicts` | Existing stub names; the verdict board's data. |
| `POST /v1/tiles/{id}/runs` | Start a run for a tile and epoch (optionally one pipeline). Returns `run_id` at once. Idempotent on (bundle id, pipeline versions, config hash). |
| `GET /v1/runs/{id}` | Per-pipeline state: queued, running with progress, finished, error. This is where heavy-lane `PENDING` shows. |
| `GET /v1/layers/{sha256}` | Serves a stored raster or vector layer for the map. |
| `GET /v1/events` | Linked events from `link_events`. |
| `GET /v1/datasets`, `GET /v1/scanner/status` | Existing stubs, unchanged scope. |

No business logic in route files: each route validates, calls one function in `verdict`, `jobs` or `registry`, and returns. Response models are pydantic models generated from the same typed dicts as the results, and the app's TypeScript types are generated from the API's OpenAPI document, so a field can not exist on one side only. `pyproject.toml` lists no dependencies today (`dependencies = []`) and there is a `requirements-retrieval.txt`, so API dependencies get their own requirements file.

**Jobs (`engine/jobs`)**

The architecture notes flag that a map click needs a task queue in front of the pool. The answer here is deliberately small: a SQLite table in WAL mode (the same pattern as `retrieval/state.py`), a worker process that claims rows, the registry's existing spawn pool for normal pipelines, and a separate slot for heavy ones. No broker to run. If load later proves it insufficient, the table is the interface a broker can replace.

**UI: what stays, what goes**

| Today in the app | After |
| --- | --- |
| Engine overview view (reads manifests from disk) | One verdict board fed by `/v1/pipelines` and `/v1/tiles/{id}/verdicts`; every pipeline is a row, same shape. |
| `earthquake-analysis.tsx` (1,398 lines) and `tsunami-panel.tsx` (915 lines) as special views | Per-pipeline detail drawers rendered from `evidence`, `layers` and `summary`. Earthquake and tsunami are the first two; the next pipeline needs no new view. |
| Map panel, colormaps, raster renderers, charts, tour, methodology | Kept. They only change their data source. |
| Next API routes `v1/attribute`, `v1/search`, `v1/timeseries`, `v1/velocity`, `tsunami/*`, and their Prisma tables | Removed at cutover. Search and external lookups move behind engine adapters. |

The board shows, for every tile: status, summary, evidence count, what data was used, what was skipped and why, and a clear marker for pipelines that did not run. Any pipeline that is `PENDING` shows its progress, never a spinner without a reason. No UI element computes science: no Okada, no thresholds, no sentences built from numbers in the browser.

## Tests and the no-mock rules

"No mock things" is turned into rules a test can enforce, plus a test layer for each module so a failure points at one file.

**The rules**

1. Product code never returns sample, demo or fallback results. If retrieval fails, the pipeline reports a status and a reason.
2. A test double is allowed only at the network edge (an adapter's `provider`), and what it replays is a recorded real response stored with its URL, retrieval time and sha256. Math, registry, bundle and API are never replaced in tests.
3. Synthetic scenes are allowed because they carry analytic truth, and they are marked: `synthetic` in their provenance, and the result gets a `synthetic_input` warning, so one can never pass for real data. The repo already does this for the NISAR-layout fixture (granule id carries `SYNTHETIC-FIXTURE`); the same marker convention is reused.
4. Real-data tests state what the real product lacks. The existing `EqSampleRetriever` already lists its own `limitations` (no ionosphere or troposphere screens, flat synthetic geometry cubes); tests that use it assert only what those limits allow.
5. The ASF range-server stand-in in `tests/synth.py` serves the retrieval work. It is not used by pipeline, verdict or API tests.

**Test layers, from smallest**

| Layer | What it proves | Reference truth |
| --- | --- | --- |
| `lib/okada` | Port is correct | The app's frozen benchmark table (72 scalar comparisons, 8 significant digits, generated from the vendored `dc3d_reference.py`), plus point-source far-field and symmetry limits. |
| `lib/los` | Sign and geometry | A displacement straight up gives a positive `los_m` where `los_enu` has a positive up component; a test that fails if the sign flips. |
| `lib/detect` | Rule and clusters | Hand-built grids with known cluster sizes; null screen reproducible for one seed. |
| `lib/tsunami/*` | Numerics | Lake at rest, wave speed, dam break, Green's law, mass conservation, Kajiura on a known profile. |
| adapters | Parsing and failure handling | Recorded real responses; malformed, empty and timeout cases produce `errors`, never an empty success. |
| pipeline contract | Every pipeline returns what `collect` needs and only allowed statuses | One parametrised test over all manifests. Today each pipeline's test file is one comment saying exactly this. |
| pipeline science | Each status is reachable and correct | Seeded synthetic bundles from `engine/validation/fixtures`: Okada-forward LOS plus correlated noise for `SUPPORTED`, a displaced wrong event for `NOT_SUPPORTED`, noise-only for the null scene, floor above signal for `INSUFFICIENT_EVIDENCE`, no catalogue event for `UNCATALOGUED_CANDIDATE`. Every bundle goes through `assert_bundle_contract` first. |
| API | Routes behave | FastAPI test client against the real app, a real temporary SQLite, the real registry. |
| generated types | App and engine agree | CI regenerates the TypeScript types from OpenAPI and fails on any diff. |
| independence | Modularity holds | Existing AST test, unchanged, plus a check that no pipeline file imports an HTTP client. |

**During the port only**

Every ported function is run against the TypeScript original on the same fixed inputs, with tolerances written next to the test. When the app is cut over, the TypeScript copy and this comparison are deleted together, so there is never a second implementation left alive.

**Validation, as distinct from tests**

- Hindcasts with real archived data: the tsunami events the app already curates (Tohoku 2011, Maule 2010 and others) against archived DART records, kept in the repo as fixtures with provenance.
- A real NISAR GUNW through the real chain end to end, using the existing sample retriever. What it covers (and whether an earthquake falls in its window) is recorded in its `meta.json`; it is used as a positive case only if it does.
- Null scenes: false-alarm fraction is reported per run. It is not claimed as a calibrated rate until labelled data exist (`engine/validation/labels.py` and `metrics.py` are stubs for that).
- Opt-in live smoke tests for each adapter, following the repo's `*_live.py` pattern, off by default.

**Not done yet by me**

I could not run the engine's test suite or the app's Bun tests in the analysis environment (the geospatial packages would not install). Their current pass state is unverified, and Phase 0 starts by running both and recording the baseline.

## Phased delivery

Each phase ships something that works alone and has a "done when" that a command can check. Order matters in two places: Phase 2's sign gate must pass before any earthquake verdict is trusted, and Phase 4's benchmark must run before the tsunami solver design is fixed. After Phase 2, Phases 3 and 4 are independent and can run in parallel; Phase 5 can start once Phase 1 is done, using the earthquake pipeline as its first real input.

| Phase | Scope | Done when |
| --- | --- | --- |
| 0. Baseline and cleanup | Run the engine suite and the app's Bun suite and record the results (not run by me). Fix the engine housekeeping in F11: modules importing a class that no longer exists, `pyproject.toml` dependencies, the missing doc reference. Check the license of the vendored `dc3d_reference.py` source before anything is copied. Settle the decisions listed at the end, and decide what to do about the five missing app modules (`cmr`, `datasets`, `pairs`, `references`, `usgs`): the R1 adapter is written from the USGS FDSN documentation, not from them. | Both baselines are recorded in the repo; every engine module imports; open decisions have an answer. |
| 1. Contract and registry | `lib/result_schema.py`; `PENDING` status; manifest fields `execution`, `timeout_s`, `needs_sar`; `_gate` honours `needs_sar`; registry docstring corrected (F4); one parametrised contract test over every implemented pipeline. | Registry and independence tests pass; a pipeline with `needs_sar: false` and no stacks is not turned into `INSUFFICIENT_EVIDENCE` by the gate. |
| 2. Libraries and fixtures | `lib/okada`, `lib/los`, `lib/geo`, `lib/detect`; seeded synthetic scene builder in `engine/validation/fixtures` (Okada truth plus correlated noise, marked synthetic). Gate G0: the LOS sign checked on a real product against its orbit state vectors and `lookDirection`. | Okada matches the frozen benchmark table; the flip test (reverse the bundle's LOS sign and the verdict must change) passes; G0 result is written down with the product id. |
| 3. Earthquake pipeline | R1, R2, R3 adapters with recorded-response tests and live smoke checks; decide S5; `prep`, `model`, `verdict` for 3.01; nuisance-operator and predicted-signal gate; null-scene runs set the minimum effective sample size; first parity run against the app's constants (F9). | All five statuses are reached by seeded scenes; null-scene false-alarm fraction is reported; the pipeline returns through `coerce_result` with no warnings about shape; the real-sample run states its limits. |
| 4. Tsunami | Benchmark gate for the solver at the app's defaults; `lib/tsunami/*`; R15, R16 adapters; `engine/jobs` heavy lane with progress and content-hash cache; pipeline 3.17. | Solver numerics tests pass; the Tohoku hindcast agrees with archived DART records within the app's documented tolerances; a run completes inside the chosen timeout or reports `PENDING` with progress. |
| 5. Verdict, API, jobs | `collect`, `persist`, `calibrate` (tiers), `select`, `link_events` (tile-to-event and quake-to-tsunami); FastAPI routes; the SQLite job table; OpenAPI to TypeScript type generation in CI. | API tests pass against the real registry; a click-to-run request returns a run id and ends in a stored verdict; the generated types file has no diff. |
| 6. UI cutover | Verdict board and per-pipeline drawers fed by `/v1`; remove `lib/science`, `lib/tsunami`, the science and tsunami Next routes and the Prisma tables; replace the UI test fixture with real engine output (F6). | The app builds with no import of the removed folders; the board shows both pipelines plus every other pipeline as not-run, from live API data. |
| 7. Validation | Label sources (`R1`, `R3`, `R11`), metrics, hindcast set, labelled null tiles; decide whether `raw_p` can graduate to a calibrated probability. | A written validation report with counts and the false-alarm fraction on null tiles; the calibration decision recorded either way. |

Phase 2 is where the plan can fail early. If G0 shows the engine's own vectors have the wrong sign, that is an engine-side fix found before any pipeline depends on it.

## Risks and what I did not verify

**Not verified by me**

- **Neither test suite was run.** pytest, `pyproj`, `h5py`, `shapely` and Bun were not available and could not be installed in my sandbox. The READINESS notes say one retrieval test fails and one test file cannot be collected; I have not confirmed either. Phase 0 records the real baseline.
- **F1 (the LOS sign contradiction) is my reading of the code, not an executed result.** Gate G0 decides it on a real product. If I am wrong, the plan loses nothing, because the pipeline uses the bundle's vectors either way.
- **The app archive is incomplete** (five missing modules and no `data/` folder), so I could not build or run it, and its USGS client is not available to port.
- **Solver speed in Python is unmeasured.** The only number is 946 s for the TypeScript hindcast.
- **Whether 3.01 routes cleanly today.** READINESS says 8 of 16 pipelines are blocked when static ancillary layers are missing. The earthquake manifest only names M1, geometry and the stable mask, and its `applies` line says land tiles, so it may be unaffected, but I did not trace its routing to confirm.
- **External services.** The USGS, NDBC and ETOPO formats the app parses were read in its code. I did not call them from the engine's environment, and the R3 GNSS source is not named anywhere.
- **`dc3d_reference.py` license.** The file credits an upstream project; its license is not in the file and I did not check it.

**Design risks and what limits them**

| Risk | Why it matters | Limit |
| --- | --- | --- |
| Many tiles will legitimately return `INSUFFICIENT_EVIDENCE` | A 10 km tile inside a large deformation field sees a ramp; the predicted-signal gate will fire often. That is honest, but a board full of it looks broken. | The board explains the reason from `evidence`; event-level combination (Phase 5) carries the conclusion. The rule for how many tiles must agree is not defined yet and is set from data in Phase 5. |
| Screening constants (factor 3, r 0.25, 30 mm) are not calibrated | They can be read as precision. The app says so itself. | Named constants in the manifest, tier labels in the UI, no percentages, calibration only after Phase 7. |
| Manifest and registry changes touch all 16 pipelines | A new manifest field could change how the other 14 are handled. | New fields have defaults that keep today's behaviour; the contract test runs over every implemented pipeline; the independence test is unchanged. |
| Heavy lane competes for memory with the spawn pool | The solver's grids plus memory-mapped bundles on one machine. | Heavy jobs use their own slot count (one by default), and the Phase 4 benchmark records peak memory with the time. |
| Two implementations alive during the port | They can disagree and both look right. | The port comparison test, then deletion of the TypeScript copy at Phase 6, as one change. |
| Real-data tests are thin | The sample retriever has flat synthetic geometry cubes and no iono or tropo screens. | Tests assert only what those limits allow; G0 uses a real product's own metadata, not the cubes. |
| Scope creep from the app's other science | SBAS, Bayesian, PELT are valuable and tempting. | Decision 10; each belongs to a later pipeline with its own manifest. |

## Decisions I need from you

Each has a recommendation, so a plain "go with yours" is enough. The first four block Phase 3.

| # | Question | My recommendation | Needed by |
| --- | --- | --- | --- |
| 1 | Is `raw_p` allowed to be an uncalibrated rank score (decision 4), or should 3.01 return `None` until calibration exists? | Rank score = Pearson r of the better nodal plane on the matched fields, `None` when no model was fitted; the UI never shows it as a percentage. | Phase 3 |
| 2 | Add `NO_SIGNAL` to 3.01's `status_values` for a scene with no event and no cluster? | Yes. The fallback is `NOT_APPLICABLE` with the reason "no trigger in window". | Phase 3 |
| 3 | S5 fault prior: build a static fault-layer loader in v1, or drop S5 from v1? | Drop from v1 and report it as not used; add later with its own ancillary channel. | Phase 3 |
| 4 | Which GNSS source for R3? | Choose one open coseismic-offset source and record it in `config/retrieval.yaml`; R3 is validation only, so v1 can run without it and say so. | Phase 3 |
| 5 | If NumPy cannot meet the solver target, may the engine take a compiled-kernel dependency (Numba or a small C extension)? | Allow it, decided only after the Phase 4 benchmark. | Phase 4 |
| 6 | Is `3.17 tsunami_ocean_evidence` the right code and name, and is 3.17 free? | Yes; I saw no 3.17 in the pipeline folders, but your numbering scheme is yours. | Phase 4 |
| 7 | When does the TypeScript science get deleted: at Phase 6 cutover, or kept until Phase 7 validation? | Phase 6, together with the port comparison test, so two cores never coexist after the engine verdicts are shown. | Phase 6 |
| 8 | Can you supply the five missing app modules and the `data/` folder? | Optional. Without them the R1 adapter is written from the USGS documentation and the app's real-data tests are not used. | Phase 0 |
| 9 | May the engine add FastAPI, pydantic, SciPy and `pyproj` as dependencies (a separate requirements file for the API)? | Yes; none of them is exotic and `pyproj` is already assumed by the common chain's tests. | Phase 1 |
