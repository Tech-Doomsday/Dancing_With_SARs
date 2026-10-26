/**
 * GNSS cross-validation protocol (plan §9 datasets, §11 validation).
 *
 * The plan benchmarks InSAR line-of-sight velocity estimates against
 * continuous 3D GNSS velocities from the Nevada Geodetic Lab network:
 *
 *   RMSE = sqrt( (1/N) Σ (v_LOS,InSAR(x_i) − v_LOS,GNSS,i)² )
 *
 * with the GNSS velocity vector projected into the NISAR line-of-sight
 * geometry (plan §6 LOS projection):
 *
 *   v_LOS,GNSS = −[ v_E·sin θ sin α + v_N·sin θ cos α + v_U·cos θ ]
 *
 * (sign convention: + = away from the satellite = range increase, matching
 * the InSAR velocity field).
 *
 * DATA HONESTY: the live NGL endpoint (geodesy.unr.edu) was unreachable from
 * this deployment at build time (connection timeout), so the Central Valley
 * validation network is a FIXTURE: station positions sit at documented
 * Central Valley localities, and velocity values are consistent with
 * published InSAR/GNSS subsidence rates for those zones (order-of-magnitude
 * faithful, e.g. USGS SIR 2016-5076 for Corcoran/Hanford/Fresno). The
 * fixture exists to exercise the real projection + RMSE machinery
 * end-to-end on the synthetic demonstration stack; it is labelled as such
 * everywhere it is displayed, and the loader attempts the live endpoint
 * first and reports its availability honestly.
 */

import type { GnssLosComparison, GnssStation, GnssValidation, RadarGeometry } from './types';
import { losUnitVector, projectDisplacementToLOS } from './los-projection';
import { haversineKm } from './attribution';

/**
 * Project a GNSS 3D velocity (mm/yr ENU) into the radar LOS.
 * Returns mm/yr with + = away from the satellite (range increase).
 */
export function gnssLosVelocityMmPerYr(
  station: Pick<GnssStation, 'eastMmPerYr' | 'northMmPerYr' | 'upMmPerYr'>,
  geometry: RadarGeometry,
): number {
  const los = losUnitVector(geometry.incidenceAngleDeg, geometry.headingDeg, geometry.lookDirection);
  return projectDisplacementToLOS(
    {
      east: station.eastMmPerYr / 1000,
      north: station.northMmPerYr / 1000,
      up: station.upMmPerYr / 1000,
    },
    los,
  ) * 1000;
}

/**
 * Full validation of an InSAR LOS velocity map against the GNSS network
 * (plan §11 protocol): per-station nearest-valid-pixel sampling with the
 * distance recorded, then RMSE and bias.
 */
export function validateAgainstGnss(
  velocityMPerYr: Float32Array,
  meta: { xStart: number; xSpacing: number; yStart: number; ySpacing: number; width: number; height: number; epsg: number },
  validMask: Uint8Array,
  geometry: RadarGeometry,
  stations: GnssStation[],
  /** Project lat/lon → grid coordinates (UTM easting/northing), throws on unsupported CRS. */
  project: (lat: number, lon: number) => { x: number; y: number },
): GnssValidation {
  const comparisons: GnssLosComparison[] = [];
  for (const st of stations) {
    const { x, y } = project(st.lat, st.lon);
    const j = Math.round((x - meta.xStart) / meta.xSpacing);
    const i = Math.round((meta.yStart - y) / -meta.ySpacing);
    if (i < 0 || i >= meta.height || j < 0 || j >= meta.width) continue;
    // Nearest valid pixel within a small search radius.
    let bestI = -1;
    let bestJ = -1;
    let bestD = Infinity;
    const R = 6;
    for (let di = -R; di <= R; di++) {
      for (let dj = -R; dj <= R; dj++) {
        const ii = i + di;
        const jj = j + dj;
        if (ii < 0 || ii >= meta.height || jj < 0 || jj >= meta.width) continue;
        const idx = ii * meta.width + jj;
        if (!validMask[idx]) continue;
        const d = di * di + dj * dj;
        if (d < bestD) {
          bestD = d;
          bestI = ii;
          bestJ = jj;
        }
      }
    }
    if (bestI < 0) continue; // station outside coverage
    const idx = bestI * meta.width + bestJ;
    const insar = velocityMPerYr[idx] * 1000; // mm/yr
    if (!Number.isFinite(insar)) continue;
    const gnss = gnssLosVelocityMmPerYr(st, geometry);
    comparisons.push({
      stationId: st.id,
      lat: st.lat,
      lon: st.lon,
      gnssLosMmPerYr: gnss,
      insarLosMmPerYr: insar,
      diffMmPerYr: gnss - insar,
      distanceKm: haversineKm(
        st.lat, st.lon,
        st.lat + (bestI - i) * Math.abs(meta.ySpacing) / 111320,
        st.lon + (bestJ - j) * meta.xSpacing / (111320 * Math.cos((st.lat * Math.PI) / 180)),
      ),
    });
  }

  let rmse = NaN;
  let bias = NaN;
  if (comparisons.length > 0) {
    let s2 = 0;
    let s = 0;
    for (const c of comparisons) {
      s2 += c.diffMmPerYr ** 2;
      s += c.diffMmPerYr;
    }
    rmse = Math.sqrt(s2 / comparisons.length);
    bias = -s / comparisons.length; // InSAR − GNSS
  }

  return {
    stations: comparisons,
    rmseMmPerYr: rmse,
    biasMmPerYr: bias,
    n: comparisons.length,
    note:
      'InSAR LOS velocity sampled at each station (nearest valid pixel within ±6 px) vs the GNSS 3D ' +
      'velocity projected into the same LOS geometry; RMSE and bias per the plan §11 protocol. ' +
      'Station velocities are honestly-labelled fixtures (see station sources): the live NGL endpoint ' +
      'was unreachable from this deployment, and the demonstration stack is itself synthetic — this ' +
      'exercises the validation machinery, it is not a claim about the real Earth.',
  };
}
