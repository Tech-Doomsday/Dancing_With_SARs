/** Geometry only (no science): a square box of a given side length centred on a point.
 *  1° of latitude = 111.32 km; 1° of longitude shrinks with cos(latitude). The result is a
 *  request window for the engine's own tiling/search — the engine still decides what it covers. */
export type Box = [west: number, south: number, east: number, north: number];

const KM_PER_DEG = 111.32;
export const RANGE_MIN_KM = 1;
export const RANGE_MAX_KM = 200;

export function boxFromCenter(lat: number, lon: number, rangeKm: number): Box | null {
  if (![lat, lon, rangeKm].every(Number.isFinite)) return null;
  if (Math.abs(lat) > 84 || Math.abs(lon) > 180) return null;
  if (rangeKm < RANGE_MIN_KM || rangeKm > RANGE_MAX_KM) return null;
  const half = rangeKm / 2;
  const dLat = half / KM_PER_DEG;
  const dLon = half / (KM_PER_DEG * Math.cos((lat * Math.PI) / 180));
  const r = (v: number) => Number(v.toFixed(4));
  const south = lat - dLat, north = lat + dLat;
  if (south < -90 || north > 90 || lon - dLon < -180 || lon + dLon > 180) return null;   // no wrap-around boxes
  return [r(lon - dLon), r(south), r(lon + dLon), r(north)];
}

/** The inverse, for showing a box that came from elsewhere (a tile, a link): centre and N-S height in km. */
export function centerOfBox(b: Box): { lat: number; lon: number; rangeKm: number } {
  return { lat: (b[1] + b[3]) / 2, lon: (b[0] + b[2]) / 2, rangeKm: (b[3] - b[1]) * KM_PER_DEG };
}
