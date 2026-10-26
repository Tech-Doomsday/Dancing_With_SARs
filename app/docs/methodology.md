# Methodology — end-to-end processing

This document mirrors the actual implementation
(`src/lib/science/pipeline.ts` and the modules it orchestrates). The
pipeline is deterministic: the same dataset and parameters always produce
the same result, which makes every run reproducible and cacheable.

## Stage A — input validation (`validation.ts`)

A dataset is rejected (with explicit, user-visible reasons) when:

- phase or coherence rasters are missing, or their dimensions are invalid;
- the phase and coherence grids are not co-registered (different shapes or
  origins);
- `numberOfLooks < 1` or the wavelength is outside 0.001–1 m;
- acquisition times are missing, not ISO-8601, or temporally inverted;
- fewer than 1% of sampled values are valid (the grid appears empty).

Warnings (non-fatal): non-UTM EPSG codes, incidence angles outside the
typical 20–40° range, degenerate footprint polygons, evidence datasets
without access notes.

## Stage B — preprocessing and referencing

1. **Quality mask:** a pixel is measurable only where phase and coherence
   are finite, coherence ≥ threshold (default 0.5; scene-adapted starting
   values may be suggested by the dataset registry — e.g. 0.1 for the
   ionosphere-destroyed Mojave pair — and remain user-adjustable), **and**
   the pixel belongs to a validated unwrapping connected component
   (component ≥ 1; 0 and the 65535 fill value are excluded). The component
   gate matters because GUNW products store finite, meaningless phase over
   non-unwrapped areas: the Mojave 2026 granule carries a ~5-rad noise field
   over its cc = 0 regions which — before this gate existed — produced a
   5,923 km² false detection from a scene whose genuinely unwrapped surface
   is stable to < 48 mm. When more than half of the coherence-passing
   pixels are excluded by the gate, a warning says so explicitly. Fewer
   than 100 surviving pixels aborts the run with a clear error.
2. **Reference region:**
   - far-field strategy (default): the outer 12% margin band of the frame;
   - full-frame median: all valid pixels.
   If the reference region has fewer than 50 pixels, the strategy falls
   back to the full frame with a warning (large anomaly may violate the
   far-field assumption).
3. **Offset removal:** the median displacement over the reference region is
   subtracted from all valid pixels. (Median, not mean: 50% breakdown point
   against unwrapping outliers.)
4. **Reference scatter:** the MAD-based σ of the reference region feeds the
   per-pixel uncertainty. If the MAD is degenerate — more than half of the
   reference pixels share one exact value, as when SNAPHU emits exact zeros
   over a flat, fully coherent basin — the standard deviation is used
   instead and a warning explains the fallback.
5. **Optional planar detrend (default off):** a least-squares plane fitted
   over the reference region may be removed, with the standing caveat that
   a plane can absorb real long-wavelength deformation. The caveat is
   surfaced in the UI and in a pipeline warning when applied.

Upstream processing (orbit, coregistration, topographic/ellipsoidal
flattening, SNAPHU unwrapping, geocoding) is performed by the NISAR SDS
before distribution; MOTUS reads the product's own flags documenting each
step and displays them, rather than reprocessing (see docs/data-sources.md
§§2–3). **Correction status (corrected 2026-10-21):** per the official ASF
GUNW guide, the ionospheric phase screen is supplied but NOT applied to the
stored phase, and tropospheric corrections are supplied as lookup tables
and NOT removed from the interferometric data (docs/science.md §2.1). All
MOTUS measurements are therefore *apparent relative LOS range change*.

## Stage C — change measurement

- Phase → displacement: `d = λφ/(4π)` under the verified NISAR convention
  (docs/science.md §2); positive = away from the satellite. Because the
  product does not remove atmospheric/ionospheric contributions from the
  stored phase, this quantity is the **apparent relative LOS range change**.
- Summary statistics over valid pixels: affected area, toward/away areas,
  extremes, displacement-weighted anomaly centroid, principal-axis
  orientation and elongation (second moments of |d|), and the signed
  displacement integral over the anomaly (m·km²).
- **Detection bookkeeping (2026-10-21):** masking is separated from
  classification — `maskedPixelCount`, `detectedPixelCount`, `validFraction`
  and the median threshold actually used are reported; connected clusters of
  detected pixels (4-neighbourhood, ≥ 4 px) are counted with sizes and the
  largest cluster area.
- **Null-scene screen (2026-10-21, exploratory):** 8 deterministic
  simulated no-change scenes with spatially correlated Gaussian noise
  (correlation length 8 px at analysis-grid scale) are pushed through the
  same detection rule; the false-exceedance fractions and cluster counts are
  reported as `summary.nullScreen` and labelled exploratory — never as a
  calibrated false-alarm rate.
- Connected components: components of the unwrapping label map holding ≥1%
  of valid pixels are counted; value 65535 (invalid) is excluded. Multiple
  components are flagged in the reliability narrative as potential
  cycle-jump boundaries. A single component lowers, but does not eliminate,
  the chance of cycle errors (never stated as proof of absence).

## Stage D — uncertainty

Per-pixel one-sigma:

```
σ_total = sqrt( σ_pixel² + σ_ref² )
```

- `σ_pixel` from the coherence formula (Rodríguez & Martín 1992) with the
  product's look count (119 for the analysed sample);
- `σ_ref` = MAD-based robust sigma of the reference region (standard-
  deviation fallback when the MAD degenerates, with a warning).

**Ionospheric systematic floor (semantics corrected 2026-10-21):** because
the supplied screen is NOT applied to the stored phase, the unresolved
ionospheric contamination is bounded by the LARGER of the unapplied screen's
own spatial variation and the screen estimate's uncertainty — both
extracted per product. The floor is spatially correlated and therefore
raises the detection floor rather than the per-pixel noise
(docs/science.md §5.2); its basis is reported (`ionoFloorBasis`).

**Detection rule:** `|d| > max(k·σ_total, floor)` with k = 3 by default and
floor = max(10 mm, ionospheric systematic). The machine-readable
`correctionStatus` block in every result states exactly which corrections
the consumed layers do and do not carry.

## Stage E — attribution

1. The API layer queries USGS ComCat (±1 day around the acquisition window,
   footprint bbox + 0.5°, M ≥ 4) and filters events to those strictly
   inside the window and within 400 km.
2. **Event selection is explicit (2026-10-21):** in-window candidates are
   ranked by magnitude (descending), then distance to the footprint centroid
   (ascending), then moment-tensor availability. The selected event, the
   alternatives and the exclusion reasons travel with every result
   (`attribution.eventSelection`); the first API result is never assumed to
   be the most relevant.
3. If an event with a published nodal plane exists, an Okada85 forward
   model is evaluated on the dataset grid (uniform slip; fault dimensions
   from Wells & Coppersmith 1994 area scaling with 2:1 aspect; moment from
   Hanks & Kanamori 1979; slip = M0/(μA)) — **anchored at the epicentre
   projected into the raster's own CRS** (docs/science.md §7.1) — and
   compared with the measurement: amplitude ratio and Pearson pattern
   correlation. The
   model field is evaluated on a deterministic strided subgrid of at most
   ~400k points (the Okada field is smooth at grid scale; full NISAR
   frames reach ~2×10⁷ pixels and need not be evaluated exhaustively),
   and peaks are computed with explicit loops — never `Math.max(...arr)`
   argument spreads, which overflow on large arrays.
4. Hypotheses (coseismic, slow surface motion, atmospheric residual,
   unwrapping artefact, no-change-above-threshold) receive evidence-linked
   statuses; the screening values (amplitude ratio within a factor of 3;
   r > 0.25; the ~30 mm atmospheric-plausibility value) are **exploratory
   conventions, not calibrated statistics** — the values and their rationale
   are attached to every result (`attribution.heuristics`). Consistency is
   never described as causation; "no change above threshold" never becomes
   "no change"; a single unwrapping component is never treated as proof of
   artefact absence.
5. The method note is always displayed, including the statement that the
   layer is not a calibrated classifier.

## Synthetic validation comparison

For fixtures with analytic ground truth, the pipeline compares its output
with the truth on a deterministic sparse sample (~50k points) and reports
MAE / RMSE / bias / n. This validates the processing mathematics only —
the note displayed with every validation block says so.

## Transport to the browser

Results are downsampled (area-average, ≤640 px per side, mask-preserving)
into a `TransportGrid` (Float32 displacement/coherence/σ + Uint8 mask,
base64 little-endian) plus the grid georeferencing. The browser re-renders
the grid into a Web-Mercator-exact canvas (per-pixel Mercator inversion →
UTM → nearest-neighbour sample) and overlays it as a geographically exact
image rectangle. Recolouring on layer/scale changes does not re-warp.

## Narrative generation

Plain-language blocks are assembled from the measured numbers (templates in
`pipeline.ts`): *What changed? How much? How reliable is this result?*
A credibility rule shared with the attribution layer gates the
"ground motion" phrasing: an affected area below max(1 km², 4 pixels) is
described as **isolated stray pixels** consistent with statistical noise,
not as detected motion — the regime where a handful of tail pixels cross
the threshold of a heavily decorrelated scene. Every quantity quoted in a
sentence comes from the computed summary — no
canned claims.

## Tooling

- `tools/extract_gunw.py` — documented, lossless HDF5 → derived-layers
  extractor with full metadata capture (see docs/data-sources.md §§2–3).
- `src/lib/science/fixtures.ts` — deterministic fixture generator chain:
  Okada truth → LOS projection → phase conversion → coherence-consistent
  noise → atmospheric ramp → decorrelation patches.

## Defaults

| Parameter | Default | Meaning |
|---|---|---|
| `coherenceThreshold` | 0.5 | minimum coherence to measure |
| `referenceStrategy` | farField | 12% frame margin |
| `farFieldMargin` | 0.12 | margin fraction |
| `detrend` | false | planar detrend off |
| `detectionFloor` | 0.01 m | physical floor (raised by iono systematic when present) |
| `significanceK` | 3 | k·σ multiplier |

All parameters are exposed in the UI's advanced controls with sensible
bounds (Zod-validated server-side).
