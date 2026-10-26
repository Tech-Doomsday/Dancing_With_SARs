/**
 * WGS84 <-> UTM conversion (Snyder 1987 transverse Mercator series).
 *
 * Reference: J. P. Snyder (1987), "Map Projections — A Working Manual",
 * U.S. Geological Survey Professional Paper 1395, Chapter 8 (equations 8-9
 * through 8-25). Accuracy at the metre level within a zone, which is far
 * below the ~80 m posting of the products we visualise; used for map
 * alignment and footprint construction, not for precision geodesy.
 */

const A = 6378137.0; // WGS84 semi-major axis (m)
const F = 1 / 298.257223563; // WGS84 flattening
const K0 = 0.9996; // UTM scale factor on central meridian
const E2 = 2 * F - F * F; // first eccentricity squared
const EP2 = E2 / (1 - E2); // second eccentricity squared

export interface UtmCoord {
  easting: number;
  northing: number;
  zone: number;
  hemisphere: 'N' | 'S';
}

/** UTM zone number for a longitude (degrees). */
export function utmZone(lonDeg: number): number {
  const lon = ((lonDeg + 180) % 360 + 360) % 360 - 180; // normalise
  return Math.floor((lon + 180) / 6) + 1;
}

/** WGS84 latitude/longitude (degrees) -> UTM easting/northing (metres). */
export function latLonToUtm(latDeg: number, lonDeg: number): UtmCoord {
  return latLonToUtmInZone(latDeg, lonDeg, utmZone(lonDeg));
}

/**
 * WGS84 lat/lon -> UTM in an EXPLICIT zone (not the auto zone of the longitude).
 *
 * Needed whenever two geographic points must be compared in one common
 * projection — e.g. projecting an earthquake epicentre onto a raster whose
 * EPSG fixes the zone. Points up to a few hundred kilometres outside the
 * zone remain accurate at the metre level (the transverse Mercator series
 * degrades gracefully); beyond that the caller should reproject properly.
 */
export function latLonToUtmInZone(latDeg: number, lonDeg: number, zone: number): UtmCoord {
  if (!Number.isInteger(zone) || zone < 1 || zone > 60) {
    throw new Error(`Invalid UTM zone: ${zone}`);
  }
  const lat = (latDeg * Math.PI) / 180;
  const lon = (lonDeg * Math.PI) / 180;
  const lon0 = ((zone - 1) * 6 - 180 + 3) * (Math.PI / 180); // central meridian

  const sinLat = Math.sin(lat);
  const cosLat = Math.cos(lat);
  const tanLat = Math.tan(lat);

  const N = A / Math.sqrt(1 - E2 * sinLat * sinLat);
  const T = tanLat * tanLat;
  const C = EP2 * cosLat * cosLat;
  const Aa = cosLat * (lon - lon0);

  const M =
    A *
    ((1 - E2 / 4 - (3 * E2 * E2) / 64 - (5 * E2 ** 3) / 256) * lat -
      ((3 * E2) / 8 + (3 * E2 * E2) / 32 + (45 * E2 ** 3) / 1024) * Math.sin(2 * lat) +
      ((15 * E2 * E2) / 256 + (45 * E2 ** 3) / 1024) * Math.sin(4 * lat) -
      ((35 * E2 ** 3) / 3072) * Math.sin(6 * lat));

  const easting =
    K0 *
      N *
      (Aa +
        ((1 - T + C) * Aa ** 3) / 6 +
        ((5 - 18 * T + T * T + 72 * C - 58 * EP2) * Aa ** 5) / 120) +
    500000;

  const northing =
    K0 *
    (M +
      N *
        tanLat *
        (Aa * Aa / 2 +
          ((5 - T + 9 * C + 4 * C * C) * Aa ** 4) / 24 +
          ((61 - 58 * T + T * T + 600 * C - 330 * EP2) * Aa ** 6) / 720));

  return {
    easting,
    northing: latDeg < 0 ? northing + 10000000 : northing,
    zone,
    hemisphere: latDeg < 0 ? 'S' : 'N',
  };
}

/** EPSG code for a WGS84 UTM zone (northern/southern). */
export function utmZoneToEpsg(zone: number, hemisphere: 'N' | 'S'): number {
  return hemisphere === 'N' ? 32600 + zone : 32700 + zone;
}

/** Inverse of utmZoneToEpsg: parse a WGS84 UTM EPSG code. */
export function zoneFromEpsg(epsg: number): { zone: number; hemisphere: 'N' | 'S' } | undefined {
  if (epsg >= 32601 && epsg <= 32660) return { zone: epsg - 32600, hemisphere: 'N' };
  if (epsg >= 32701 && epsg <= 32760) return { zone: epsg - 32700, hemisphere: 'S' };
  return undefined;
}

/** EPSG code for a WGS84 latitude/longitude. */
export function epsgForLatLon(latDeg: number, lonDeg: number): number {
  const zone = utmZone(lonDeg);
  return utmZoneToEpsg(zone, latDeg < 0 ? 'S' : 'N');
}

const M1 = (1 - E2 / 4 - (3 * E2 * E2) / 64 - (5 * E2 ** 3) / 256);

/** Inverse: UTM (metres, zone) -> WGS84 lat/lon (degrees). Snyder eq. 8-17..8-25. */
export function utmToLatLon(
  easting: number,
  northing: number,
  zone: number,
  southern: boolean,
): { lat: number; lon: number } {
  const x = easting - 500000;
  const y = southern ? northing - 10000000 : northing;
  const M = y / K0;
  const mu = M / (A * M1);

  const e1 = (1 - Math.sqrt(1 - E2)) / (1 + Math.sqrt(1 - E2));

  const phi1 =
    mu +
    ((3 * e1) / 2 - (27 * e1 ** 3) / 32) * Math.sin(2 * mu) +
    ((21 * e1 * e1) / 16 - (55 * e1 ** 4) / 32) * Math.sin(4 * mu) +
    ((151 * e1 ** 3) / 96) * Math.sin(6 * mu) +
    ((1097 * e1 ** 4) / 512) * Math.sin(8 * mu);

  const sinP1 = Math.sin(phi1);
  const tanP1 = Math.tan(phi1);
  const cosP1 = Math.cos(phi1);
  const C1 = EP2 * cosP1 * cosP1;
  const T1 = tanP1 * tanP1;
  const N1 = A / Math.sqrt(1 - E2 * sinP1 * sinP1);
  const Dd = x / (K0 * N1);
  const R1 = (A * (1 - E2)) / Math.pow(1 - E2 * sinP1 * sinP1, 1.5);

  const lat =
    phi1 -
    ((N1 * tanP1) / R1) *
      (Dd * Dd / 2 -
        ((5 + 3 * T1 + 10 * C1 - 4 * C1 * C1 - 9 * EP2) * Dd ** 4) / 24 +
        ((61 + 90 * T1 + 298 * C1 + 45 * T1 * T1 - 252 * EP2 - 3 * C1 * C1) * Dd ** 6) / 720);

  const lon =
    ((zone - 1) * 6 - 180 + 3) * (Math.PI / 180) +
    (Dd - ((1 + 2 * T1 + C1) * Dd ** 3) / 6 + ((5 - 2 * C1 + 28 * T1 - 3 * C1 * C1 + 8 * EP2 + 24 * T1 * T1) * Dd ** 5) / 120) /
      cosP1;

  return { lat: (lat * 180) / Math.PI, lon: (lon * 180) / Math.PI };
}

/** Convenience: inverse projection given an EPSG UTM code (326xx/327xx). */
export function epsgToLatLon(
  easting: number,
  northing: number,
  epsg: number,
): { lat: number; lon: number } {
  if (epsg >= 32601 && epsg <= 32660) {
    return utmToLatLon(easting, northing, epsg - 32600, false);
  }
  if (epsg >= 32701 && epsg <= 32760) {
    return utmToLatLon(easting, northing, epsg - 32700, true);
  }
  throw new Error(`Unsupported EPSG code for UTM inversion: ${epsg}`);
}
