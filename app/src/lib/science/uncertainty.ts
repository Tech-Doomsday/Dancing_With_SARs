/**
 * Coherence-based interferometric phase uncertainty.
 *
 * For a multilooked interferogram with L independent looks and coherence
 * magnitude gamma, the standard deviation of the interferometric phase is
 * commonly estimated as
 *
 *     sigma_phi = sqrt( (1 - gamma^2) / (2 * L * gamma^2) )
 *
 * This is the classical expression derived as the Cramer-Rao-like bound for
 * the phase of a complex normal process (Rodriguez & Martin, 1992, "Theory
 * and design of interferometric synthetic aperture radars", IEE Proc.-F
 * 139(2):147-159, Eq. 15; see also Hanssen, 2001, "Radar Interferometry",
 * Kluwer, Section 4.2.2, and the exact expression in Tough et al. 1995).
 * It is accurate for gamma >~ 0.3-0.4 and degrades (underestimates spread)
 * at low coherence where the phase distribution deviates strongly from
 * Gaussian — documented as a limitation; pixels below the coherence
 * threshold are masked in any case.
 *
 * The displacement sigma follows by error propagation of
 * d = lambda * phi / (4 pi):
 *
 *     sigma_d = lambda * sigma_phi / (4 pi)
 */

/** Phase standard deviation (radians) from coherence and look count. */
export function phaseSigmaRad(coherence: number, looks: number): number {
  const g = Math.min(Math.max(coherence, 0), 1);
  if (looks < 1) return NaN;
  if (g <= 0) return NaN;
  if (g >= 1) return 0;
  return Math.sqrt((1 - g * g) / (2 * looks * g * g));
}

/** Displacement one-sigma (metres) from coherence, looks and wavelength. */
export function displacementSigmaM(
  coherence: number,
  looks: number,
  wavelength: number,
): number {
  const sp = phaseSigmaRad(coherence, looks);
  if (Number.isNaN(sp)) return NaN;
  return (wavelength * sp) / (4 * Math.PI);
}

/**
 * Combined per-pixel sigma including a spatially constant reference-sigma
 * term (from the reference region scatter) and an optional ionospheric
 * screen uncertainty term (radians -> metres). All terms independent:
 * sigma_total = sqrt(sigma_pixel^2 + sigma_ref^2 + sigma_iono^2).
 */
export function combinedSigmaM(
  coherence: number,
  looks: number,
  wavelength: number,
  referenceSigmaM: number,
  ionoScreenSigmaRad?: number,
): number {
  const sPix = displacementSigmaM(coherence, looks, wavelength);
  const sIono =
    ionoScreenSigmaRad && ionoScreenSigmaRad > 0
      ? (wavelength * ionoScreenSigmaRad) / (4 * Math.PI)
      : 0;
  if (Number.isNaN(sPix)) return NaN;
  return Math.sqrt(sPix * sPix + referenceSigmaM * referenceSigmaM + sIono * sIono);
}

/**
 * Detection threshold for a pixel: |d| must exceed BOTH the physical floor
 * and k * sigma_total to count as detected change.
 */
export function detectionThresholdM(
  sigmaTotalM: number,
  k: number,
  physicalFloorM: number,
): number {
  return Math.max(k * sigmaTotalM, physicalFloorM);
}
