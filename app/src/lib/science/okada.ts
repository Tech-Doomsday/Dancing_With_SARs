/**
 * Okada (1985) rectangular dislocation in an elastic half-space —
 * surface displacement (z = 0) port of the official DC3D algorithm.
 *
 * Lineage of this implementation:
 *   Y. Okada, "Surface deformation due to shear and tensile faults in a
 *   half-space", Bull. Seismol. Soc. Am. 75(4):1135-1154, 1985.
 *   Y. Okada, "Internal deformation due to shear and tensile faults in a
 *   half-space", Bull. Seismol. Soc. Am. 82(2):1018-1040, 1992 (DC3D code).
 *   -> dc3d.py (Python port of the Fortran DC3D,
 *      https://github.com/satoshi-pes/dc3d.py)
 *   -> this TypeScript port (surface displacement terms only, z = 0).
 *
 * The port is cross-validated against dc3d.py in the test suite
 * (tests/science/okada.test.ts) at multiple points and slip modes.
 *
 * SLIP-SENSE CONVENTIONS (determined empirically against the reference
 * implementation and used for the rake mapping):
 *   - U1 (strike-slip dislocation) POSITIVE = sinistral (left-lateral):
 *     the +y block moves in the -x direction.
 *   - U2 (dip-slip dislocation) POSITIVE = normal-faulting sense:
 *     the hanging wall moves down-dip; subsidence occurs over the +y
 *     (hanging-wall) side.
 *   - U3 (tensile dislocation) POSITIVE = opening (uplift above the fault).
 *
 * With the Aki & Richards rake convention (rake = direction of hanging-wall
 * slip measured from the strike; rake 0 = sinistral, ±180 = dextral,
 * +90 = reverse, -90 = normal), the mapping is:
 *
 *   U1 =  slip * cos(rake)
 *   U2 = -slip * sin(rake)
 *
 * The fault coordinate system: x along strike, y horizontal pointing in the
 * dip direction (the hanging-wall side), z up. DEPTH is the depth of the
 * top-edge midpoint whose surface projection is the coordinate origin; the
 * down-dip extent is expressed via DC3D's aw range (see the friendly
 * wrapper docstring: positive aw = up-dip, so the fault spans aw = [-W, 0]).
 */

/** Elastic half-space alpha = (lambda+mu)/(lambda+2mu); 0.5 => nu=0.25. */
export interface OkadaFault {
  /** Top-edge depth in the same length unit as the coordinates (km or m — be consistent) */
  depth: number;
  /** Dip angle in degrees from horizontal */
  dipDeg: number;
  /** Along-strike extent */
  length: number;
  /** Down-dip extent */
  width: number;
  /** Strike-slip dislocation U1 (positive = sinistral) */
  u1: number;
  /** Dip-slip dislocation U2 (positive = normal sense) */
  u2: number;
  /** Tensile dislocation U3 (positive = opening) */
  u3: number;
}

export interface SurfaceDisplacement {
  ux: number;
  uy: number;
  uz: number;
  /** true when the evaluation point is singular (on/near the fault edge) */
  singular: boolean;
}

const PI2 = 2 * Math.PI;

interface C0 {
  alp1: number;
  alp2: number;
  alp3: number;
  alp4: number;
  alp5: number;
  sd: number;
  cd: number;
  sdsd: number;
  cdcd: number;
  sdcd: number;
}

function dccon0(alpha: number, dipDeg: number): C0 {
  const alp1 = (1 - alpha) / 2;
  const alp2 = alpha / 2;
  const alp3 = (1 - alpha) / alpha;
  const alp4 = 1 - alpha;
  const alp5 = alpha;
  let sd = Math.sin((dipDeg * Math.PI) / 180);
  let cd = Math.cos((dipDeg * Math.PI) / 180);
  if (Math.abs(cd) < 1e-10) {
    cd = 0;
    if (sd > 0) sd = 1;
    else if (sd < 0) sd = -1;
  }
  return {
    alp1, alp2, alp3, alp4, alp5, sd, cd,
    sdsd: sd * sd, cdcd: cd * cd, sdcd: sd * cd,
  };
}

interface C2 {
  xi2: number; et2: number; q2: number;
  r: number; r2: number; r3: number; r5: number;
  y: number; d: number; tt: number;
  alx: number; ale: number;
  x11: number; y11: number; x32: number; y32: number;
  ey: number; ez: number; fy: number; fz: number;
  gy: number; gz: number; hy: number; hz: number;
}

function nearlyZero(a: number): boolean {
  return Math.abs(a) < 1e-5;
}

function dccon2(xiIn: number, etIn: number, qIn: number, sd: number, cd: number, kxi: number, ket: number): C2 | null {
  let xi = nearlyZero(xiIn) ? 0 : xiIn;
  let et = nearlyZero(etIn) ? 0 : etIn;
  let q = nearlyZero(qIn) ? 0 : qIn;

  const xi2 = xi * xi;
  const et2 = et * et;
  const q2 = q * q;
  const r2 = xi2 + et2 + q2;
  const r = Math.sqrt(r2);
  if (nearlyZero(r)) return null;

  const r3 = r * r2;
  const r5 = r3 * r2;
  const y = et * cd + q * sd;
  const d = et * sd - q * cd;

  let tt = 0;
  if (q !== 0) tt = Math.atan((xi * et) / (q * r));

  let alx: number, x11: number, x32: number;
  if (kxi === 1) {
    alx = -Math.log(r - xi);
    x11 = 0;
    x32 = 0;
  } else {
    const rxi = r + xi;
    alx = Math.log(rxi);
    x11 = 1 / (r * rxi);
    x32 = ((r + rxi) * x11 * x11) / r;
  }

  let ale: number, y11: number, y32: number;
  if (ket === 1) {
    ale = -Math.log(r - et);
    y11 = 0;
    y32 = 0;
  } else {
    const ret = r + et;
    ale = Math.log(ret);
    y11 = 1 / (r * ret);
    y32 = ((r + ret) * y11 * y11) / r;
  }

  const ey = sd / r - (y * q) / r3;
  const ez = cd / r + (d * q) / r3;
  const fy = d / r3 + xi2 * y32 * sd;
  const fz = y / r3 + xi2 * y32 * cd;
  const gy = 2 * x11 * sd - y * q * x32;
  const gz = 2 * x11 * cd + d * q * x32;
  const hy = d * q * x32 + xi * q * y32 * sd;
  const hz = y * q * x32 + xi * q * y32 * cd;

  return { xi2, et2, q2, r, r2, r3, r5, y, d, tt, alx, ale, x11, y11, x32, y32, ey, ez, fy, fz, gy, gz, hy, hz };
}

/** Part-a displacement (du[0..2]) — contributions of strike/dip/tensile. */
function uaDisp(xi: number, et: number, q: number, disl1: number, disl2: number, disl3: number, C0: C0, C2: C2): [number, number, number] {
  const { alp1, alp2 } = C0;
  const { y11, x11, r, ale, tt, alx } = C2;

  const qx = q * x11;
  const qy = q * y11;

  let dux = 0, duy = 0, duz = 0;

  if (disl1 !== 0) {
    const a0 = tt / 2 + alp2 * xi * qy;
    const a1 = alp2 * q / r;
    const a2 = alp1 * ale - alp2 * q * qy;
    dux += (disl1 / PI2) * a0;
    duy += (disl1 / PI2) * a1;
    duz += (disl1 / PI2) * a2;
  }
  if (disl2 !== 0) {
    const b0 = alp2 * q / r;
    const b1 = tt / 2 + alp2 * et * qx;
    const b2 = alp1 * alx - alp2 * q * qx;
    dux += (disl2 / PI2) * b0;
    duy += (disl2 / PI2) * b1;
    duz += (disl2 / PI2) * b2;
  }
  if (disl3 !== 0) {
    const c0 = -alp1 * ale - alp2 * q * qy;
    const c1 = -alp1 * alx - alp2 * q * qx;
    const c2 = tt / 2 - alp2 * (et * qx + xi * qy);
    dux += (disl3 / PI2) * c0;
    duy += (disl3 / PI2) * c1;
    duz += (disl3 / PI2) * c2;
  }
  return [dux, duy, duz];
}

/** Part-b displacement (image source) — du[0..2]. */
function ubDisp(xi: number, et: number, q: number, disl1: number, disl2: number, disl3: number, C0: C0, C2: C2): [number, number, number] {
  const { alp3, sd, cd, sdsd, cdcd, sdcd } = C0;
  const { xi2, q2, r, y, d, tt, ale, x11, y11 } = C2;

  const rd = r + d;
  const d11 = 1 / (r * rd);
  const aj2 = (xi * y * d11) / rd;
  const aj5 = -(d + (y * y) / rd) * d11;

  let ai4 = 0;
  let ai3: number;
  let ak1: number;
  let ak3: number;
  let aj3: number;
  let aj6: number;

  if (cd !== 0) {
    if (xi === 0) {
      ai4 = 0;
    } else {
      const x = Math.sqrt(xi2 + q2);
      ai4 =
        (1 / cdcd) *
        ((xi / rd) * sdcd +
          2 * Math.atan((et * (x + q * cd) + x * (r + x) * sd) / (xi * (r + x) * cd)));
    }
    ai3 = ((y * cd) / rd - ale + sd * Math.log(rd)) / cdcd;
    ak1 = (xi * (d11 - y11 * sd)) / cd;
    ak3 = (q * y11 - y * d11) / cd;
    aj3 = (ak1 - aj2 * sd) / cd;
    aj6 = (ak3 - aj5 * sd) / cd;
  } else {
    const rd2 = rd * rd;
    ai3 = (et / rd + (y * q) / rd2 - ale) / 2;
    ai4 = (xi * y) / rd2 / 2;
    ak1 = ((xi * q) / rd) * d11;
    ak3 = (sd / rd) * (xi2 * d11 - 1);
    aj3 = (-xi / rd2) * (q2 * d11 - 1 / 2);
    aj6 = (-y / rd2) * (xi2 * d11 - 1 / 2);
  }

  const ai1 = (-xi / rd) * cd - ai4 * sd;
  const ai2 = Math.log(rd) + ai3 * sd;
  const qx = q * x11;
  const qy = q * y11;

  let dux = 0, duy = 0, duz = 0;

  if (disl1 !== 0) {
    const a0 = -xi * qy - tt - alp3 * ai1 * sd;
    const a1 = -q / r + (alp3 * y * sd) / rd;
    const a2 = q * qy - alp3 * ai2 * sd;
    dux += (disl1 / PI2) * a0;
    duy += (disl1 / PI2) * a1;
    duz += (disl1 / PI2) * a2;
  }
  if (disl2 !== 0) {
    const b0 = -q / r + alp3 * ai3 * sdcd;
    const b1 = -et * qx - tt - (alp3 * xi * sdcd) / rd;
    const b2 = q * qx + alp3 * ai4 * sdcd;
    dux += (disl2 / PI2) * b0;
    duy += (disl2 / PI2) * b1;
    duz += (disl2 / PI2) * b2;
  }
  if (disl3 !== 0) {
    const c0 = q * qy - alp3 * ai3 * sdsd;
    const c1 = q * qx + (alp3 * xi * sdsd) / rd;
    const c2 = et * qx + xi * qy - tt - alp3 * ai4 * sdsd;
    dux += (disl3 / PI2) * c0;
    duy += (disl3 / PI2) * c1;
    duz += (disl3 / PI2) * c2;
  }
  void ak1; void ak3;
  return [dux, duy, duz];
}

/**
 * Raw DC3D-style entry point: surface displacement at fault-coordinate
 * (x, y) for a fault spanning along-strike [al1, al2] and down-dip
 * [aw1, aw2] below DEPTH, with dislocations (u1, u2, u3).
 * alpha = Poisson combination (lambda+mu)/(lambda+2mu); 0.5 => nu = 0.25.
 * Returns displacement in the fault coordinate frame (x, y, up) in the same
 * units as the fault geometry, or singular=true near fault edges.
 * This raw form exists for 1:1 benchmarking against the reference DC3D code.
 */
export function okadaSurfaceDisplacementRaw(
  x: number,
  y: number,
  depth: number,
  dipDeg: number,
  al1: number,
  al2: number,
  aw1: number,
  aw2: number,
  u1: number,
  u2: number,
  u3: number,
  alpha = 0.5,
): SurfaceDisplacement {
  const C0 = dccon0(alpha, dipDeg);
  const sd = C0.sd;
  const cd = C0.cd;

  const xi = [x - al1, x - al2];
  if (nearlyZero(xi[0])) xi[0] = 0;
  if (nearlyZero(xi[1])) xi[1] = 0;

  // At z = 0 the real-source and image-source geometries coincide:
  // d = depth (+/- 0), p = y*cd + d*sd, q = y*sd - d*cd.
  const d = depth;
  const p = y * cd + d * sd;
  const q = y * sd - d * cd;
  const et = [p - aw1, p - aw2];
  if (nearlyZero(et[0])) et[0] = 0;
  if (nearlyZero(et[1])) et[1] = 0;

  // Reject singular points on the fault edge (DC3D IRET=1 semantics).
  if (
    q === 0 &&
    ((xi[0] * xi[1] <= 0 && et[0] * et[1] === 0) ||
      (et[0] * et[1] <= 0 && xi[0] * xi[1] === 0))
  ) {
    return { ux: NaN, uy: NaN, uz: NaN, singular: true };
  }

  const r12 = Math.sqrt(xi[0] * xi[0] + et[1] * et[1] + q * q);
  const r21 = Math.sqrt(xi[1] * xi[1] + et[0] * et[0] + q * q);
  const r22 = Math.sqrt(xi[1] * xi[1] + et[1] * et[1] + q * q);
  const kxi = [0, 0];
  const ket = [0, 0];
  if (xi[0] < 0 && r21 + xi[1] < 1e-6) kxi[0] = 1;
  if (xi[0] < 0 && r22 + xi[1] < 1e-6) kxi[1] = 1;
  if (et[0] < 0 && r12 + et[1] < 1e-6) ket[0] = 1;
  if (et[0] < 0 && r22 + et[1] < 1e-6) ket[1] = 1;

  let ux = 0, uy = 0, uz = 0;
  let singular = false;

  for (let k = 0; k < 2; k++) {
    for (let j = 0; j < 2; j++) {
      const C2 = dccon2(xi[j], et[k], q, sd, cd, kxi[k], ket[j]);
      if (!C2) {
        singular = true;
        continue;
      }
      const [uaX, uaY, uaZ] = uaDisp(xi[j], et[k], q, u1, u2, u3, C0, C2);
      const [ubX, ubY, ubZ] = ubDisp(xi[j], et[k], q, u1, u2, u3, C0, C2);

      // Real-source contribution (rotation with sign flips).
      const rX = -uaX;
      const rY = -uaY * cd + uaZ * sd;
      const rZ = -uaY * sd - uaZ * cd;

      // Image-source contribution.
      const iX = uaX + ubX;
      const iY = (uaY + ubY) * cd - (uaZ + ubZ) * sd;
      const iZ = (uaY + ubY) * sd + (uaZ + ubZ) * cd;

      const sgn = j + k === 1 ? -1 : 1;
      ux += sgn * (rX + iX);
      uy += sgn * (rY + iY);
      uz += sgn * (rZ + iZ);
    }
  }
  return { ux, uy, uz, singular };
}

/**
 * Friendly wrapper: surface displacement for a fault whose TOP EDGE lies at
 * `fault.depth`, extending `fault.width` DOWN-DIP.
 *
 * DC3D aw convention (verified empirically against the reference code):
 * positive aw extends UP-DIP from the reference point; therefore a fault
 * with its top edge at DEPTH and width W spanning down-dip corresponds to
 * aw = [-W, 0] with DEPTH = top-edge depth. (Using aw = [0, +W] would place
 * the fault ABOVE the reference depth — up into the air — producing
 * unphysical surface fields; this was caught by analytic calibration.)
 */
export function okadaSurfaceDisplacement(
  x: number,
  y: number,
  fault: OkadaFault,
  alpha = 0.5,
): SurfaceDisplacement {
  return okadaSurfaceDisplacementRaw(
    x, y,
    fault.depth, fault.dipDeg,
    -fault.length / 2, fault.length / 2,
    -fault.width, 0,
    fault.u1, fault.u2, fault.u3,
    alpha,
  );
}

/** Geographic fault specification for forward modelling. */
export interface GeoFault {
  /** Strike azimuth in degrees clockwise from north */
  strikeDeg: number;
  /** Dip in degrees */
  dipDeg: number;
  /** Rake in degrees (Aki & Richards: 0=sinistral, ±180=dextral, +90=reverse, -90=normal) */
  rakeDeg: number;
  /** Rupture length (m) */
  lengthM: number;
  /** Down-dip width (m) */
  widthM: number;
  /** Depth of the top edge (m) */
  topDepthM: number;
  /** Slip magnitude (m) */
  slipM: number;
  /** Latitude/longitude of the rupture centroid projection */
  centroidLat: number;
  centroidLon: number;
}

/** Dislocation components from rake/slip (see module docstring conventions). */
export function rakeToDislocations(slipM: number, rakeDeg: number): { u1: number; u2: number; u3: number } {
  const r = (rakeDeg * Math.PI) / 180;
  return {
    u1: slipM * Math.cos(r),
    u2: -slipM * Math.sin(r),
    u3: 0,
  };
}

/**
 * Surface displacement (ENU, metres) at a point given in local east/north
 * offsets (metres) from the fault centroid's surface projection.
 *
 * The fault is placed with its along-dip centroid at the given centroid
 * location: the top-edge midpoint sits updip of the centroid by
 * (width/2)*cos(dip) horizontally, and topDepth = centroidDepth - (width/2)*sin(dip).
 */
export function geoFaultDisplacementENU(
  fault: GeoFault,
  centroidDepthM: number,
  eastM: number,
  northM: number,
): { east: number; north: number; up: number; singular: boolean } {
  const { u1, u2, u3 } = rakeToDislocations(fault.slipM, fault.rakeDeg);
  const strike = (fault.strikeDeg * Math.PI) / 180;

  // Local fault-frame unit vectors.
  // along-strike: (sin s, cos s); dip direction: (cos s, -sin s)
  const sx = Math.sin(strike);
  const sy = Math.cos(strike);
  const dx = Math.cos(strike);
  const dy = -Math.sin(strike);

  // Position of the top-edge midpoint relative to the centroid projection.
  const halfWidth = fault.widthM / 2;
  const centroidOffsetDip = halfWidth * Math.cos((fault.dipDeg * Math.PI) / 180); // centroid is downdip of top edge
  const topDepthM = Math.max(500, centroidDepthM - halfWidth * Math.sin((fault.dipDeg * Math.PI) / 180));

  // Observation point in fault coordinates relative to the top-edge midpoint.
  const relE = eastM + centroidOffsetDip * dx;
  const relN = northM + centroidOffsetDip * dy;
  const fx = relE * sx + relN * sy;
  const fy = relE * dx + relN * dy;

  const res = okadaSurfaceDisplacement(fx, fy, {
    depth: topDepthM,
    dipDeg: fault.dipDeg,
    length: fault.lengthM,
    width: fault.widthM,
    u1, u2, u3,
  });

  // Rotate displacement back to ENU.
  return {
    east: res.ux * sx + res.uy * dx,
    north: res.ux * sy + res.uy * dy,
    up: res.uz,
    singular: res.singular,
  };
}

/**
 * Top-edge variant for tsunami source construction (2026-10-24, additive):
 * surface displacement (ENU, metres) at a point given in local east/north
 * offsets (metres) from the surface projection of the fault's TOP-EDGE
 * midpoint, with the top edge at exactly `topEdgeDepthM`.
 *
 * Used by the tsunami finite-fault reader (USGS FFM.geojson subfaults carry
 * top-edge corner depths), where the centroid bookkeeping of
 * geoFaultDisplacementENU would be circular. Same conventions otherwise.
 */
export function geoFaultDisplacementENUTopEdge(
  fault: GeoFault,
  topEdgeDepthM: number,
  eastM: number,
  northM: number,
): { east: number; north: number; up: number; singular: boolean } {
  const { u1, u2, u3 } = rakeToDislocations(fault.slipM, fault.rakeDeg);
  const strike = (fault.strikeDeg * Math.PI) / 180;

  const sx = Math.sin(strike);
  const sy = Math.cos(strike);
  const dx = Math.cos(strike);
  const dy = -Math.sin(strike);

  const fx = eastM * sx + northM * sy;
  const fy = eastM * dx + northM * dy;

  const res = okadaSurfaceDisplacement(fx, fy, {
    depth: Math.max(0, topEdgeDepthM),
    dipDeg: fault.dipDeg,
    length: fault.lengthM,
    width: fault.widthM,
    u1, u2, u3,
  });

  return {
    east: res.ux * sx + res.uy * dx,
    north: res.ux * sy + res.uy * dy,
    up: res.uz,
    singular: res.singular,
  };
}

/**
 * Seismic moment from moment magnitude (Hanks & Kanamori 1979):
 * M0 = 10^(1.5*Mw + 9.1) N*m.
 */
export function momentFromMw(mw: number): number {
  return Math.pow(10, 1.5 * mw + 9.1);
}

/**
 * Empirical subsurface rupture-area scaling (Wells & Coppersmith 1994,
 * all slip types): log10(A / km^2) = 0.91*Mw - 3.49.
 * Returns the area in m^2. Scatter of the underlying regression is
 * ~0.25 orders of magnitude (factor ~1.8); treat as order-of-magnitude only.
 */
export function ruptureAreaFromMw(mw: number): number {
  return Math.pow(10, 0.91 * mw - 3.49) * 1e6;
}

/** Uniform slip implied by a seismic moment over a rupture area. */
export function slipFromMoment(momentNM: number, areaM2: number, shearModulus = 3.0e10): number {
  return momentNM / (shearModulus * areaM2);
}
