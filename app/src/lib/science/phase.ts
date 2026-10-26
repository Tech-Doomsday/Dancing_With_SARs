/**
 * Interferometric phase <-> line-of-sight displacement conversions.
 *
 * CONVENTION (documented from the official NISAR SDS documents):
 *
 * The NISAR L1/L2 ATBD (JPL D-95677, Rev A, 12 Nov 2023, Section 5.7
 * "Coregistered RSLCs cross-multiplication") specifies that the wrapped
 * interferogram is formed by cross-multiplying the *reference* RSLC with the
 * *secondary* RSLC and is flattened by multiplying exp(-j (4pi/lambda) dr),
 * where dr is the geometrical range offset between the reference and secondary
 * images. Writing S = exp(-j 4 pi R / lambda) for the focused SLC phase, the
 * resulting interferogram phase is
 *
 *      phi = (4 pi / lambda) * (R_secondary - R_reference)
 *
 * whose components the ATBD decomposes as (Section 5.11.1):
 *
 *      dphi = (4 pi / lambda) * (dr_d + dr_g + dr_t + dr_i + dr_n)
 *
 * where dr_d is the surface displacement in the line-of-sight direction and
 * the remaining terms are geometric, tropospheric, ionospheric and noise range
 * differences.
 *
 * CORRECTION STATUS OF THE CONSUMED LAYER (verified against the official ASF
 * NISAR GUNW product guide, https://nisar-docs.asf.alaska.edu/gunw/,
 * retrieved 2026-10-21, and confirmed against the sample product's own run
 * configuration):
 *
 *   - The geometric (flattening) term IS removed: the product records
 *     topographicFlatteningApplied / ellipsoidalFlatteningApplied = True.
 *   - The ionospheric phase screen is INCLUDED in the product but NOT
 *     APPLIED to the data layers ("The estimated ionospheric phase screen is
 *     included in the product but not applied to the data layers within the
 *     GUNW product by default" — ASF guide).
 *   - Tropospheric and solid-Earth-tide corrections are supplied as geocoded
 *     lookup tables and are NOT removed from the interferometric data
 *     ("These phase corrections, when available, are not removed from the
 *     interferometric data" — ASF guide).
 *   - The metadata flags parameters/geocoding/*CorrectionApplied record
 *     corrections applied during the GEOCODING (geolocation) step, not phase
 *     corrections applied to the interferogram.
 *
 * CONSEQUENCE (this is the honest reading): applying
 *
 *      d_LOS = lambda * phi / (4 pi)
 *
 * to the stored unwrappedPhase yields the APPARENT RELATIVE range change
 * along the line of sight — the sum of true ground displacement and the
 * UNCORRECTED tropospheric and ionospheric contributions (plus noise). It
 * is verified ground displacement only up to those residuals. MOTUS
 * therefore labels its output "apparent relative LOS range change",
 * quantifies the unresolved ionospheric systematic from the product's own
 * screen statistics (variation of the unapplied screen, and the screen
 * estimate's uncertainty — whichever is larger; see pipeline.ts Stage D),
 * and never describes single-pair results as verified tectonic displacement.
 *
 * SIGN: POSITIVE d_LOS means an INCREASE in radar range, i.e. ground motion
 * AWAY from the satellite; negative d_LOS = motion TOWARD the satellite.
 * The frequently quoted alternative d = -lambda*dphi/(4*pi) refers to the
 * opposite conjugation order; the sign is fully determined by which scene is
 * conjugated when forming the interferogram, and we follow the NISAR SDS
 * convention above.
 */

/** One interferometric fringe = half a wavelength of range change (metres). */
export function fringeDisplacement(wavelength: number): number {
  return wavelength / 2;
}

/**
 * Convert unwrapped interferometric phase to line-of-sight displacement
 * following the NISAR SDS convention (see module docstring).
 *
 * @param phaseRad  unwrapped phase in radians
 * @param wavelength radar wavelength in metres
 * @returns LOS displacement in metres (+ = away from radar)
 */
export function phaseToDisplacement(phaseRad: number, wavelength: number): number {
  return (wavelength * phaseRad) / (4 * Math.PI);
}

/** Inverse of phaseToDisplacement: displacement -> phase (radians). */
export function displacementToPhase(displacementM: number, wavelength: number): number {
  return (4 * Math.PI * displacementM) / wavelength;
}

/**
 * Phase equivalent of one 2-pi ambiguity in displacement units (same as
 * fringeDisplacement; provided under a pedagogical name for the UI).
 */
export function ambiguityPerFringe(wavelength: number): number {
  return wavelength / 2;
}

/**
 * Wrap an arbitrary (unwrapped) phase into (-pi, pi].
 */
export function wrapPhase(phaseRad: number): number {
  const twoPi = 2 * Math.PI;
  let p = phaseRad % twoPi;
  if (p > Math.PI) p -= twoPi;
  if (p <= -Math.PI) p += twoPi;
  return p;
}

/**
 * Coefficient of the phase-unwrapping ambiguity in displacement:
 * an integer 2-pi error of k cycles corresponds to k * lambda/2 metres.
 */
export function unwrapErrorDisplacement(cycles: number, wavelength: number): number {
  return cycles * (wavelength / 2);
}

/**
 * Vectorised phase -> displacement for a Float32Array grid in place-safe form.
 * NaN propagates as NaN.
 */
export function phaseGridToDisplacement(
  phase: Float32Array,
  wavelength: number,
): Float32Array {
  const out = new Float32Array(phase.length);
  const k = wavelength / (4 * Math.PI);
  for (let i = 0; i < phase.length; i++) {
    const p = phase[i];
    out[i] = Number.isNaN(p) ? NaN : k * p;
  }
  return out;
}
