/**
 * Line-of-sight (LOS) unit-vector geometry.
 *
 * The radar measures the projection of the surface displacement vector onto
 * the line-of-sight direction between the satellite and the ground target.
 *
 * For a right-looking satellite with heading angle H (degrees clockwise from
 * north, i.e. the direction of the ground-track velocity) and incidence angle
 * theta (degrees from vertical at the target), the ground-to-satellite unit
 * vector in local East-North-Up (ENU) components is
 *
 *   look_azimuth = heading + 90 deg  (clockwise from north, right-looking)
 *
 *   û_gs = ( sin(theta) sin(look_azimuth), sin(theta) cos(look_azimuth), cos(theta) )
 *
 * Example (verified against the NISAR sample GUNW granule, ascending pass,
 * heading ≈ -12.5 deg, incidence 24.2 deg): û_gs ≈ (+0.400 E, +0.089 N, +0.912 U).
 * The sample product stores losUnitVectorX = -0.4005, losUnitVectorY = -0.0837,
 * i.e. the NISAR radarGrid stores the SATELLITE-TO-GROUND direction
 * (negative of û_gs). The relationship is asserted in unit tests.
 *
 * Range-change sign convention: displacement "away from the satellite"
 * (range increase) is POSITIVE, matching the phase convention of
 * phase.ts (phi = (4 pi / lambda) * dR).
 */

export interface LosUnitVectorENU {
  east: number;
  north: number;
  up: number;
}

/** Look azimuth (degrees clockwise from north) for a look side + heading. */
export function lookAzimuthDeg(
  headingDeg: number,
  lookDirection: 'right' | 'left',
): number {
  return lookDirection === 'right' ? headingDeg + 90 : headingDeg - 90;
}

/** LOS unit vector (ground -> satellite) in local ENU from geometry. */
export function losUnitVector(
  incidenceAngleDeg: number,
  headingDeg: number,
  lookDirection: 'right' | 'left' = 'right',
): LosUnitVectorENU {
  const theta = (incidenceAngleDeg * Math.PI) / 180;
  const az = (lookAzimuthDeg(headingDeg, lookDirection) * Math.PI) / 180;
  const st = Math.sin(theta);
  return {
    east: st * Math.sin(az),
    north: st * Math.cos(az),
    up: Math.cos(theta),
  };
}

/**
 * Project a 3-component displacement vector (ENU) onto the LOS direction.
 * Returns metres with sign + = away from satellite (range increase),
 * i.e. the NEGATIVE of the projection onto the ground->satellite unit vector.
 */
export function projectDisplacementToLOS(
  displacementENU: { east: number; north: number; up: number },
  los: LosUnitVectorENU,
): number {
  const toward =
    displacementENU.east * los.east +
    displacementENU.north * los.north +
    displacementENU.up * los.up;
  // positive projection = motion toward the satellite = range decrease
  return -toward;
}

/**
 * Heading angle of a sun-synchronous ascending/descending pass for display.
 * Typical values: ascending ~ -12 deg (mod 360: 348), descending ~ -168 (192).
 * These are nominal; actual headings vary with latitude.
 */
export const NOMINAL_HEADING: Record<'ascending' | 'descending', number> = {
  ascending: -12.5,
  descending: -167.5,
};

/**
 * Compare a reconstructed ground-to-satellite vector against the
 * satellite-to-ground components stored in NISAR radarGrid datasets
 * (losUnitVectorX = E component, losUnitVectorY = N component, both
 * pointing from satellite toward ground). Returns the max absolute
 * component difference.
 */
export function compareWithNisarProductVector(
  incidenceAngleDeg: number,
  headingDeg: number,
  productX: number,
  productY: number,
  lookDirection: 'right' | 'left' = 'right',
): number {
  const v = losUnitVector(incidenceAngleDeg, headingDeg, lookDirection);
  return Math.max(Math.abs(v.east + productX), Math.abs(v.north + productY));
}
