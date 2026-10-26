# Tsunami System — Evidence Matrix (Phase B audit)

Audit date: 2026-10-24. Every scientific choice below maps to a primary reference, a
data requirement, a validation method, and an implementation status. Statuses:
**DONE** = implemented + tested; **PARTIAL** = implemented, limited validation;
**BLOCKED** = documented as unavailable (data / hardware / dependency), never faked.

## 0. Audit of the research-plan PDF itself

The plan is used as the specification skeleton, but each important claim was checked
against primary sources. Documented corrections / deviations:

| # | Plan claim | Audit result | Action taken |
|---|---|---|---|
| A1 | Okada vertical-displacement formula reproduced on p.6/18 (with `I5 = α/cosδ (ln(R+d) − sinδ ln(R+η))`) | Text-extraction of the PDF garbles the formula; rather than trust a second-hand transcription we use the project's existing TypeScript port of the **official DC3D code** (Okada 1992), cross-validated against `dc3d.py` in `tests/science/okada.test.ts` at multiple points and slip modes | Reuse `src/lib/science/okada.ts`; add only a top-edge-depth placement variant |
| A2 | Slip-sense conventions | Plan states rake +90° = reverse. DC3D U2 positive = *normal*. Mapping `U1 = slip·cos(rake)`, `U2 = −slip·sin(rake)` verified empirically against the reference implementation | Already implemented + tested |
| A3 | Formula 6 (appendix) states the 1-D CFL `Δt ≤ C·Δx/(|u|+√(gh))` | The 2-D constraint is the **sum** of the x and y contributions (the plan itself states this correctly on p.10). The appendix version is a simplification that would be unstable in 2-D | Solver uses the 2-D sum form with C ≤ 0.45 |
| A4 | Arrival threshold `|η| ≥ 0.05 m` (NTHMP convention) | Verified as the standard NTHMP arrival definition. **But** a fixed threshold is not a valid *detection* criterion on real sensor data (noise varies per station/epoch) | Two separate concepts implemented: model arrival-time field uses the documented 0.05 m (configurable); sensor detection uses a noise-adaptive k·σ rule |
| A5 | Level-2 screening `Mw ≥ 6.5, depth ≤ 50 km` | These are heuristic screening values, **not** universal physical truth (deep M7.5 events can be tsunamigenic; shallow M6.3 events have generated local tsunamis) | Implemented as explicitly-labelled configurable screening heuristics, shown as such in the UI |
| A6 | Green's law `(h0/h)^{1/4}` | Valid only for slowly-varying depth, linear non-reflecting waves | Used only as a labelled approximation where stated; primary results come from the solver |
| A7 | "HLLC" Riemann solver recommended | HLLC restores the contact/shear wave. For tsunami propagation the contact wave is immaterial; HLL with Einfeldt speeds is the robust standard for wet/dry (used by production codes e.g. GeoClaw variants) | HLL implemented; choice documented |
| A8 | "Mass conservation error below 10⁻¹²" acceptance | Reasonable for a conservative FV scheme in double precision; our solver accumulates in Float64 | Tested at ≤ 10⁻¹² relative |
| A9 | DART de-tiding via UTide/FES2014 harmonic analysis | FES2014 requires a Copernicus account (not accessible); UTide is MATLAB-origin | Local quadratic-regression de-tiding over a ±3 h window (documented approximation; tide period 12.4 h ≫ window, so the local fit absorbs it); optionally followed by a zero-phase Butterworth band-pass 2–120 min for presentation |
| A10 | EnKF assimilation, F-FNO surrogates, GPU FTRT, Boussinesq dispersion | Out of scope for CPU-only sandbox; plan itself ranks these Phase 2/3 | Documented as **BLOCKED**, not imitated |
| A11 | NTHMP benchmarks "BP1–BP6 all passing" as minimum credible claim | Full BP suite requires laboratory/field reference datasets (conical-island, Monai Valley DEMs, Tohoku surveys) that are not machine-accessible here | Analytical + numerical benchmarks implemented and executed (lake-at-rest, solitary wave, conservation, positivity, convergence, sponge reflection); **plus** a real-data hindcast against archived NOAA DART records — the strongest validation available in this environment. No NTHMP "pass" is claimed |

## 1. Component evidence matrix

| Component | Method / equation | Primary reference | Real data required | Validation | Status |
|---|---|---|---|---|---|
| Seismic source (uniform) | Rectangular dislocation in elastic half-space; M0 = 10^(1.5Mw+9.1); Wells–Coppersmith area scaling (σ ≈ 0.25 ord) | Okada 1985/1992; Hanks & Kanamori 1979; Wells & Coppersmith 1994 | USGS ComCat moment tensor (live FDSN) | Cross-validated against dc3d.py (existing suite); moment/area sanity tests | DONE |
| Seismic source (finite fault) | Subfault summation of Okada dislocations from USGS FFM.geojson (236 subfaults for Maule 2010) | USGS finite-fault products (Hayes, NEIC) | USGS product contents (live) | Subfault geometry parser unit-tested vs known Maule values; summed moment ≈ published M0 | DONE |
| Horizontal-slope coupling | Δh = u_z + **u**_h · ∇b (smoothed gradients) | Tanioka & Seno 1997 | bathymetry gradients (ETOPO1) | Exact check on synthetic planar slope | DONE |
| Water-column filter | η̂(k) = Δĥ(k)/cosh(k·d) (local-depth form) | Kajiura 1963 | — | Transfer-function identity via FFT impulse test; long-wave limit ≈ 1 | DONE |
| Propagation solver | 2-D nonlinear shallow-water equations, conservative form; well-balanced hydrostatic reconstruction; HLL fluxes; MUSCL + SSP-RK2 (2nd order, optional 1st); positivity with h_dry = 10⁻⁴ m; Manning friction (semi-implicit); Rayleigh sponge layers; 2-D-sum CFL ≤ 0.45 | Audusse et al. 2004 (hydrostatic recon); Toro 2009 (HLL); Gosse 2013 / NTHMP procedures (benchmarks); Madsen-Sørensen dispersion **omitted** (documented: non-dispersive NSWE, deep-water dispersion error grows over trans-oceanic distance; regional domains keep this small) | ETOPO1 bathymetry | Lake-at-rest (incl. wet/dry) to roundoff; mass conservation ≤ 10⁻¹²; positivity; solitary wave vs analytical sech²; long-wave speed vs √(gh); grid convergence; sponge reflection | DONE |
| Spherical terms | Cell areas vary with cos φ (variable-area FV); Coriolis and curvature metric terms omitted | standard regional-model practice; documented approximation (f ≪ ω for tsunami periods → first-arrival error < ~1% over a few hours) | — | Documented limitation | DONE (as documented omission) |
| Travel time | Eikonal ‖∇T‖ = 1/√(gh), fast-sweeping | Sethian 1999 (sweeping methods) | ETOPO1 | Analytic flat-plane test | DONE |
| Inundation metrics | Flow depth d_f = max(0, η−b) on land; run-up = max wetted b | NTHMP definitions | ETOPO1 only (≈1.8 km cells) | **BLOCKED at credible resolution**: ETOPO1 cannot resolve coastal flooding; wet/dry works mechanically, all land outputs carry a resolution caveat and no site-specific claim is made (per plan's own prohibition §18.7) | PARTIAL (coarse-DEM caveat enforced in UI) |
| De-tiding / detection | Local quadratic detide (±3 h); noise σ from pre-event window; trigger = \|v\| > k·σ (k=5 default); arrival = trigger time; amplitude = max\|v\| in +3 h; period from zero-crossings | Mofjeld et al. (DART methodology); NOAA NDBC archive format | NOAA DART archive (historical, 2005–2025) + realtime2 (≈45-day window, 45 stations) | Labeled synthetic fixtures (arrival within 1 sample; amplitude ≤ 10% err); noise-only controls (false-alarm rate); real records reproduce published values (DART 21418 Tōhoku max 1.86 m vs published ≈1.8 m) | DONE |
| Evidence classification | 5 states: insufficient / none-detected / possible / consistent / confirmed-official; seismic screening as labelled heuristic; per-station obs-vs-sim consistency (\|Δt\| ≤ 15 min, amplitude factor ≤ 3 — documented diagnostic tolerances); correlation caveat (buoys sample one wave field) | Task spec §4; plan §14 | DART + simulation + curated official links | Unit tests per level; no fabricated confirmation | DONE |
| Hindcast validation | Full pipeline: USGS FFM → ETOPO1 → solver → DART comparison | — | Tohoku 2011 (DART 21418/21413/21419/21401, archive), Maule 2010 (data-driven station selection), Panama 2026 (realtime DART 32413/43413) | Executed; results recorded in docs/validation.md and served in the UI | DONE |
| Uncertainty quantification | Monte-Carlo source ensembles | plan §11 | — | **BLOCKED** (CPU budget); deterministic runs only, no invented error bars | BLOCKED |
| Data assimilation (EnKF) | plan §8 | — | — | — | BLOCKED |
| Official warning feed | PTWC/NTWC alerts | — | no public machine API | curated official event links only, never auto-claimed | PARTIAL (by design) |
| Coastal tide gauges | IOC SLSM / CO-OPS | — | IOC service retains only ≈17 h (verified) — covers no curated event; CO-OPS is US-only | — | BLOCKED (documented) |
| Field run-up surveys | NCEI run-up DB | — | hazel API endpoints 404 (verified) | — | BLOCKED (documented) |

## 2. Data sources actually accessed (verified 2026-10-24)

| Source | Endpoint | Verified result |
|---|---|---|
| USGS ComCat FDSN | `earthquake.usgs.gov/fdsnws/event/1` | live, used for event metadata + moment tensors |
| USGS finite-fault | product `finite-fault` → `FFM.geojson` | Tohoku + Maule models retrieved (subfault polygons, slip, rake) |
| ETOPO1 Bedrock (bathymetry/topography, MSL datum) | NCEI THREDDS OPeNDAP `dodsC/global/ETOPO1_Bed_g_gmt4.nc.ascii` | 8°×6° @ 1′ subset in ≈4 s / 1.5 MB; grid registration at exact arc-minutes verified |
| NOAA DART historical | `ndbc.noaa.gov/data/historical/dart/{ID}t{YEAR}.txt.gz` | parsed; 9999.0 = missing; 15-min and 1-min (event-mode) cadences; Tōhoku signal in 21418 verified (σ ≈ 2.7 cm, peak 1.86 m) |
| NOAA DART realtime | `ndbc.noaa.gov/data/realtime2/{ID}.dart` | 45 stations, ≈45-day lookback → covers the 2026-10-09 Panama event window |
| DART station coordinates | NDBC station pages | current positions retrieved per station (redeployment caveat documented) |
| IOC sea level | `ioc-sealevelmonitoring.org/service.php` | works but retains ≈17 h only → not usable for curated events (documented) |
| CO-OPS water level | `api.tidesandcurrents.noaa.gov` | works, US coast only — not integrated v1 (documented) |
| NCEI tsunami run-up DB | hazel endpoints | 404 — BLOCKED (documented) |
| GEBCO WMS | `wms.gebco.net` | works (rendering service; ETOPO1 used for *data* instead) |

## 3. Honesty contract

* Every number the UI shows is either (a) an observation fetched live/cached from the
  sources above, (b) an output of a documented computation from those data, or (c) a
  clearly-labelled configuration constant.
* Synthetic signals exist **only** inside unit tests, labelled as fixtures.
* No arrival time, amplitude, inundation line, or classification is hardcoded.
* Missing data produce explicit states, never defaults dressed as results.
* "Confirmed" can only ever be cited to an official external source (link), never
  asserted by the software.
