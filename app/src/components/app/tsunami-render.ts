/**
 * Client-side overlay rendering for tsunami result grids (lat/lon grids —
 * simpler than the InSAR Mercator-exact path because tsunami domains are
 * regional and corner-bounded overlays are sub-pixel accurate at this scale).
 *
 * Every overlay rendered here is a MODEL OUTPUT from an actual solver run
 * (or the measured DART residuals in charts) — nothing decorative.
 */

import { b64ToFloat32 } from './decode';

export type TsunamiLayerKind = 'maxEta' | 'arrival' | 'dz' | 'travel' | 'bed';

export interface TsunamiLayerDef {
  key: TsunamiLayerKind;
  label: string;
  plain: string;
  unit: string;
}

export const TSUNAMI_LAYERS: TsunamiLayerDef[] = [
  { key: 'maxEta', label: 'Max wave height', plain: 'the largest water-surface rise or dip the model produced at each place over the whole run', unit: 'm' },
  { key: 'arrival', label: 'Wave arrival time', plain: 'when the modeled water first rose or dipped more than the arrival threshold (5 cm)', unit: 'min' },
  { key: 'dz', label: 'Seafloor shift', plain: 'how much the earthquake lifted or dropped the sea floor at the start — the wave’s cause', unit: 'm' },
  { key: 'travel', label: 'Travel time (ray theory)', plain: 'an independent first-arrival estimate from wave speed √(g·depth) only — no full simulation', unit: 'min' },
  { key: 'bed', label: 'Ocean depth & land', plain: 'the ETOPO1 sea-floor and land elevations the model ran on', unit: 'm' },
];

/** Sequential palette: pale sand → sage → deep teal (no blue/indigo). */
export function amplitudeColor(t: number): [number, number, number] {
  const c = Math.max(0, Math.min(1, t));
  // 3-stop interpolation
  const stops: Array<[number, [number, number, number]]> = [
    [0.0, [247, 242, 233]],
    [0.45, [148, 175, 153]],
    [1.0, [26, 77, 68]],
  ];
  for (let s = 0; s < stops.length - 1; s++) {
    const [p0, c0] = stops[s];
    const [p1, c1] = stops[s + 1];
    if (c <= p1) {
      const f = (c - p0) / (p1 - p0);
      return [
        Math.round(c0[0] + f * (c1[0] - c0[0])),
        Math.round(c0[1] + f * (c1[1] - c0[1])),
        Math.round(c0[2] + f * (c1[2] - c0[2])),
      ];
    }
  }
  return stops[stops.length - 1][1];
}

/** Sequential palette for times: cream → olive → umber. */
export function arrivalColor(t: number): [number, number, number] {
  const c = Math.max(0, Math.min(1, t));
  const stops: Array<[number, [number, number, number]]> = [
    [0.0, [249, 245, 235]],
    [0.5, [176, 164, 106]],
    [1.0, [110, 78, 45]],
  ];
  for (let s = 0; s < stops.length - 1; s++) {
    const [p0, c0] = stops[s];
    const [p1, c1] = stops[s + 1];
    if (c <= p1) {
      const f = (c - p0) / (p1 - p0);
      return [
        Math.round(c0[0] + f * (c1[0] - c0[0])),
        Math.round(c0[1] + f * (c1[1] - c0[1])),
        Math.round(c0[2] + f * (c1[2] - c0[2])),
      ];
    }
  }
  return stops[stops.length - 1][1];
}

/** Diverging brown–sand–sage for seafloor shift (matches the app palette). */
export function dzColor(v: number): [number, number, number] {
  const t = Math.max(-1, Math.min(1, v));
  if (t >= 0) {
    const f = t;
    return [Math.round(234 - f * 106), Math.round(199 - f * 100), Math.round(172 - f * 87)];
  }
  const f = -t;
  return [Math.round(234 - f * 94), Math.round(199 + f * 8), Math.round(172 - f * 66)];
}

/** Bathymetry shading: deep ocean dark teal → shelf pale → land tan. */
export function bedColor(b: number): [number, number, number] {
  if (b > 0) {
    const t = Math.min(1, b / 1500);
    return [Math.round(212 - t * 40), Math.round(204 - t * 52), Math.round(188 - t * 56)];
  }
  const d = Math.min(1, -b / 6000);
  return [
    Math.round(214 - d * 158),
    Math.round(226 - d * 140),
    Math.round(222 - d * 128),
  ];
}

export interface TsunamiGrids {
  nx: number;
  ny: number;
  lat0: number;
  lon0: number;
  resArcmin: number;
  maxEta: Float32Array;
  arrival: Float32Array;
  dz: Float32Array;
  bed: Float32Array;
  travel: Float32Array;
}

export function decodeTsunamiTransport(t: {
  maxEtaB64: string;
  arrivalB64: string;
  dzB64: string;
  bedB64: string;
  travelB64: string;
}, grid: { nx: number; ny: number; lat0: number; lon0: number; resArcmin: number }): TsunamiGrids {
  return {
    ...grid,
    maxEta: b64ToFloat32(t.maxEtaB64),
    arrival: b64ToFloat32(t.arrivalB64),
    dz: b64ToFloat32(t.dzB64),
    bed: b64ToFloat32(t.bedB64),
    travel: b64ToFloat32(t.travelB64),
  };
}

export interface RenderedTsunamiOverlay {
  dataUrl: string;
  /** legend range actually used */
  range: [number, number];
  unit: string;
}

/**
 * Render one grid to a data-URL canvas (RGBA). NaN cells are transparent.
 * `norm` maps a value to [0,1] (or [-1,1] for diverging).
 */
export function renderTsunamiLayer(
  grids: TsunamiGrids,
  layer: TsunamiLayerKind,
  maxCanvasPx = 720,
): RenderedTsunamiOverlay {
  const { nx, ny } = grids;
  const scale = Math.min(1, maxCanvasPx / Math.max(nx, ny));
  const W = Math.max(1, Math.round(nx * scale));
  const H = Math.max(1, Math.round(ny * scale));

  let values: Float32Array;
  let unit = 'm';
  let range: [number, number] = [0, 1];
  let color: (v: number) => [number, number, number];

  switch (layer) {
    case 'maxEta': {
      values = grids.maxEta;
      let mx = 0;
      for (let k = 0; k < values.length; k++) {
        const a = values[k];
        if (Number.isFinite(a) && a > mx) mx = a;
      }
      const cap = Math.max(0.1, mx);
      range = [0, cap];
      color = (v) => amplitudeColor(v / cap);
      break;
    }
    case 'arrival': {
      values = grids.arrival;
      let mx = 0;
      for (let k = 0; k < values.length; k++) {
        const a = values[k];
        if (Number.isFinite(a) && a > mx) mx = a;
      }
      const cap = Math.max(60, mx / 60); // minutes
      range = [0, cap];
      unit = 'min';
      color = (v) => arrivalColor(Number.isFinite(v) ? v / 60 / cap : NaN);
      break;
    }
    case 'dz': {
      values = grids.dz;
      let mx = 0;
      for (let k = 0; k < values.length; k++) {
        const a = Math.abs(values[k]);
        if (Number.isFinite(a) && a > mx) mx = a;
      }
      const cap = Math.max(0.1, mx);
      range = [-cap, cap];
      color = (v) => dzColor(v / cap);
      break;
    }
    case 'travel': {
      values = grids.travel;
      let mx = 0;
      for (let k = 0; k < values.length; k++) {
        const a = values[k];
        if (Number.isFinite(a) && a > mx) mx = a;
      }
      const cap = Math.max(60, mx / 60);
      range = [0, cap];
      unit = 'min';
      color = (v) => arrivalColor(Number.isFinite(v) ? v / 60 / cap : NaN);
      break;
    }
    case 'bed':
    default: {
      values = grids.bed;
      range = [-6000, 1500];
      color = bedColor;
      break;
    }
  }

  const canvas = document.createElement('canvas');
  canvas.width = W;
  canvas.height = H;
  const ctx = canvas.getContext('2d');
  if (!ctx) return { dataUrl: '', range, unit };
  const img = ctx.createImageData(W, H);
  const data = img.data;
  for (let y = 0; y < H; y++) {
    const j = Math.min(ny - 1, Math.floor(y / scale));
    for (let x = 0; x < W; x++) {
      const i = Math.min(nx - 1, Math.floor(x / scale));
      const v = values[j * nx + i];
      const p = (y * W + x) * 4;
      if (layer === 'bed') {
        const [r, g, b] = color(v);
        data[p] = r; data[p + 1] = g; data[p + 2] = b; data[p + 3] = Math.round(215);
      } else if (!Number.isFinite(v)) {
        data[p + 3] = 0;
      } else {
        const [r, g, b] = color(v);
        data[p] = r; data[p + 1] = g; data[p + 2] = b;
        data[p + 3] = 205;
      }
    }
  }
  ctx.putImageData(img, 0, 0);
  return { dataUrl: canvas.toDataURL('image/png'), range, unit };
}

/** Geographic bounds of the grid for Leaflet ImageOverlay. */
export function tsunamiGridBounds(grids: TsunamiGrids): [[number, number], [number, number]] {
  const latMin = grids.lat0 - grids.resArcmin / 120;
  const lonMin = grids.lon0 - grids.resArcmin / 120;
  const latMax = grids.lat0 + ((grids.ny - 1) * grids.resArcmin) / 60 + grids.resArcmin / 120;
  const lonMax = grids.lon0 + ((grids.nx - 1) * grids.resArcmin) / 60 + grids.resArcmin / 120;
  return [
    [latMin, lonMin],
    [latMax, lonMax],
  ];
}
