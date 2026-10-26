# Validation

Recorded 2026-10-21, from the actual test run in this repository
(`bun test`): **103 tests, 103 pass, 0 fail, 533 assertions, ~4.1 s** (feature pass, 2026-10-21 evening: +13 tests for the verdict engine, pair grouping and the client GeoTIFF writer).
(Includes the remediation suites: clean-clone registry behaviour, event
selection, forward-model coordinate alignment, detection statistics.)

> A passing software test is not equivalent to scientific validation. This
> page separates what is verified, how, and what deliberately remains
> unvalidated — and records every claim with its exact reproduction command.

## 1. Claim-by-claim validation table

Categories: **UT** = unit test (offline, deterministic) · **SYN** = synthetic
benchmark validation (self-consistency against analytic ground truth) ·
**INT** = integration test with real local data · **LIVE** = live external
API · **DOC** = documented claim from a prior run, not re-executable here.

| Claim | Category | Data source | Command | Expected | Observed (2026-10-21) | Status | Limitations |
|---|---|---|---|---|---|---|---|
| Okada port matches DC3D reference ≤1e-8 rel. | UT | frozen benchmarks in `tests/science/okada.test.ts` | `bun test tests/science/okada.test.ts` | 0.0 worst rel. diff over 72 comparisons | passes | **REPRODUCED** | validates the port, not the physics |
| Buried strike-slip matches 2-D screw solution | UT | analytic `atan` solution | same | 1–10% agreement | passes | **REPRODUCED** | finite-length effects explain residual |
| Phase→LOS scale and sign (NISAR convention) | UT | `tests/science/core.test.ts` | `bun test tests/science/core.test.ts` | fringe = λ/2; +2π → +λ/2 | passes | **REPRODUCED** | convention verified from JPL D-95677 §5.7 |
| Correction status: iono/tropo NOT applied to stored phase | UT + document | ASF GUNW guide + product run-config | `bun test tests/science/detection.test.ts` (correction-status assertions) | `correctionStatus.ionosphericPhaseCorrectionApplied === false` | passes; measured screen stats: variation 0.37–0.60 rad, uncertainty 4.31 rad | **REPRODUCED** | authoritative source: nisar-docs.asf.alaska.edu/gunw/, retrieved 2026-10-21 |
| Strike-slip fixture recovered vs analytic truth | SYN | seeded fixture (250 m, 64×64 km) | `bun test tests/science/pipeline.test.ts` | MAE < 10 mm | MAE 2.59 mm, RMSE 3.18 mm, bias −2.04 mm, 1372 km², 12 clusters | **REPRODUCED** | synthetic self-consistency — no claim about real-Earth accuracy |
| Thrust fixture recovered | SYN | seeded fixture | same | MAE < 10 mm | MAE 3.93 mm, RMSE 4.86 mm, 494 km² | **REPRODUCED** | same as above |
| Null fixture: zero detection | SYN | seeded fixture | same | 0 km² detected | 0 km², 0 clusters, RMSE 1.83 mm vs truth | **REPRODUCED** | one seed; see null-scene screen below for broader behaviour |
| Null-scene screen (exploratory FP behaviour) | SYN | 8 correlated-noise scenes per run | `bun test tests/science/detection.test.ts` | small, deterministic exceedance fraction | strikeslip 0.282%, thrust 0.326%, null 0.140% mean false-exceedance | **REPRODUCED** | NOT a calibrated false-alarm rate (documented) |
| Clean clone lists datasets without crashing | UT | temp-dir scenarios | `bun test tests/data/registry.test.ts` | structured states, no throw | passes (missing/incomplete/invalid scenarios) | **REPRODUCED** | |
| Event ranking never takes first API result | UT | synthetic event lists | `bun test tests/science/attribution.test.ts` | largest/nearer/with-mechanism wins; exclusions carry reasons | passes | **REPRODUCED** | ranking is a relevance order, not causation |
| Synthetic earthquake fixture is not contradicted by its own attribution | UT | seeded fixtures | `bun test tests/science/attribution.test.ts` | coseismic status reflects known construction, never "no USGS event" | passes (supported-by-construction; null fixture stays not-supported-by-construction) | **REPRODUCED** | fix of final-pass audit finding 2026-10-21 |
| Forward model anchored at epicentre | UT | synthetic grids | same | bit-identical values on shifted grids; peak follows the event | passes (max diff 0.0 on probe points) | **REPRODUCED** | UTM-only; polar-stereographic granules disable the check |
| LA 2008 sample: honest negative below iono floor | INT | committed derived product (`data/derived/nisar-la-2008`, checksums in `docs/data-manifest.md`) | `bun test tests/science/pipeline.test.ts` | floor reported; no detection above floor | valid 14.6%, coh. median 0.57, ref σ 18.5 mm, iono floor 81.0 mm (basis: uncertainty), median threshold 81.0 mm, max apparent LOS 89.4 mm → 0.013 km² stray pixels only | **REPRODUCED** | real product, but no independent geodetic comparison exists |
| Extractor validates GUNW structure | UT | synthetic HDF5 fixtures | `python3 tools/test_extract_gunw.py` | 26 checks pass | 26/26 pass (exit 0) | **REPRODUCED** | synthetic fixtures only; real-product extraction verified once for LA |
| Mojave 2026 quantitative analysis (81→72 mm floor, M4.06 cross-match, 48 mm max LOS) | DOC + LIVE | operational granule analysed 2026-10-20 | `tools/fetch_nisar_granule.py` + `tools/extract_gunw.py` + `POST /api/analyses` | — | — | **RECORDED RESULT SERVED** | the 2.58 GB granule was not retained (data/ is not committed; re-download needs Earthdata credentials), so the pipeline still refuses to re-run (dataset state `missing`, POST 409). The recorded figures are now served honestly as a labelled recorded result (data/derived/nisar-mojave-2026/recorded_run.json + `GET /api/datasets/real-nisar-mojave-2026` → recordedRun), including the real CMR footprint polygon and the real USGS M4.06 event (ci41478192, verified live 2026-10-21). These remain documented observations of the 2026-10-20 run, not re-computed results. |
| Panama evidence (granule catalogue, M7.7 event, browse imagery) | INT + LIVE | committed CMR/USGS caches + live re-verification | `POST /api/analyses {"datasetId":"evidence-panama-2026"}` | granules bracket event; conservative language | 3 granules, selection by explicit criteria (epicentre containment), narrative uses the mandated "has not yet been established" wording | **REPRODUCED** | browse imagery ≠ science rasters; no quantitative claim |
| USGS/CMR live queries work | LIVE | FDSN + CMR APIs | UI live checks / `curl` | events and granules return | verified during browser E2E (2026-10-21) | **REPRODUCED** | network-dependent; app degrades explicitly on failure |

## 2. Test inventory (103 tests)

### tests/science/okada.test.ts — the physics engine
Unchanged from the pre-remediation suite: 24 benchmark geometries × 3 slip
modes against the vendored DC3D reference (worst relative difference 0.0),
the analytic screw-dislocation calibration, slip-sense trace-jump tests,
far-field decay, and the scaling-relation sanity checks.

### tests/science/core.test.ts — conventions and statistics
Unchanged: fringe/sign conventions, LOS vector reconstruction against the
product's stored radarGrid, Rodríguez & Martín σφ values, robust statistics.

### tests/science/pipeline.test.ts — geodesy, gates, end-to-end
UTM round trips, grid-corner inverse projection, Stage A rejection gates,
null/strike-slip fixture recovery, LA real-product honesty (data-dependent,
self-skipping when the local product is absent), determinism.

### tests/science/attribution.test.ts — NEW (remediation)
Event ranking (unsorted inputs, ties, missing magnitudes, window/distance
exclusions), forward-model epicentre anchoring (move-event tracking,
bit-exact grid-shift invariance, near-epicentre peak, non-UTM refusal,
mechanism-missing skip), and attribution language assertions ("consistent,
not causation"; "no detection above threshold, NOT no motion"; single
component ≠ proof of artefact absence). Final pass (2026-10-21) adds the
synthetic-fixture honesty regressions: an earthquake fixture must never
report "no USGS catalog earthquake" against its own construction; the null
fixture's not-supported verdict must be construction-based; synthetic runs
must not cite real-product correction status; a catalog screen outcome is
preserved when an event IS supplied.

### tests/science/detection.test.ts — NEW (remediation)
Masking/detection bookkeeping, NaN handling, cluster statistics
(4-neighbourhood, stragglers ignored), correlated-noise null-scene screens
(determinism, correlation-length exposure, cluster-forming failure mode,
empty-mask refusal), and pipeline integration of the new summary fields.

### tests/data/registry.test.ts — NEW (remediation)
Clean-clone behaviour: listing with no data, partial data, corrupt data
(size-mismatched rasters, unparseable meta, malformed Panama cache);
availability explanations naming the acquisition path; the Panama granule
selected by explicit criteria rather than array order.

### tools/test_extract_gunw.py — NEW (remediation)
26 checks: valid synthetic GUNW-layout fixture extracts; six corruption
modes (missing layer, shape mismatch, positive dy, unsupported spec version,
no co-pol, coordinate mismatch) each rejected with exit 2 and a
defect-naming message.

## 3. What is deliberately NOT validated

- **No comparison against independent geodetic measurements** (GNSS, or
  published InSAR studies of the 2008 LA region) has been ingested. This
  remains the natural next validation step and is documented as a gap.
- **The Mojave 2026 real-data analysis is not currently reproducible** from
  this checkout (credential-gated 2.58 GB source product not retained). Its
  documented figures are honest records of a prior run, nothing more.
- **The Panama prediction is a forward model, not a validated measurement.**
  Whether the bracketing products contain a measurable earthquake signal
  has not been established.
- **Attribution thresholds are exploratory conventions, not calibrated
  statistics.** No precision/recall claims are made anywhere for the
  hypothesis screen; the values and their rationale ship with every result.
- **The null-scene screen is not a formal false-alarm-rate calibration** —
  it models correlated Gaussian noise only, not atmospheric/ionospheric
  covariance.
- **The coherence-based σ underestimates below γ ≈ 0.3** (documented;
  masked pixels keep the formula inside its valid range).
- **UTM series accuracy (~metre level)** is adequate for 80 m products but
  is not geodetic-grade.
- **No automated UI tests.** The interface is verified interactively during
  development (dataset selection, both analysis modes, layer switching,
  methodology drawer, live CMR query, responsive layout, footer behaviour);
  those checks are not repeatable artifacts.

## 4. Provisional-product caveat

The operational NISAR collection analysed for the Mojave dataset is
**NISAR_L2_GUNW_PROVISIONAL_V1** — an interim product whose provider states
it is not fully validated. The existence of a NASA product is not
independent validation of MOTUS's results on it, and MOTUS's own
measurements on provisional products inherit that caveat (displayed in the
provenance panel).

## 5. Reproducibility

- Fixtures are seeded (`mulberry32`); the pipeline is a pure function of
  (dataset, parameters, USGS events). Null-scene screens use fixed seeds.
- Real derived products carry SHA-256 checksums in `docs/data-manifest.md`
  (`python3 tools/generate_data_manifest.py` to regenerate/compare).
- Analysis runs are persisted (`AnalysisRun` table) with parameters,
  summaries and durations; identical parameter sets are served from an
  in-memory cache within a server session.
- The Okada benchmarks are frozen in the test file and regenerable from the
  vendored reference implementation (`tools/dc3d_reference.py`).
- CI (`.github/workflows/ci.yml`) runs lint, strict type check, offline
  tests and the production build on a clean checkout — no credentials, no
  untracked data.

## SBAS time-series pipeline (system plan implementation, 2026-10-22)

Verified by tests/science/plan-core.test.ts (24 tests) and live E2E:

- Design matrix, WLS solve, missing-observation semantics (unobserved intervals pinned
  by the displacement prior — the mean velocity underestimates by exactly the
  unobserved time fraction, reported honestly via per-epoch σ).
- PELT: clean step isolated at the exact epoch; pure linear trends produce zero change
  points; NaN rejection.
- Bayesian engine: broad-linear → SUBSID, steep-stepped with Okada agreement → QUAKE,
  ambiguous features → UNATTRIBUTED via the posterior rule; non-finite demCorr is
  neutral (regression test for the posterior-collapse bug).
- Stack fixture: the 2026-07-27…08-10 instrument gap contains no epoch; one interval
  widens to 24 days; the quake step epoch brackets the catalog event time.
- End-to-end pipeline (synthetic stack): subsidence recovered at the truth scale,
  177 mm coseismic step isolated at the correct epoch, both PRIMARY_CAUSE_QUAKE and
  PRIMARY_CAUSE_SUBSID present, GNSS RMSE 33.2 mm/yr (9 stations, secular-only
  comparison), Brier 0.163 (target ≤ 0.15; mixed-zone pixels documented), truth
  velocity RMSE 34.4 mm/yr, synthetic labelling enforced in the narrative.

Honest limits: the stack is a synthetic fixture parameterized from published Central
Valley rates (real GUNW stacks need Earthdata credentials); GNSS station velocities are
fixtures sampled from the same synthetic Earth truth (live NGL endpoint unreachable);
the Brier score sits just above the plan's 0.15 target on mixed-zone pixels.

## Tsunami capability (2026-10-24)

Unit/benchmark verification: 46 tests (see `docs/tsunami.md` §7 for the full
table with acceptance criteria). Highlights: lake-at-rest over wet/dry
topography to machine precision (≤ 3e-14 m/s); dam-break mass conservation
4.6e-15 relative with zero positivity violations; linear long-wave speed
within 3% of √(gh); order-2 grid convergence; Ritter front physics within the
documented fixed-grid front-capture tolerance; sponge absorption < 5%;
detection false-alarm 0/12 on synthetic controls; all five evidence states
unit-tested.

Real-data hindcasts (executed 2026-10-24, live USGS + ETOPO1 + archived
NOAA DART; exact commands in `docs/tsunami.md` §9):

### Tōhoku 2011 (M 9.1) — 2′ hindcast, run ts-mvmi61bh-u6vsx (2026-10-24)

Configuration: USGS finite-fault model (325 subfaults, ΣM₀ = 4.94×10²² N·m),
Okada + Tanioka–Seno + Kajiura source on live ETOPO1, 2′ grid (470×481 cells
over a 16°×22.4° domain), 4 simulated hours, order 2. Wall clock 946 s,
6,041 steps, mass drift 1.5×10⁻⁵ relative (sponge absorption at open
boundaries included).

| DART station | observed arrival | observed max | model arrival | model max | Δt | amp ratio | verdict |
|---|---|---|---|---|---|---|---|
| 21418 (555 km) | +27 min | 187 cm | +26 min | 157 cm | **0 min** | **1.20** | consistent |
| 21401 (~970 km) | +59 min | 68 cm | +54 min | 76 cm | **+4 min** | **0.89** | consistent |
| 21413 | +74 min* | 78 cm | — | — | — | — | inside boundary sponge — not comparable |
| 21419 | +9 min* | 56 cm | — | — | — | — | outside domain — not comparable |
| 21420 | no record (archive 404) | | | | | | honest missing state |

*Observed arrivals use the model-informed seismic-phase mask (a water wave
cannot beat the eikonal ray time; triggers before 0.85×T_eikonal are flagged
as seismic contamination — the raw triggers at +3/+9 min are the M 9.1's
Rayleigh/E phases on the pressure records). 21419 sits outside the domain so
only the fixed 8-min mask applied; its +9 min trigger is likewise seismic.*

The observed maxima match the published NOAA values for this event (21418 ≈
1.8 m; 21401 ≈ 0.68 m). The simulation — a global 2′ bathymetry model with
the published finite-fault source and no tuning — reproduces both stations'
water-wave arrivals within 4 minutes and amplitudes within 20%. Evidence
classification: **consistent** (with the correlated-buoy and coarse-DEM
caveats rendered inline).
