# Tsunami Capability — Methods, Discretization, Benchmarks, Limitations

Status: implemented 2026-10-24. This document is the technical reference for the
tsunami subsystem (`src/lib/tsunami/`), written so that every numerical choice can
be audited against its primary source. The companion evidence matrix
(`docs/tsunami-evidence-matrix.md`) maps features → references → data → status.

## 1. What the system does

Given a real earthquake (curated registry, live USGS metadata):

1. **Observation check** — fetches real NOAA DART bottom-pressure records
   (historical archive 2005–2025 + realtime ≈45-day window), de-tides them and
   runs a noise-adaptive detection (§5).
2. **Source construction** — USGS finite-fault slip inversions (FFM.geojson)
   where published, otherwise the moment-tensor plane + Wells–Coppersmith
   scaling (labelled approximation); Okada elastic dislocations summed over
   subfaults; Tanioka–Seno bathymetric-slope correction; Kajiura water-column
   filter (§2).
3. **Propagation** — 2-D nonlinear shallow-water equations on ETOPO1
   bathymetry, solved with a well-balanced, positivity-preserving
   finite-volume scheme (§3).
4. **Independent travel time** — eikonal fast sweeping (§4).
5. **Comparison & evidence** — observed vs simulated arrivals/amplitudes per
   station with documented tolerances; a five-level evidence classification
   that never conflates detection with attribution (§6).

## 2. Source initialization

- **Okada (1985/1992).** The project's TypeScript port of the official DC3D
  code (cross-validated against `dc3d.py` in `tests/science/okada.test.ts`).
  Subfaults use the top-edge placement (`geoFaultDisplacementENUTopEdge`).
  Static (instantaneous) rupture — valid for far-field phases whose period ≫
  rupture duration (~2–3 min for M9).
- **Tanioka & Seno (1997).** Δh = u_z + **u**_h·∇b with bed gradients smoothed
  over a 3×3 box (unsmoothed DEM gradients amplify noise). Wiring verified
  cell-by-cell in tests.
- **Kajiura (1963).** η̂(k) = Δĥ(k)/cosh(k·d) implemented as a separable
  row/column FFT pass with each line's mean wet depth (local-depth form).
  Identical to leading order for long waves (k·d ≪ 1), which is the seismic
  regime; verified against the exact transfer function on a Fourier-mode
  fixture. Cells whose filtered surface falls below the bed start dry.
- **Scaling fallback.** log₁₀(A/km²) = 0.91·M_w − 3.49 (Wells & Coppersmith
  1994, all slip types; scatter ≈ ×1.8), uniform slip from M₀ = μ·A·s with
  μ = 30 GPa. The UI labels this an approximation.

## 3. NSWE finite-volume solver (`fv-solver.ts`)

Equations (b positive up, h = η − b, Manning friction, no Coriolis):

    ∂h/∂t + ∂(hu)/∂x + ∂(hv)/∂y = 0
    ∂(hu)/∂t + ∂(hu² + ½gh²)/∂x + ∂(huv)/∂y = −gh ∂b/∂x − g n² u|u|/h^{1/3}
    ∂(hv)/∂t + ∂(huv)/∂x + ∂(hv² + ½gh²)/∂y = −gh ∂b/∂y − g n² v|v|/h^{1/3}

- **Well-balancing:** hydrostatic reconstruction (Audusse et al. 2004) with
  double-sided pressure corrections. With MUSCL, the correction uses the
  RECONSTRUCTED cell-side depth — using the cell-centre depth cancels the
  physical pressure gradient (a bug caught by the wave-speed benchmark;
  momentum came out ~10³× too small).
- **Fluxes:** HLL with Einfeldt speeds and George (2008) dry-front speeds.
  HLLC's contact wave is immaterial for tsunami propagation (documented
  choice; production codes make the same trade).
- **Time integration:** SSP-RK2 (Heun) `Uⁿ⁺¹ = ½Uⁿ + ½E(E(Uⁿ))` with MUSCL
  reconstruction (monotonized-central limiter) of (η, hu, hv); order 1
  available (piecewise-constant + single Euler stage).
- **Flattening:** reconstruction reverts to piecewise-constant when either
  side's depth < 0.1 m — second-order reconstruction is not positivity-safe
  at wet/dry fronts (zero-friction sloshing blew up without it).
- **Thin-film limiter:** momentum zeroed below 1 cm (mass preserved);
  desingularized velocity u = hu·h/(h² + (1 cm)²) in the interface states.
- **CFL:** Δt ≤ C / max((|u|+c)/Δx + (|v|+c)/Δy), C = 0.45 (order 2) / 0.5,
  recomputed every step, with the dry-front characteristic |u|+2c for cells
  bordering dry neighbours (the exact dam-break-into-dry-bed front speed;
  omitting it silently destroys mass through the negative-depth clamp).
- **Boundaries:** Rayleigh sponge layers (quadratic ramp, ≤ 15% of either
  grid dimension, width ~35/√res' cells) at every edge + reflective wall
  fluxes (mirrored-ghost HLL) behind them. Sponges absorb outgoing wave mass
  BY DESIGN — run summaries report the drift including that absorption.
- **Geometry:** local Cartesian (tangent-plane) at the grid's centre
  latitude. With per-row cos φ widths and no spherical metric terms, a
  variable-area scheme cannot be simultaneously conservative and exactly
  well-balanced (measured pressure residual ~1e-4 m/s/step). The tangent
  plane makes both properties exact; the cost is a zonal-distance error
  ≤ ~5% at the edges of a ±5° domain (acceptable against the ±15 min
  arrival tolerance and small next to source-model uncertainty).

## 4. Travel-time field (`traveltime.ts`)

Eikonal ‖∇T‖ = 1/√(g·h) by Gauss–Seidel fast sweeping (alternating orders,
Zhao 2005) with the two-sided Godunov local update (the PLUS quadratic root;
the minus root is the non-upwind branch). Seeded at cells with |Δh| ≥
10%·max|Δh|. First-order accurate: ~3.6% diagonal error at 21 cells
(documented; axis errors < 0.1%).

## 5. Detection (`detection.ts`)

- **De-tiding:** robust local quadratic regression over ±3 h windows — pass 1
  fits [1, x, x²]; window samples with pass-1 residuals > 3× the window MAD
  (×1.4826) are excluded and the fit redone (the tsunami itself must not bias
  the tide fit — measured 13% amplitude loss without the robust pass). The
  centre sample is maskable too. Tide leak ≤ ~1.5 cm for a 0.6 m semidiurnal
  tide. This replaces operational harmonic prediction (FES2014/UTide) because
  the Copernicus service requires an account (documented deviation).
- **Noise scale:** robust MAD estimate over [origin−6 h, origin−10 min] — a
  seismic-phase excursion inside the window would inflate an RMS and push the
  threshold above the excursion itself.
- **Trigger:** first |residual| > k·σ (k = 5) after origin−15 min, excluding a
  configurable 8-min seismic-phase mask after origin (DART sensors record the
  earthquake's Rayleigh/E phase minutes before the water wave; verified on
  Tōhoku: the +3 min trigger at 21418 is seismic, the water wave arrives
  ~+29 min). Pre-origin triggers are flagged as such, never reported as
  arrivals.
- **Amplitude:** max |residual| in [trigger, trigger + 3 h].
- **Period:** median zero-crossing spacing × 2 in the first hour.
- Every parameter is surfaced in the returned provenance; missing coverage is
  an explicit `availability: 'missing'` state, never a silent null.

## 6. Comparison and evidence (`evidence.ts`)

- Per station: |Δarrival| and amplitude ratio vs documented diagnostic
  tolerances (±15 min ≈ monitor-mode cadence + model timing error; factor 3 ≈
  source + ETOPO1 uncertainty). Stations inside the boundary sponge are
  flagged not-comparable instead of compared.
- Five evidence levels (insufficient / none-detected / possible / consistent /
  confirmed-official) with reasons rendered in the UI. "Confirmed" is only
  ever a citation to an official external page.
- Correlation honesty: multiple buoys sample one wave field — stated as a
  caveat wherever ≥2 stations agree.
- Seismic screening (M ≥ 6.5, depth ≤ 50 km, dip-slip fraction ≥ 0.3) is
  labelled a heuristic in the UI; it is a prior, never a verdict.

## 7. Executed verification (2026-10-24)

`bun test` — 173 tests / 22,859 assertions, 0 failures, of which 46 tsunami
tests. Highlights (acceptance criteria in the test files):

| Benchmark | Result |
|---|---|
| Lake at rest over a steep ridge (wet/dry), order 1 & 2 | max u ≤ 3e-14 m/s (criterion 1e-6) |
| Lake at rest, η ≡ 2 m over ridge | ≤ 1e-13 m/s |
| Wet/dry shoreline at rest | ≤ 1e-6 m/s |
| Dam break onto dry bed (order 2) | mass drift 4.6e-15 relative (criterion ≤ 1e-12), 0 positivity violations |
| Ritter (1892) front | fitted front speed within [0.6, 1.1]× 2√(g·h_L); per-gauge arrivals carry a constant startup lag — fixed-grid front-capture limitation, documented |
| Linear long wave, c = √(gh) | arrival within 3% (incl. 30 s gauge quantization) |
| Grid convergence 4′→2′→1′ | peak error 0.0103 → 0.0028 → 0.0024 m (order-2 behaviour) |
| Sponge absorption | late-time interior amplitude < 5% of input |
| FFT | round-trip + Parseval exact; non-power-of-two rejected |
| Kajiura transfer | Fourier mode attenuated by 1/cosh(k·d) (≤ 2% deviation) |
| Tanioka–Seno wiring | correction equals u_h·∇b to mm-level at sampled cells |
| Detection (synthetic, labelled) | arrival within one sample; amplitude within 2.5%; 0 false alarms / 12 noise seeds; pre-origin + seismic-phase excursions flagged |
| Travel time | flat plane: axis 0.05%, diagonal 3.6% (first-order scheme) |
| Evidence classification | all five states unit-tested incl. the not-consistent and insufficient branches |

Real-data hindcasts (results recorded in `docs/validation.md`): Tōhoku 2011,
Maule 2010, Panama 2026 — live USGS sources, live ETOPO1 subsets, archived
NOAA DART records.

## 8. Limitations (all surfaced in the UI where relevant)

1. **Coarse DEM:** ETOPO1 (1′ best case, ~1.8 km cells) cannot resolve
   coastal inundation. Wet/dry works mechanically; land flow depths are
   indicative only and no site-specific claim is made (per the research
   plan's own prohibition).
2. **Non-dispersive NSWE:** frequency dispersion is not modelled; trans-oceanic
   (≫ 1000 km) wave trains will be too sharp. Regional domains keep this
   error small. Boussinesq: not implemented (documented, not imitated).
3. **Coriolis / spherical metric terms omitted** (tangent-plane geometry,
   §3). Regional-domain approximation.
4. **Static rupture**, uniform-slip fallback concentrates peak uplift;
   WC94 area scatter ≈ ×1.8.
5. **No uncertainty quantification:** deterministic runs only; no ensemble was
   computed, so no error bars are shown anywhere.
6. **Station positions are current deployments** — DART buoys are redeployed;
   historical-event comparisons carry this caveat explicitly.
7. **Detection thresholds are diagnostic**, not calibrated probabilities;
   false-alarm behaviour is characterized on synthetic controls only.
8. **Blocked capabilities** (documented in the evidence matrix, never faked):
   EnKF assimilation, F-FNO surrogates, GPU acceleration, tide-gauge
   integration (IOC retains ~17 h; CO-OPS US-only), NCEI run-up database
   (API 404), FES2014 harmonic de-tiding (account required).

## 9. Reproduction

```bash
bun run db:push          # Prisma schema incl. TsunamiRun audit table
bun test                 # 173 tests incl. the 46 tsunami benchmarks
bun run dev              # then open / (tsunami section below the radar workspace)
# APIs:
#   GET  /api/tsunami/events
#   GET  /api/tsunami/events/[id]
#   GET  /api/tsunami/observations?event=<id>
#   POST /api/tsunami/simulate   { eventId, params }
#   GET  /api/tsunami/runs/[id]  (poll; full result when finished)
```

Data caches: `data/cache/etopo/` (bathymetry subsets), `data/cache/dart/`
(pressure records), run artifacts `data/derived/tsunami/<runId>/`.
