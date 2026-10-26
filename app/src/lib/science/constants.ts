/**
 * Physical constants and radar band table.
 *
 * Sources:
 *  - Speed of light (exact, SI): BIPM 2019 SI brochure.
 *  - NISAR L-SAR center frequency: NISAR L2 GUNW sample product
 *    centerFrequency = 1269999750.0604727 Hz (λ = 0.2360571 m).
 */

/** Speed of light in vacuum (m/s, exact by SI definition). */
export const C_LIGHT = 299792458.0;

/** Nominal NISAR L-SAR wavelength derived from the product center frequency. */
export const NISAR_L_BAND_WAVELENGTH = 0.2360571;

/** Reference radar wavelengths by band (metres). */
export const RADAR_BANDS: Record<string, { wavelength: number; note: string }> = {
  L: { wavelength: 0.2360571, note: 'L band — NISAR L-SAR / ALOS PALSAR (1.27 GHz)' },
  C: { wavelength: 0.0554658, note: 'C band — Sentinel-1 (5.405 GHz)' },
};

/** Default shear modulus used for moment/slip conversion (Pa).
 * Standard crustal value; Okada half-space default mu = 3e10 Pa with
 * lambda = mu (Poisson solid), consistent with alpha = 0.5 used in the
 * DC3D benchmarks (Okada 1992). */
export const CRUSTAL_SHEAR_MODULUS = 3.0e10;

/** Okada half-space elastic constants: alpha = (lambda+mu)/(lambda+2mu). */
export const OKADA_ALPHA_POISSON = 0.5;

/** Seconds per day. */
export const SECONDS_PER_DAY = 86400;

/** Mean Earth radius (m) — WGS84 mean radius IUGG R1. */
export const EARTH_RADIUS_M = 6371008.8;
