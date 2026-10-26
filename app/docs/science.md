# Science foundations

This document records the physical and statistical basis of every scientific
computation in MOTUS, the conventions (and how they were verified), and the
conditions under which each method fails.

## 1. The measurement problem

A synthetic-aperture radar (SAR) satellite transmits microwave pulses and
records the echoes. The phase of each echo depends on the distance the wave
travelled — the two-way *slant range* between the antenna and the ground.
When the same ground is observed twice from nearly the same orbital position
(a *repeat-pass interferometric pair*), a surface displacement along the
line of sight (LOS) changes that distance and shifts the echo phase.

The satellite measures the projection of the 3-D displacement vector onto
its LOS direction: a mix of vertical and horizontal motion, not height
change alone. Separating vertical from horizontal motion requires at least
two different viewing geometries (e.g. an ascending and a descending pair);
MOTUS analyses one pair at a time and does not claim to resolve the full
vector.

Key properties of the measurement:

- **One fringe = λ/2 of range change.** For the NISAR L-SAR wavelength
  (λ = 0.236 m from the product's centre frequency 1.26999975 GHz), one
  2π phase cycle = 118 mm of LOS motion.
- **The phase is ambiguous modulo 2π.** Unwrapping (performed upstream by
  the product generator with SNAPHU) recovers the integer-cycle count but
  can fail in low-coherence regions; whole-cycle errors appear as
  λ/2 · k steps (118 mm · k for L band).
- **The measurement is relative.** An interferogram's phase has an unknown
  2πk offset; a reference region must be chosen (see §4).

## 2. The NISAR phase convention (verified, not assumed)

The sign of the phase→displacement conversion is fully determined by which
scene is conjugated when forming the interferogram — the most common silent
error in InSAR software. MOTUS follows the convention of the NISAR SDS
itself, as specified in the official Algorithm Theoretical Basis Document:

> **Source:** NISAR NASA SDS Algorithm Theoretical Basis Document, JPL
> D-95677 Rev A (12 Nov 2023), §5.7 "Coregistered RSLCs cross-multiplication"
> and §5.11.1. Verified against the document text on 2026-10-20.

- The interferogram is formed by cross-multiplying the **reference** RSLC
  with the **secondary** RSLC, i.e. `I = S_ref · conj(S_sec)`.
- Flattening multiplies `exp(−j(4π/λ)δr)` where δr is the geometrical range
  offset between the two images.
- With the SLC phase convention `S = exp(−j4πR/λ)`, the interferometric
  phase is

```
φ = (4π/λ) · (R_secondary − R_reference)
```

whose components the ATBD decomposes as:

```
Δφ = (4π/λ) · (Δr_d + Δr_g + Δr_t + Δr_i + Δr_n)
      displacement  geometric  troposphere  ionosphere  noise
```

### 2.1 Correction status of the consumed layer (CORRECTED 2026-10-21)

**Earlier versions of this document stated that the GUNW product applies
the tropospheric and ionospheric corrections upstream. That statement was
wrong.** The authoritative ASF NISAR GUNW product guide
(https://nisar-docs.asf.alaska.edu/gunw/, retrieved 2026-10-21) states:

> "The estimated ionospheric phase screen is included in the product but not
> applied to the data layers within the GUNW product by default."
>
> "The GUNW product also includes geocoded lookup tables for external phase
> corrections (e.g., solid Earth tides, hydrostatic and wet tropospheric
> phases). These phase corrections, when available, are not removed from the
> interferometric data."

This was cross-checked against the sample product's own embedded run
configuration (`science/LSAR/GUNW/metadata/processingInformation/parameters/runConfigurationContents`),
which shows `ionosphere_phase_correction.enabled: true` (the screen is
*estimated and included*) and `troposphere_delay` writing LUT products —
consistent with "supplied, not applied". The metadata flags
`parameters/geocoding/*CorrectionApplied` that earlier versions cited record
corrections applied during the **geocoding (geolocation) step**, not phase
corrections applied to the interferogram.

What IS removed from the stored phase: the geometric term
(`topographicFlatteningApplied`/`ellipsoidalFlatteningApplied` = True).

**Consequence for every MOTUS measurement:** applying

```
d_LOS = λ · φ / (4π)
```

to the stored `unwrappedPhase` yields the **apparent relative range change
along the line of sight** — the sum of true ground displacement and the
uncorrected tropospheric and ionospheric contributions (plus noise). MOTUS
labels it exactly that way, quantifies the unresolved ionospheric systematic
(§5.2), and never describes a single-pair result as verified tectonic
displacement. The sign convention is unchanged:

**positive d_LOS = an increase in range = ground motion AWAY from the
satellite**; negative = toward the satellite.

Note: the frequently quoted alternative `d = −λ·φ/(4π)` (e.g. in much
Sentinel-1 tooling) corresponds to the opposite conjugation order
(`S_sec · conj(S_ref)`). Both are correct within their own convention; the
error is to mix them. MOTUS documents and tests its convention
(`tests/science/core.test.ts`).

## 3. Line-of-sight geometry

For a right-looking satellite with heading H (degrees clockwise from north)
and incidence angle θ (from vertical at the target), the ground-to-satellite
unit vector in local East-North-Up components is:

```
û_gs = ( sin θ · sin(H+90°),  sin θ · cos(H+90°),  cos θ )
```

A displacement vector **u** projects onto range change as
`d = −u · û_gs` (motion toward the satellite shortens the range).

Verification: the NISAR sample product's radarGrid stores
`losUnitVectorX/Y` as the **satellite-to-ground** direction. For the
analysed granule (mean incidence 24.17°, ascending, heading −11.80° derived
from the same vectors) the stored means are (−0.4005, −0.0837); the
reconstruction above reproduces them to within 0.02 after sign flip
(asserted in `tests/science/core.test.ts`).

## 4. Referencing (Stage B)

An unwrapped interferogram carries an unknown constant. MOTUS estimates it
as the **median** of the displacement over a reference region and subtracts
it:

- **far-field (default):** the outer 12% margin band of the frame, where
  deformation is assumed absent;
- **full-frame median:** the median over all valid pixels.

The residual scatter of the reference region (robust MAD-based σ) becomes
part of the error budget. *Failure mode:* if deformation covers the entire
frame, the reference absorbs part of the signal — a caveat MOTUS states in
the reliability narrative. An optional planar detrend (default **off**)
exists for advanced use, with the explicit warning that a plane can absorb
real long-wavelength deformation.

## 5. Uncertainty model (Stage D)

### 5.1 Coherence-based phase noise

For an interferogram averaged over L independent looks with coherence
magnitude γ, the phase standard deviation is estimated with the classical
expression (Rodríguez & Martin 1992, IEE Proc.-F 139(2), Eq. 15; see also
Hanssen 2001, §4.2.2):

```
σ_φ = sqrt( (1 − γ²) / (2 · L · γ²) )      σ_d = λ σ_φ / (4π)
```

Worked examples for the analysed product (L = 119 looks, λ = 0.236 m):

| γ | σ_φ (rad) | σ_d |
|---|-----------|-----|
| 0.5 | 0.112 | 2.1 mm |
| 0.7 | 0.070 | 1.3 mm |
| 0.8 | 0.057 | 1.1 mm |
| 0.9 | 0.042 | 0.8 mm |

*Failure mode:* the expression is accurate for γ ≳ 0.3–0.4 and
underestimates the spread at low coherence, where the phase distribution
deviates strongly from Gaussian. Pixels below the coherence threshold
(default 0.5) are masked rather than measured, so the formula is only used
in its valid range.

### 5.2 The ionospheric systematic floor (semantics corrected 2026-10-21)

Because the ionospheric phase screen is supplied but NOT applied to the
stored phase (§2.1), the unresolved ionospheric contamination of the
apparent LOS range change is bounded by the LARGER of two statistics that
MOTUS extracts from every product:

1. **the unapplied screen's own spatial variation** — the ionospheric signal
   actually present in the measurement after constant referencing. Extracted
   as both a robust MAD-based σ and a standard deviation of the
   `ionospherePhaseScreen` layer;
2. **the screen estimate's uncertainty** — the median of the
   `ionospherePhaseScreenUncertainty` layer, i.e. how poorly the true
   ionosphere is known even if a user chose to apply the screen.

A correction-uncertainty value alone would NOT describe the spatially varying
contribution that remains in the data — which is why both statistics are
extracted and the maximum is used. The floor is a spatially correlated
systematic: it is deliberately NOT folded into the per-pixel σ (that would
let it shrink with averaging like random noise); instead it raises the
detection floor, is reported as `summary.systematicIonoFloorM` with its
basis (`summary.ionoFloorBasis`), and is named in the reliability narrative.

Worked cases:

- **LA 2008 sample product**: screen variation σ ≈ 0.37–0.60 rad
  (7–11 mm), screen-estimate uncertainty ≈ 4.31 rad ≈ **81 mm** → the
  uncertainty dominates (basis: `uncertainty`), and the product honestly
  reports *no detection above floor*.
- **Mojave 2026 operational granule** (from the 2026-10-20 analysis,
  locally not reproducible since the granule was not retained): screen
  variation of tens of radians dominated (basis: `variation`), floor ≈ 72 mm
  against a max apparent LOS motion of 48 mm → no detection.

### 5.3 Combined detection rule

A pixel counts as *changed* only when

```
|d| > max( k · σ_total , physical floor )
```

with defaults k = 3 and floor = 10 mm (or the ionospheric systematic when
larger). The physical floor guards against declaring sub-noise variations
significant when coherence is very high but systematic residuals dominate —
the realistic situation for single L-band pairs.

### 5.4 Detection statistics and their statistical status (2026-10-21)

A per-pixel threshold plus a minimum cluster size does not by itself
establish a controlled false-positive rate over a large, spatially
correlated raster. MOTUS therefore:

- separates valid-pixel masking from detected-pixel classification and
  reports `maskedPixelCount`, `detectedPixelCount`, `validFraction` and the
  median threshold actually used;
- computes connected-cluster statistics of the detected pixels
  (4-neighbourhood, ≥ 4 px minimum) and reports counts and sizes;
- runs an **exploratory null-scene screen**: simulated spatially correlated
  Gaussian noise (deterministic seeds, configurable correlation length)
  scaled by the scene's own σ map is pushed through the same detection rule,
  and the false-exceedance fractions and cluster counts are reported in
  `summary.nullScreen`;
- **does not claim a formal false-alarm rate** — calibrating one would
  require the scene-dependent covariance of atmosphere + ionosphere +
  decorrelation, which is not available to this system. The screen's numbers
  are labelled exploratory wherever they appear.

A zero detected area is reported as "no change detected above the configured
threshold … inconclusive at the available sensitivity" — never as proof that
the ground did not move.

## 6. Elastic dislocation modelling (Okada 1985)

### 6.1 Implementation and validation

`src/lib/science/okada.ts` is a surface-displacement port of the DC3D
algorithm (Okada 1985; Okada 1992), following the reference Python port
[dc3d.py](https://github.com/satoshi-pes/dc3d.py) (vendored at
`tools/dc3d_reference.py`).

- **Benchmark:** 24 geometries × 3 slip modes (strike/dip/tensile; dips
  10–90°, varied depths and footprints) agree with the reference to a worst
  relative difference of 0.0 (8 significant digits; 72 scalar comparisons).
  The benchmark values are frozen in `tests/science/okada.test.ts`.
- **Analytic calibration:** the friendly wrapper's buried vertical
  strike-slip matches the 2-D anti-plane solution
  `u(y) = (1/π)[atan(y/d₁) − atan(y/d₂)]` within ~1–10% (finite-length
  effects account for the residual at distance).
- **Fault placement:** DC3D's `aw` convention (positive = up-dip from the
  reference point; a buried fault spans `aw = [−W, 0]`) was determined
  empirically after an initial placement bug produced unphysical fields —
  caught precisely because the analytic calibration disagreed. The
  calibration is now a permanent test.

### 6.2 Slip-sense conventions (determined empirically)

Trace-jump tests on a near-surface-breaking steep fault fix the senses:

- **U1 (strike-slip) positive = sinistral** (left-lateral);
- **U2 (dip-slip) positive = normal** sense (hanging wall drops);
- **U3 (tensile) positive = opening.**

With the Aki & Richards rake convention (rake measured from strike in the
fault plane; 0 = sinistral, ±180 = dextral, +90 = reverse, −90 = normal),
the mapping used everywhere in MOTUS is:

```
U1 = slip · cos(rake)        U2 = −slip · sin(rake)
```

### 6.3 Scaling relations

- Moment–magnitude (Hanks & Kanamori 1979): `M0 = 10^(1.5 Mw + 9.1)` N·m.
- Rupture area (Wells & Coppersmith 1994, all slip types):
  `log10(A/km²) = 0.91 Mw − 3.49`, with regression scatter ≈ 0.25 decades
  (factor ~1.8). A 2:1 length:width aspect ratio is assumed.
- Uniform slip: `slip = M0 / (μ A)`, μ = 3×10¹⁰ Pa.

*Failure mode:* the scaling relations make the forward model an
order-of-magnitude screen. The Panama evidence page says so explicitly, and
the attribution thresholds (below) are correspondingly coarse.

## 7. Attribution (Stage E)

Candidate hypotheses — coseismic deformation, slow surface motion
(subsidence/uplift/landslide), atmospheric/ionospheric residual,
unwrapping artefact, and no-change-above-threshold — are evaluated by
transparent, evidence-linked rules (never a trained classifier):

- **Event selection is explicit (2026-10-21).** In-window USGS ComCat
  candidates are ranked by magnitude (descending), then distance to the
  footprint centroid (ascending), then moment-tensor availability; the
  selection, the alternatives and the exclusion reasons are returned with
  every result (`attribution.eventSelection`). The first API result is never
  assumed to be the most relevant.
- **Forward-model consistency screen:** the Okada prediction is anchored at
  the epicentre projected into the raster's own CRS (§7.1) and compared with
  the measurement via peak-amplitude ratio and spatial correlation.
  Agreement classes — "consistent" (amplitude within a factor of 3 AND r >
  0.25), "plausible" (one check), "not-supported" (neither) — are
  **exploratory screening values, not calibrated statistics**; the values and
  their rationale travel with every result (`attribution.heuristics`), and
  consistency is never described as causation.
- **Insufficient evidence is a first-class output.** Every hypothesis carries
  its evidence items and a "what would settle it" note. "No change above the
  detection threshold" means exactly that — not that the ground did not
  move. A single unwrapping component is never treated as proof of artefact
  absence.

*Failure modes:* amplitude ratio and correlation can be misleading when the
event lies outside the coherent footprint or when the empirical rupture
dimensions are far off; the UI always displays the check's components so a
reader can judge.

### 7.1 Forward-model coordinate frame (corrected 2026-10-21)

Earlier versions computed raster offsets for the forward model relative to
the **raster centre** while the fault geometry carried a geographic
epicentre — misplacing the model whenever the epicentre was not at the grid
centre (the synthetic fixtures hid this because their faults sit at the
grid centre). The frame is now:

- The raster CRS is read from the grid metadata (EPSG; WGS84 UTM zones
  326xx/327xx are supported — the CRS of the NISAR GUNW products consumed).
- The **epicentre** (lat/lon) is projected into that same zone with the
  Snyder transverse-Mercator series (`latLonToUtmInZone`), so both the
  raster and the event live in one common projection; degrees are never
  mixed with metres.
- Per-pixel offsets are `(x − epicentre.x, y − epicentre.y)` — relative to
  the event, not the grid.
- Fault-frame conventions (strike clockwise from north; dip from horizontal;
  Aki & Richards rake; depth of the top edge derived from the centroid
  depth) are unchanged and documented in §6.
- Non-UTM grids (e.g. polar stereographic EPSG 3413/3031) disable the
  quantitative forward model with an explicit reason rather than silently
  misplacing it; events without a published mechanism are skipped, never
  invented.

The invariance is tested exactly in `tests/science/attribution.test.ts`:
the prediction at a fixed geographic point is *bit-identical* on shifted
grids, and moving the event moves the predicted peak with it.

## 8. Geodesy

UTM forward/inverse conversions implement the Snyder (1987) transverse
Mercator series (USGS Professional Paper 1395, ch. 8), accurate at the
metre level within a zone — far below the 80 m posting of the products, and
used only for map alignment and footprint construction. The browser-side
raster overlay renders in Web Mercator geometry by inverting Mercator per
canvas pixel and sampling the UTM grid, so the overlay is geographically
exact by construction (grid-zone edges are masked rather than distorted).

## 9. What this system does not claim

- No vertical/horizontal decomposition (single geometry).
- No time-series analysis (single pair per run).
- No causal probabilities (rule-based screening only).
- No fabrication: when the Panama HDF5 cannot be downloaded (Earthdata
  credentials), MOTUS presents metadata, official imagery and a labelled
  forward model — and says that is what it is doing.
