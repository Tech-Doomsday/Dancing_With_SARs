# MOTUS — Earth-change intelligence from spaceborne radar

**NASA Space Apps Challenge 2026 · Challenge: "Dancing with the SARs"**

> **Integration note.** This application is the `app/` web layer of the
> [nisar-hazard-engine](../README.md) repository (the "Dancing with SARs" main
> project). It was developed as the standalone
> [`dancing-with-sars-eq`](https://github.com/Tech-Doomsday/dancing-with-sars-eq)
> project and integrated here unchanged in its science, serving as the
> interactive counterpart of the engine's `earthquake_coseismic` (3.01) and
> `tsunami_inundation` (3.14) pipelines. The app shell (header, navigation,
> footer) belongs to the main project; the **Earthquake analysis** section is
> the MOTUS module. Attribution and scientific documentation below are
> preserved verbatim from the original project.

MOTUS measures how the Earth's surface apparently moved between two radar
satellite acquisitions, quantifies the uncertainty of that measurement,
screens candidate physical explanations against independent evidence, and
shows its work at every step. It is built around real NISAR mission data
products and live NASA/USGS services.

What makes it scientifically defensible rather than merely functional:

- **Verified conventions, not assumed ones.** The phase→displacement sign
  convention is taken from the official NISAR SDS Algorithm Theoretical Basis
  Document (JPL D-95677 §5.7/§5.11.1) and stated in the UI; the LOS
  unit-vector reconstruction is checked against the actual product's stored
  radarGrid vectors.
- **Correction status stated from the authoritative source.** The official
  ASF NISAR GUNW guide (retrieved 2026-10-21) documents that the ionospheric
  phase screen is supplied but **not applied** to the stored phase, and that
  tropospheric corrections are supplied as lookup tables and **not removed**
  from the interferometric data. MOTUS therefore labels every measurement
  *apparent relative LOS range change*, quantifies the unresolved
  ionospheric systematic from the product's own screen statistics, and never
  describes a single-pair result as verified tectonic displacement.
- **A benchmarked physics engine.** The Okada (1985) elastic dislocation
  port matches the official DC3D reference implementation to better than
  10⁻⁸ relative error over 24 benchmark geometries × 3 slip modes, and its
  fault-geometry conventions were calibrated against analytic solutions
  (a placement bug caught this way is now a permanent regression test). The
  forward model is anchored at the earthquake epicentre projected into the
  raster's own coordinate system — with a bit-exact grid-shift invariance
  test.
- **Honest uncertainty.** Coherence-based phase noise (Rodríguez & Martín
  1992), robust reference-region scatter, and the ionospheric systematic
  floor all enter the detection rule. The 2008 sample product correctly
  yields a *negative* result because its ~81 mm ionospheric floor exceeds
  the observed residual signal — the system says so plainly. Detection
  bookkeeping separates masking from classification, reports cluster
  statistics, and carries an exploratory correlated-noise null-scene screen
  (never a claimed false-alarm rate).
- **Attribution that knows its limits.** Candidate explanations carry
  evidence-linked statuses (supported / consistent / plausible /
  insufficient-evidence / not-supported). In-window USGS events are ranked by
  explicit criteria (magnitude, proximity, mechanism availability — never
  "first API result"), and an Okada forward model built from the published
  moment tensor is compared with the measurement as a **consistency screen,
  never as a probability of causation**. Exploratory threshold values ship
  with every result.
- **Real data, honestly labelled.** A real NISAR-format L-band interferogram
  (ALOS PALSAR over Los Angeles, processed by the NISAR SDS) is quantitatively
  analysed; the 2026 Panama M7.7 earthquake is presented with its real NISAR
  product availability, official browse imagery and a clearly labelled
  forward model. Whether the bracketing products contain a measurable
  earthquake-related deformation signal **has not been established**, and the
  application says exactly that. Synthetic validation fixtures are badged
  everywhere they appear.
- **A fresh clone just works.** The dataset registry never crashes on absent
  data: every dataset resolves to a structured availability state
  (`available` / `missing` / `incomplete` / `invalid`) with the missing-file
  list and documented acquisition path. The default offline test suite needs
  no credentials and no untracked data files.
- **Live services.** NASA CMR granule discovery and USGS ComCat seismicity
  are queried live (with caching, retries and honest degradation messages).
- **Traceability.** A 15-entry reference registry links every major claim to
  its source, with verification dates; every dataset carries a full
  provenance record and a checksummed data manifest.

## What you can do here (feature pass, 2026-10-21)

- **One-screen verdicts** — every analysis opens with a plain-language headline such as
  *"No motion detectable. Ionosphere noise (72 mm) exceeds the signal (48 mm)."*
- **Real charts** — a histogram of the measured displacement with an interactive
  *threshold explorer* (drag it to see how the flagged area changes), and a profile line
  through the anomaly with ±1σ uncertainty and the ionosphere noise floor shaded.
- **Seismic context** — the acquisition window drawn as a timeline with the real USGS
  earthquakes on it; dot size = magnitude, primary event highlighted.
- **"Why can't I see anything?"** — ionospheric disturbance, coherence loss and the noise
  floor explained in plain language, using this scene's own numbers.
- **Guided tour** — a "Start here" walk through the three flagship stories (Mojave 2026
  honest negative, Panama M7.7 evidence, synthetic earthquake lab) in about two minutes;
  includes a copyable script for the demo video.
- **Exports** — download the displayed layer as a standards-compliant GeoTIFF (float32,
  EPSG-tagged, NaN no-data; verified against an independent parser), as CSV with UTM +
  lat/lon coordinates, or as PNG.
- **Find a good radar pair** — search the live NASA CMR catalog for recent NISAR pairs
  over any place on Earth, ranked by catalog metadata with live USGS earthquake context
  (the ranking never claims measured coherence).
- **Recorded result** — the flagship Mojave analysis of the real operational NISAR granule
  (source since removed from this server) is served as a clearly-labelled recorded result
  with its real CMR footprint and the real M4.06 event on the map.
- **SBAS time-series engine (the research-plan core)** — a fourth dataset drives the
  complete pipeline from the underlying research paper: 21 acquisitions / 24
  interferograms over a Central Valley analog (12-day repeat, the documented 2026 NISAR
  instrument gap honoured), with stratified-troposphere + ionospheric-ramp corrections,
  a coherence-weighted Tikhonov-regularized SBAS inversion (λ from a DOF-aware Morozov
  L-curve), PELT change-point isolation with step-augmented refits (coseismic steps are
  separated from the secular velocity instead of smeared into it), the six-stage
  Bayesian causal engine with entropy abstention, and validation via GNSS LOS-projection
  RMSE, Brier calibration and analytic truth. An epoch time-slider on the map scrubs
  through the cumulative displacement; the velocity layer shows sustained motion only.
- **v1 API (plan §12 spec)** — `GET /api/v1/search` (live CMR granule search),
  `POST /api/v1/velocity`, `GET /api/v1/timeseries?lat=&lon=`, and
  `POST /api/v1/attribute` (posteriors, assigned cause, Shannon entropy) are
  implemented and live.

## Quick start

Prerequisites: [Bun](https://bun.sh) ≥ 1.1. Network access is optional —
live NASA/USGS queries and map tiles degrade gracefully without it.

```bash
bun install          # dependencies
cp .env.example .env # placeholders only; the default DATABASE_URL just works
bun run db:push      # create the SQLite schema (Prisma, non-destructive)
bun run dev          # start on http://localhost:3000
```

Run from this `app/` directory (that is also how the engine root is
auto-discovered for the Engine overview; see `ENGINE_ROOT` in
`.env.example` when running the app from elsewhere).

Checks (all offline; no credentials or untracked data required):

```bash
bun test             # 86 tests / 485 assertions: science core, pipeline,
                     # geodesy, Okada benchmarks, event ranking, detection
                     # statistics, clean-clone registry behaviour
bun run lint         # ESLint (Next.js rules)
bun run typecheck    # strict tsc --noEmit (build errors are NOT ignored)
python3 tools/test_extract_gunw.py   # extractor validation on synthetic HDF5
```

Data-dependent tests self-skip with a visible message when the optional
local scientific data has not been obtained.

### Obtaining the optional real datasets

The repository commits the small evidence caches and derived LA layers;
larger products are documented and checksummed in
[docs/data-manifest.md](docs/data-manifest.md):

```bash
bash tools/prepare_data.sh            # open-access items + guided extraction
# Credential-gated (free NASA Earthdata Login):
#   set EARTHDATA_BEARER_TOKEN in .env
#   python3 tools/fetch_nisar_granule.py     # ~2.58 GB operational granule
#   python3 tools/extract_gunw.py <granule.h5> data/derived/nisar-mojave-2026 \
#       --slug nisar-mojave-2026
```

Datasets that have not been obtained are listed with state `missing` and
their acquisition instructions — never fabricated.

## The demonstration flow

1. **Mojave 2026 — real operational NISAR data (credential-gated).** A
   genuine 2.58 GB NISAR L2 GUNW granule from the operational mission
   (12-day pair, May–June 2026, over the southern Sierra / Mojave Desert).
   The analysed pair was acquired through a severely disturbed ionosphere:
   coherence collapsed scene-wide and the product's own ionospheric screens
   reach tens of radians. The honest outcome — **no deformation claim
   possible** — was demonstrated with the numbers (2% of the frame validly
   unwrapped, 0 km² above the ~72 mm systematic floor, and a real M4.06
   earthquake inside the frame reported as *insufficient evidence* rather
   than a cause). *The source granule is not retained in a fresh clone;
   the figures are documented in docs/validation.md as a prior run.*
2. **Panama M7.7 — real evidence (works out of the box).** A magnitude-7.7
   earthquake struck Panama on 2026-10-09, between the acquisitions of real
   NISAR interferometric pairs. Explore the official SDS browse imagery, the
   granule catalogue with "spans event" badges, a live NASA CMR availability
   check, the USGS moment tensor, and an Okada forward model of the expected
   deformation — labelled as a prediction, not a measurement.
3. **Los Angeles 2008 — real measurement (works out of the box).** A
   genuine NISAR-format L-band unwrapped interferogram (46-day pair)
   analysed end-to-end: displacement map with uncertainty, coherence and σ
   layers, detection statistics and attribution. The honest outcome is
   "no change above this product's ionospheric systematic floor" — with the
   numbers to back it up.
4. **Synthetic validation lab.** Three deterministic fixtures (dextral
   strike-slip, thrust, null) with analytic Okada ground truth. The pipeline
   recovers the deformation to ~2.6 mm MAE and produces zero false alarms on
   the null fixture — the numbers are shown next to each result.

## Repository map

```
src/lib/science/     Scientific core (pure TypeScript)
  phase.ts             NISAR phase↔displacement convention + correction status
  okada.ts             Okada 1985 DC3D port + moment/area scaling
  uncertainty.ts       Coherence-based σ (Rodríguez & Martín 1992)
  detection.ts         Detection rule, cluster stats, null-scene screen
  attribution.ts       Event ranking, epicentre-anchored forward model, hypothesis screen
  pipeline.ts          Stages A–E orchestrator
  fixtures.ts          Deterministic synthetic validation fixtures
  utm.ts, statistics.ts, los-projection.ts, validation.ts, types.ts, constants.ts
src/lib/data/        Data layer: safe dataset registry, CMR + USGS clients, references
src/lib/tsunami/     Tsunami science: DART records, ETOPO1, NSWE solver, evidence
src/lib/engine/      Engine introspection: reads the real pipeline manifests
                     from ../engine/pipelines/ (server-side only)
src/app/api/         REST API (analyses, datasets, external CMR/USGS/browse,
                     references, tsunami runs, engine/pipelines + engine/status)
src/app/page.tsx     Application shell: shared header/nav/footer + the two sections
src/components/app/  Earthquake analysis module (earthquake-analysis.tsx), engine
                     overview (engine-overview.tsx), Mercator-exact raster
                     rendering, map, results, methodology, tour, pair finder,
                     tsunami panel
tools/               extract_gunw.py (validated HDF5→derived layers),
                     make_synthetic_gunw_fixture.py + test_extract_gunw.py,
                     fetch_nisar_granule.py, prepare_data.sh,
                     generate_data_manifest.py, dc3d_reference.py
data/                Committed science artifacts (derived rasters, evidence
                     caches, browse imagery, sample docs) + git-ignored runtime
                     caches — manifest + prep script
tests/science/       86 automated validation tests (see docs/validation.md)
tests/data/          Clean-clone registry behaviour tests
tests/tsunami/       Tsunami solver / source / pipeline tests
tests/ui/            Verdict engine, pair ranking and export-byte tests
docs/                Science, data sources, methodology, validation, architecture,
                     limitations, references, data manifest (from the original project)
prisma/schema.prisma AnalysisRun + TsunamiRun + Reference models (SQLite)
```

## Documentation

- [docs/science.md](docs/science.md) — foundations, equations, conventions,
  correction status, limitations
- [docs/data-sources.md](docs/data-sources.md) — every dataset and service,
  with provenance
- [docs/data-manifest.md](docs/data-manifest.md) — checksummed file manifest
  and acquisition paths
- [docs/methodology.md](docs/methodology.md) — the end-to-end processing
  pipeline
- [docs/validation.md](docs/validation.md) — claim-by-claim validation
  table, test inventory, what is and is not validated
- [docs/architecture.md](docs/architecture.md) — modules and data flow
- [docs/limitations.md](docs/limitations.md) — the honest list
- [docs/references.md](docs/references.md) — annotated bibliography

## Scope statement

MOTUS is a scientific demonstration for the NASA Space Apps Challenge 2026,
not an operational service. It processes one interferogram at a time, in one
viewing geometry, and its attribution layer is a transparent rule-based
screen. Its measurements are apparent relative LOS range change with
unquantified atmospheric/ionospheric residuals (§ science.md). Synthetic
fixtures are clearly labelled and are not Earth observations. See
docs/limitations.md for the complete list.


## Tsunami capability (2026-10-24)

The same earthquakes MOTUS watches from space can move the ocean. A new
"Did the ocean move? Tsunami check." section (below the radar workspace on `/`)
closes the loop with real observations and real physics:

- **Observations:** live/cached NOAA DART bottom-pressure records (historical
  archive + ~45-day realtime window), de-tided with a robust local-quadratic
  fit, MAD-scaled noise, 5σ triggers with a documented seismic-phase mask.
- **Source:** USGS finite-fault inversions (FFM.geojson) or the MT plane +
  Wells–Coppersmith scaling (labelled approximation) → Okada dislocations +
  Tanioka–Seno slope correction + Kajiura water-column filter.
- **Propagation:** 2-D nonlinear shallow-water equations on live ETOPO1
  bathymetry — well-balanced hydrostatic-reconstruction finite volumes
  (Audusse 2004), HLL fluxes with George (2008) dry-front speeds, MUSCL +
  SSP-RK2, positivity-preserving wet/dry, Manning friction, sponge
  boundaries. Runs on this server as a background job with honest progress.
- **Independent estimate:** eikonal travel-time field (fast sweeping).
- **Evidence:** five transparent levels (insufficient → none-detected →
  possible → consistent → confirmed-official); observed vs simulated
  arrivals/amplitudes with documented tolerances; correlated-buoy and
  coarse-DEM caveats rendered inline; "confirmed" is only ever a citation.

Verified: 46 tsunami tests (lake-at-rest to machine precision, mass
conservation 4.6e-15, wave speed within 3% of √(gh), order-2 convergence,
0/12 false alarms on controls) plus real-data hindcasts against archived DART
records — see `docs/tsunami.md` (methods + limitations),
`docs/tsunami-evidence-matrix.md` (feature → reference → data → status) and
`docs/validation.md` (executed results).

APIs: `GET /api/tsunami/events`, `GET /api/tsunami/events/[id]`,
`GET /api/tsunami/observations?event=…`, `POST /api/tsunami/simulate`,
`GET /api/tsunami/runs/[id]`.
