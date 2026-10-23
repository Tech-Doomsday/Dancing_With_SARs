# NISAR Hazard Engine — Architecture & Vision

Status: planning document, derived from `nisar_hazard_engine.json` (spec export of `flow.py` / `SARs_full_workflow.html`) plus a current-data reality check (Oct 2026). Builds on the prototype work in `Tech-Doomsday/nisar-data_analysis`.

---

## 1. Vision

A standing system that watches the whole planet with NISAR (and Sentinel-1 where NISAR doesn't help) and, for any tile, on any schedule — background scan, event trigger, or a user clicking the map — runs every hazard detector that *could* apply, and reports back **what it found, how confident it is, and exactly what evidence and data it used to get there.**

The core bet the spec already makes, and that this document keeps: **16 hazards, 16 independent pipelines.** No pipeline is allowed to read another pipeline's output. Shared physics and shared statistics live in one library. A tile that triggers three hazards gets three honest, separately-derived answers, linked together *after* the fact by the verdict stage — never fused inside a single model that would hide which evidence came from where.

---

## 2. Design Principles

1. **Independence is structural, not just a convention.** Enforced by where code is allowed to live (§3), not by a comment asking developers to behave.
2. **"Not applicable" ≠ "no event."** Every pipeline must be able to say "I didn't run here" distinctly from "I ran and found nothing."
3. **No silent corrections.** Anything that could be applied to the data (tropo/iono/tide corrections, detrending) is recorded as a flag and a diagnostic layer, never baked in invisibly.
4. **Calibration is honest about not existing yet.** Before there's enough labeled history to fit isotonic/Platt calibration, pipelines report status tiers and a within-tier score — not a probability they haven't earned.
5. **Cold start is a first-class state**, not an edge case — see §5, the archive is genuinely young right now.
6. **Build on existing open science code wherever it's mature**, and spend custom engineering on orchestration, fetch, and integration — not reinventing Okada inversion or SBAS from a blog post. See §6.

---

## 3. Repository Layout

```
nisar-hazard-engine/
├── engine/
│   ├── retrieval/
│   │   ├── sources/                 # 1.1 — static registry of M*, S*, R* source definitions
│   │   ├── scheduler/               # 1.2 — granule poller, event pollers, trigger rules
│   │   │   ├── granule_poller.py
│   │   │   ├── event_pollers/       # one module per R-source poller (comcat.py, firms.py, ibtracs.py, gfw.py, imerg.py)
│   │   │   └── triggers.py          # trigger_rules -> run list
│   │   ├── clients/                 # 1.3 — one fetch(bbox, t0, t1) adapter per source
│   │   │   ├── nisar_client.py      # asf_search + HDF5 range-read clip
│   │   │   ├── sentinel1_client.py
│   │   │   ├── adapters/            # one file per R1..R14
│   │   │   └── cache.py             # TTL disk cache, degraded-mode flag
│   │   └── auth/                    # Earthdata login, CDS API key, FIRMS key, GFW token — centralized, not per-adapter
│   │
│   ├── common/                      # 2 — P01..P13, sequential chain -> SceneBundle
│   │   ├── p01_granule_resolution.py
│   │   ├── p02_clip.py
│   │   ├── p03_decode_gate.py
│   │   ├── p04_grid_harmonization.py
│   │   ├── p05_geometry.py
│   │   ├── p06_phase_to_los.py
│   │   ├── p07_correction_audit.py
│   │   ├── p08_backscatter_prep.py
│   │   ├── p09_ancillary_align.py
│   │   ├── p10_temporal_stack.py
│   │   ├── p11_stable_mask.py
│   │   ├── p12_tile_routing.py
│   │   ├── p13_provenance.py
│   │   └── scene_bundle.py          # the read-only object every pipeline receives
│   │
│   ├── lib/                         # shared physics/stats — the ONLY code pipelines may cross-import
│   │   ├── okada.py                 # wraps an existing Okada85 implementation, see §6
│   │   ├── flood_detect.py          # reused by flood_inundation AND tsunami_inundation
│   │   ├── offset_tracking.py       # wraps autoRIFT-style tracking, used by glacier + landslide creep
│   │   ├── changepoint.py           # CUSUM / PELT wrappers
│   │   └── terrain.py               # slope/aspect/curvature/HAND helpers
│   │
│   ├── pipelines/
│   │   ├── _template/               # manifest.yaml, prep.py, fetch.py, model.py, verdict.py, tests/ skeleton
│   │   ├── earthquake_coseismic/
│   │   ├── land_subsidence/
│   │   ├── volcanic_deformation/
│   │   ├── landslide/
│   │   ├── glacier_ice_flow/
│   │   ├── permafrost_thaw/
│   │   ├── flood_inundation/
│   │   ├── deforestation/
│   │   ├── wildfire_burn_scar/
│   │   ├── river_erosion/
│   │   ├── coastal_erosion/
│   │   ├── urban_damage/
│   │   ├── tropical_cyclone/
│   │   ├── tsunami_inundation/
│   │   ├── oil_spill/
│   │   └── iceberg_calving_drift/
│   │       # each folder: manifest.yaml, prep.py, fetch.py, model.py, verdict.py,
│   │       # optional lib_local.py for pipeline-only helpers, tests/, README.md
│   │
│   ├── verdict/                     # 4 — 4.1 collect -> 4.2 calibrate -> 4.3 link -> 4.4 select -> 4.5 persist
│   ├── validation/                  # 6 — labels, metrics, fixtures, regression tests
│   ├── api/                         # 5 — FastAPI (or similar): /tiles, /events, /scanner/status, /datasets
│   └── registry.py                  # 7 — scans pipelines/*, reads manifests, runs applicable ones in a process pool
│
├── app/                             # existing Next.js UI (map, panel, time slider)
├── data/                            # git-ignored: clips/, ancillary/, reference/, bundles/, verdicts.db
├── config/                          # tiling scheme, thresholds, per-source timeouts
└── docs/
    └── ARCHITECTURE.md              # this file
```

This is exactly the shape you described: every hazard is a semi-independent segment in its own folder, each can hold more than one utility file, and the only shared code sits one level up in `engine/lib/` where it's visible to everyone equally instead of being borrowed from a neighbor.

---

## 4. System Flow

```
1 Retrieval → 2 Common Preprocessing → 3 Pipelines (parallel, independent) → 4 Verdict → 5 Serving/UI
                                                ↑                                  ↑
                                        7 Cross-cutting registry          6 Validation & calibration
                                        (runs applicable pipelines)       (labels feed 4.2 calibrate)
```

- **Retrieval** decides *what* to fetch (scheduler) and *gets* it (clients), writing clips/ancillary/reference into `data/`.
- **Common preprocessing** runs once per (frame, tile, epoch) and produces one read-only `SceneBundle` — the only thing pipelines are allowed to read from the measurement side.
- **Pipelines** run in parallel, each only touching its own folder plus `engine/lib/`. "Not applicable" is a real status, not a crash.
- **Verdict** collects every pipeline's return, calibrates it, links causally-related hazards (quake → landslide → tsunami → urban; storm → flood → river erosion/deforestation), resolves exclusivity (burn scar vs. forest loss on one patch — higher posterior wins), and persists.
- **Serving/UI** exposes it as tiles, verdicts, and evidence layers, with a drawer generated straight from each pipeline's manifest.

---

## 5. Data Reality Check (as of October 2026)

This matters enough to put up front, because several pipelines assume stack depth that the archive doesn't have yet.

| Milestone | Date | What it means for you |
|---|---|---|
| NISAR launch | July 30, 2025 | Mission is real and in orbit; this part is solid. |
| First sample data release | Jan 23, 2026 | Early look only, not representative coverage. |
| Global **pre-calibration (BETA)** release | Feb 2026 | ~100K L1–L3 products, covering acquisitions Oct 17, 2025 – Jan 20, 2026, up to 8 passes/track. Useful for pipeline *development and testing*, but BETA quality — don't trust it for mm-level deformation claims. |
| Global **calibrated/provisional** L-band data public | July 20–21, 2026 | This is the archive your pipelines should treat as authoritative, and it only covers acquisitions from **June 17, 2026 onward**. |
| Pre-launch backlog processing (Sept 23, 2025 – July 2026) | Expected complete by end of 2026 | Will backfill history once released, but isn't there yet. |

**Practical consequence:** as of today, you have roughly 4 months of continuous, calibrated, global coverage. That is thin for:
- `land_subsidence` (wants ≥10 pairs in a connected network, ≤36-day baselines) — achievable per-tile given the 12-day repeat cycle, but expect many tiles to sit in `cold_start` for a while depending on revisit density.
- `permafrost_thaw` (wants ≥12 epochs spanning a full thaw season) — you likely don't have a full season yet in most places.
- Any pipeline's seasonal-harmonic fit, which the spec correctly gates behind "history ≥ 1 year, else linear."

The good news: the spec already designed for this — `cold_start` flags, `min_epochs_met`, status tiers before calibration exists, and `NOT_MODELLED`/`INSUFFICIENT_EVIDENCE` as first-class outcomes. Nothing architecturally needs to change. Just set expectations: most pipelines will correctly report "not enough data yet" on most tiles for the next several months, and that's the system working as designed, not a bug to chase.

Everything else in the sensor/reference inventory (GPM IMERG, ERA5/ERA5-Land, FIRMS, GloFAS, IBTrACS, ITS_LIVE, RGI7, Hansen GFC, ESA WorldCover, JRC GSW, GHSL, pyTMD) is mature, public, and well-documented. Two small notes:
- ERA5 has a few days of delivery latency (ERA5T near-real-time, later corrected) — fine as a secondary/validation input, don't rely on it as your fast trigger source. GPM IMERG ("Early" run, hours-latency) is correctly the fast one in the spec.
- GFW's 4Wings vessel API (R13) is already marked optional in the spec — keep it that way, heavy usage can need a commercial agreement.

**Open question to resolve before coding:** is `M5 GSLC on_demand_only` an ASF access constraint, or your own design choice to avoid pre-fetching heavy SLC for every tile? Worth confirming — GSLC is a standard direct L2 product, so if it's your own choice (likely, for data-volume/cost control), document it as a policy in `urban_damage/manifest.yaml`, not as a platform limitation.

**Sources:** NASA Earthdata NISAR data releases and ASF DAAC notices (Jan/Feb/July 2026), ASF NISAR Data User Guide, NASA ARSET NISAR training materials (July 2026), Earthdata Forum ASF staff posts.

---

## 6. The 16 Hazard Pipelines — Algorithms & Open-Source Building Blocks

The goal here isn't "use this library instead of thinking" — it's "don't hand-write Okada inversion or SBAS network solving from scratch when mature, maintained code already does it." Where I'm not confident a clean off-the-shelf fit exists, I say so rather than inventing one.

### 3.01 — earthquake_coseismic
**Algorithm:** Elastic half-space dislocation (Okada, 1985) forward model + bounded global optimizer fitting fault geometry/slip to LOS displacement; both nodal planes tested, better fit kept; quadtree-subsampled fit for the uncatalogued branch.
**Build on:**
- `okada_wrapper` (PyPI) — Python wrapper around the original compiled Okada DC3D code. Exact, fast, no need to reimplement the elasticity.
- `pyrocko` (`pyrocko.modelling`) — heavier alternative with layered-earth Green's functions if you outgrow the uniform half-space assumption later.
- `scipy.optimize` (`differential_evolution`, `dual_annealing`) for the bounded global optimizer on the quadtree-subsampled uncatalogued-event branch.

### 3.02 — land_subsidence
**Algorithm:** Tikhonov-regularized weighted-least-squares SBAS network inversion → velocity + cumulative displacement; PELT changepoint detection; seasonal harmonic fit gated on ≥1 year history.
**Build on:**
- `MintPy` (insarlab) — the standard, actively maintained Python SBAS/time-series package; built to ingest stacks of unwrapped interferograms, which is exactly GUNW.
- `opera-adt/dolphin` — OPERA's own open-source displacement time-series package (built for their DISP-S1 product); worth evaluating alongside MintPy since it's purpose-built for this exact SAR-to-displacement problem by the same community producing your cross-check product (M7).
- `ruptures` (deepcharles) — implements PELT and other changepoint algorithms directly.

### 3.03 — volcanic_deformation
**Algorithm:** Mogi point source vs. rectangular tensile dislocation (sill/dike), model choice by BIC, atmospheric veto from elevation-correlated phase.
**Build on:**
- `pyrocko` — has Mogi, CDM, and rectangular dislocation sources with a consistent interface; actively used in the volcano-geodesy community.
- `okada_wrapper` again for the rectangular tensile-source branch.
- BIC model comparison: plain `scipy`/`statsmodels`, no special package needed.

### 3.04 — landslide
**Algorithm:** Slope-parallel LOS creep detector (direction matches aspect); abrupt coherence/backscatter-loss failure detector; logistic-regression susceptibility prior.
**Build on:**
- `whitebox-tools` (core engine written in **Rust**, with a Python wrapper package `whitebox`) — computes slope, aspect, curvature, HAND directly; faster than hand-rolled NumPy terrain derivatives and a good proof that Rust already lives comfortably in this exact domain.
- `scikit-learn` `LogisticRegression` for the susceptibility prior — standard, no reason to build custom.
- `nasa-jpl/autoRIFT` for the creep-detection offset tracking — it's explicitly built for "glacier flow, large earthquake displacements, **and landslides**," so it's directly applicable here too, not just to §3.05.

### 3.05 — glacier_ice_flow
**Algorithm:** Dense offset tracking → velocity, outlier rejection, z-score vs. climatology, surge detection.
**Build on:**
- `nasa-jpl/autoRIFT` — this is not just "a good fit," it is literally the algorithm behind the ITS_LIVE product your own spec already uses as the reference climatology (R12). Same team, NISAR Science Team–connected. Use it rather than writing your own feature tracker.
- `itslive-py` for pulling the ITS_LIVE per-pixel median/MAD climatology you're already fetching as R12.

### 3.06 — permafrost_thaw
**Algorithm:** Seasonal-amplitude + secular-trend regression, bedrock referencing, abrupt-thaw (thermokarst) cluster detection.
**Build on:**
- No single dominant off-the-shelf detector exists for this one — be honest about that going in. `MintPy` can still produce the underlying displacement time series; `statsmodels` (STL decomposition) handles the seasonal/trend split; `ruptures` again for the abrupt-thaw changepoint. This pipeline will carry more original logic than most others.

### 3.07 — flood_inundation
**Algorithm:** Adaptive per-tile threshold open-water + flooded-vegetation classification, region growing constrained by HAND, optional gradient-boosted learned slot.
**Build on:**
- `opera-adt/DSWX-SAR` (Apache 2.0, actively maintained) — NASA/JPL's own open-sourced SAR surface-water algorithm, built for exactly this problem on Sentinel-1/NISAR-class backscatter. Since your spec already treats OPERA DSWx-S1 as a cross-check input (M7), seriously consider running the *actual open algorithm* yourselves on GCOV rather than only cross-checking against the external product — same team, same method, one less black box.
- `whitebox-tools`/`pysheds` for HAND.
- `lightgbm` or `xgboost` for the learned slot.

### 3.08 — deforestation
**Algorithm:** Per-pixel CUSUM sequential change detection on HV backscatter, persistence rule, patch filtering, cause attribution (fire/clearing/windthrow).
**Build on:**
- `ruptures` for the CUSUM/online change-detection core (vectorize across pixels).
- `scikit-image` (`skimage.measure.label`) or `rasterio.features` for connected-component patch filtering and the minimum-area rule.
- Global Forest Watch's RADD alert methodology (published, Sentinel-1-based) is a useful design reference for the persistence-rule tuning even if you don't reuse code directly.

### 3.09 — wildfire_burn_scar
**Algorithm:** Post-fire HV decrease + coherence-change layer vs. history, hotspot-density overlap with ordering constraint.
**Build on:** No single dominant SAR-specific burn-scar package. ESA's SNAP toolbox (via the `esa-snappy` Python bridge) has general backscatter-change graphs worth referencing. Mostly `rasterio`/`numpy` plus the shared `engine/lib/changepoint.py`. Geometric FIRMS overlay via `geopandas`.

### 3.10 — river_erosion
**Algorithm:** Per-epoch adaptive water mask → vectorized bank lines/centerline → per-transect bank displacement, discharge-normalized.
**Build on:**
- `RivGraph` (jonschwenk) — purpose-built, actively maintained Python package for extracting channel networks, centerlines, and banks from binary water masks. This is close to an exact match for the vectorization step.
- Feed it the water mask from the same `DSWX-SAR`-derived logic used in §3.07.

### 3.11 — coastal_erosion
**Algorithm:** Tide-normalized waterline extraction per epoch → transects → retreat rate, storm-linked flag.
**Build on:**
- `CoastSat` (kvos) — the widely-used open-source shoreline-mapping and transect-based change-analysis toolkit. Built originally for optical imagery, but its transect/shoreline-change engine is sensor-agnostic and commonly adapted to SAR-derived waterlines — reuse the change-analysis half, feed it your own SAR waterline extraction.
- `pyTMD` is already correctly chosen in the spec for tide correction.

### 3.12 — urban_damage
**Algorithm:** Coherence-based damage proxy = normalized (pre-event coherence − co-event coherence), aggregated per building/block.
**Build on:** This is the cleanest match in the whole spec — it's the **ARIA Damage Proxy Map** method almost exactly, developed by the same JPL/Caltech team for the same coherence-drop logic, already used operationally after major earthquakes and hurricanes. Since your spec treats ARIA DPM as a cross-check (M7), the same opportunity as flood_inundation applies: implement the DPM methodology as your actual model rather than only diffing against the external product. Worth tracking down `aria-tools`-ecosystem code (Caltech/JPL ARIA team) for GUNW/coherence product handling conventions.

### 3.13 — tropical_cyclone
**Algorithm:** Eye/eyewall pattern detector on cross-pol backscatter, absolute wind only from Sentinel-1 cross-pol geophysical model function.
**Build on:**
- IFREMER's Sentinel-1 ocean SAR stack (`xsar`/`xsarsea`) — handles calibration, noise-floor correction, and cross-pol wind geophysical model functions for exactly this use case. I'm less certain of exact current package names/maturity here than for the NASA/JPL tools above — verify before committing.
- Eye/eyewall circular-pattern detection is more custom; `scikit-image`'s Hough-circle/feature detectors are a reasonable starting point, not a ready-made cyclone detector.

### 3.14 — tsunami_inundation
**Algorithm:** Okada vertical seafloor displacement as a trigger-plausibility prior × flood-detection evidence restricted to the coastal strip.
**Build on:** Nothing new — this pipeline is explicitly designed to call `engine/lib/okada.py` (§3.01's building blocks) and `engine/lib/flood_detect.py` (§3.07's building blocks) as library functions, never the other pipelines' verdicts. That's the spec's independence rule working exactly as intended.

### 3.15 — oil_spill
**Algorithm:** Adaptive local-threshold dark-spot detection vs. surrounding sea, oil-vs-lookalike classifier (gradient boosting).
**Build on:**
- `xsar`/`xsarsea` again for ocean-scene calibration/preprocessing.
- No mature, widely-adopted open end-to-end oil-spill detector I'd confidently point you to — this one is mostly original: `scikit-image`/OpenCV for the CFAR/segmentation step, `scikit-learn` or `lightgbm` for the classifier, trained on your own weak labels.

### 3.16 — iceberg_calving_drift
**Algorithm:** CFAR bright-target detection; calving = front-retreat/new-iceberg area balance; drift = nearest-neighbor + size-matched tracking across epochs.
**Build on:**
- `trackpy` (soft-matter) — mature, widely-used Python particle-tracking library; its nearest-neighbor + feature-linking engine maps directly onto "track icebergs between epochs by position and size."
- `scikit-image` for the CFAR bright-target detection step.
- `nasa-jpl/autoRIFT` is also relevant here for the drift-vector estimation between epochs, same tool as glacier flow.

### Summary table

| # | Pipeline | Primary OSS building block(s) |
|---|---|---|
| 3.01 | earthquake_coseismic | `okada_wrapper`, `pyrocko`, `scipy.optimize` |
| 3.02 | land_subsidence | `MintPy`, `opera-adt/dolphin`, `ruptures` |
| 3.03 | volcanic_deformation | `pyrocko`, `okada_wrapper` |
| 3.04 | landslide | `whitebox-tools`, `nasa-jpl/autoRIFT`, `scikit-learn` |
| 3.05 | glacier_ice_flow | `nasa-jpl/autoRIFT`, `itslive-py` |
| 3.06 | permafrost_thaw | `MintPy`, `statsmodels`, `ruptures` (mostly original logic) |
| 3.07 | flood_inundation | `opera-adt/DSWX-SAR`, `whitebox-tools`, `lightgbm` |
| 3.08 | deforestation | `ruptures`, `scikit-image` |
| 3.09 | wildfire_burn_scar | `esa-snappy` (reference only), mostly original |
| 3.10 | river_erosion | `RivGraph` |
| 3.11 | coastal_erosion | `CoastSat`, `pyTMD` |
| 3.12 | urban_damage | ARIA DPM method / `aria-tools` ecosystem |
| 3.13 | tropical_cyclone | `xsar`/`xsarsea` (verify maturity), `scikit-image` |
| 3.14 | tsunami_inundation | reuses 3.01 + 3.07 via `engine/lib/` |
| 3.15 | oil_spill | `xsar`/`xsarsea`, mostly original |
| 3.16 | iceberg_calving_drift | `trackpy`, `scikit-image`, `nasa-jpl/autoRIFT` |

---

## 7. Technology Stack

**Core: Python.** Not a default-by-inertia choice — `asf_search`, `earthaccess`, `rasterio`/GDAL, `xarray`/Zarr, `pyTMD`, and every library in §6 is Python-first. Rewriting the retrieval/auth layer in another language means reimplementing the Earthdata-login OAuth flow yourself, for no real gain.

**Where C++/Rust genuinely help — and where they don't:**

"Streaming on demand" is network/DAAC-latency bound, not CPU bound. Rust will not make fetches faster; Python `asyncio`/`httpx` already saturates that, since the bottleneck is ASF's servers, not the interpreter. Where compiled code *does* pay off is in CPU-heavy kernels running after the bytes land, especially during a global background scan across thousands of tiles:

- CUSUM change detection (3.08) at global scale
- CFAR detection (3.15, 3.16)
- Okada forward-model evaluation at scale (3.01, 3.14) — already solved, see `okada_wrapper`
- SBAS WLS inversion (3.02) — already solved, see `MintPy`/`dolphin`
- Terrain derivatives (slope/aspect/curvature/HAND) — already solved, see `whitebox-tools`, itself written in Rust

**Recommendation:** build pipelines in Python/NumPy/SciPy first, using the §6 libraries wherever they fit. Profile the global-scan path once real data is flowing. Only then drop into Rust via `PyO3`/`maturin` for whatever profiling actually flags — likely candidates are the CUSUM and CFAR kernels, not the physics inversions (those are already solved by compiled libraries). Pre-optimizing before any pipeline runs against real data is the most likely way to stall this project.

---

## 8. Streaming, Fetch & Cache Design

Builds directly on the pattern already proven in `nisar-data_analysis` (probe-by-region, fetch narrowly, cache results) — this is also the officially documented ASF/NASA workflow (byte-range reads into clips via `asf_search`/`earthaccess`, rather than downloading full granules).

```
engine/retrieval/clients/nisar_client.py
  fetch(bbox, t0, t1, product, layers) -> clip
    1. cache_key = hash(bbox, t0, t1, product, processing_version, layers)
    2. check data/clips/ cache — TTL per source type
    3. if miss: asf_search query -> HDF5 range-read clip (earthaccess) -> write Zarr clip
    4. on fetch failure: serve stale cache + set degraded_mode flag, never fail silently
```

Keep the cache key deterministic and explicit from day one — it's what makes the TTL logic and the degraded-mode fallback actually work instead of becoming a source of stale-data bugs later.

---

## 9. Build Roadmap

**Phase 1 — Foundation.** Tiling scheme (currently undefined — pick one before anything else depends on it), `engine/common/` P01–P13, `SceneBundle`, auth/secrets layer, registry + process pool.

**Phase 2 — Highest-confidence pipelines.** Pick pipelines with the clearest existing OSS reference implementation and least dependence on long time-series depth: `deforestation` (CUSUM, single-epoch-comparable), `flood_inundation` (adapt `DSWX-SAR` directly), `urban_damage` (ARIA DPM method, event-triggered rather than background-scan dependent). These prove the end-to-end flow fastest.

**Phase 3 — Stack-dependent pipelines.** `land_subsidence`, `permafrost_thaw` — bring these online as the archive accumulates the epoch depth §5 flagged as currently thin.

**Phase 4 — Physics-inversion pipelines.** `earthquake_coseismic`, `volcanic_deformation`, `tsunami_inundation` — integrate `okada_wrapper`/`pyrocko`, wire up the uncatalogued-event branches.

**Phase 5 — Ocean pipelines.** `tropical_cyclone`, `oil_spill`, `iceberg_calving_drift` — these lean most on Sentinel-1 fallback and on the least-verified OSS stack (`xsarsea`); budget extra evaluation time here.

**Phase 6 — Verdict, calibration, UI.** Event-linking graph, isotonic/Platt calibration once labels accumulate, serving API, map/panel.

---

## 10. Open Risks & Decisions Needed

- **Tiling/grid scheme** is referenced everywhere (routing, triggers, storage paths) but not yet defined — define it before Phase 1 work starts.
- **`M5 GSLC on_demand_only`** — confirm whether this is a platform constraint or your own cost-control policy (see §5).
- **Job orchestration for "user AOI (map click)" mode** — a process pool suits background scans; the click-to-run path needs a proper task queue in front of it so a UI click doesn't block on pool availability.
- **`xsarsea`/ocean-SAR stack maturity** — verify current state before committing the two ocean pipelines to it.
- **`spec_data.py`** (referenced by `flow.py` but not uploaded) — may contain logic not reflected in the JSON export; worth checking if available.
- **Archive depth** — most pipelines will legitimately report `INSUFFICIENT_EVIDENCE`/`cold_start` on most tiles for months yet. Build validation fixtures (§6 of the spec, synthetic scenes) so pipeline correctness can be tested independent of real-archive depth.
