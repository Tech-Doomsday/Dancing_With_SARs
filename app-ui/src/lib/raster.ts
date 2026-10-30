/** ONE colour implementation for evidence rasters: decode + validate + colorize + legend stops.
 *  The overlay and the legend both come from here (never two code paths).
 *  Zero science: dtype/shape/bounds transport checks only, no interpretation of values.
 *
 *  S1 spike finding (maplibre 5.23/5.24): setting `raster-resampling: "nearest"` on an IMAGE
 *  source renders the layer BLACK. Workaround used here: the colorized canvas is pre-upscaled
 *  with imageSmoothingEnabled = false (nearest-neighbour in 2D), and the map layer keeps the
 *  DEFAULT linear resampling — visually hard pixels, scientifically honest (each grid cell
 *  displays as a block, no invented in-between values). */
import type { LayerPayload } from "./types";
import { TOKENS, fallbackCategory, toneColor } from "./status";

export type DecodeResult =
  | { ok: true; data: Float32Array | Uint8Array }
  | { ok: false; reason: string };

export function validateLayerPayload(layer: unknown): string[] {
  const problems: string[] = [];
  if (!layer || typeof layer !== "object") return ["layer payload is not an object"];
  const p = layer as Partial<LayerPayload> & Record<string, unknown>;
  for (const key of ("name dtype shape data_b64 bounds_lonlat".split(" "))) {
    if (p[key] === undefined || p[key] === null) problems.push(`missing field: ${key}`);
  }
  if (problems.length) return problems;
  if (p.dtype !== "<f4" && p.dtype !== "<u1") problems.push(`dtype ${String(p.dtype)} is not <f4 or <u1`);
  if (!Array.isArray(p.shape) || p.shape.length !== 2 || !p.shape.every((v) => Number.isInteger(v) && v > 0)) {
    problems.push(`shape must be [h, w] of positive integers, got ${JSON.stringify(p.shape)}`);
  } else {
    const [h, w] = p.shape as number[];
    const itemsize = p.dtype === "<u1" ? 1 : 4;
    try {
      const raw = atob(String(p.data_b64));
      if (raw.length !== h * w * itemsize) {
        problems.push(`decoded data_b64 is ${raw.length} bytes; shape*itemsize is ${h * w * itemsize}`);
      }
    } catch {
      problems.push("data_b64 is not valid base64");
    }
  }
  const b = p.bounds_lonlat;
  if (!Array.isArray(b) || b.length !== 4 || !b.every((v) => typeof v === "number" && Number.isFinite(v))) {
    problems.push(`bounds_lonlat must be four finite numbers [W, S, E, N], got ${JSON.stringify(b)}`);
  } else {
    const [w0, s0, e0, n0] = b as number[];
    if (w0 === e0) problems.push("bounds_lonlat has W == E");
    if (s0 >= n0) problems.push(`bounds_lonlat has S >= N (${s0} >= ${n0})`);
  }
  for (const key of ["vmin", "vmax"] as const) {
    const v = p[key];
    if (v !== null && v !== undefined && (typeof v !== "number" || !Number.isFinite(v))) {
      problems.push(`${key} must be finite or null`);
    }
  }
  return problems;
}

export function decodeLayer(layer: LayerPayload): DecodeResult {
  try {
    const bytes = Uint8Array.from(atob(layer.data_b64), (c) => c.charCodeAt(0));
    if (layer.dtype === "<u1") return { ok: true, data: new Uint8Array(bytes.buffer) };
    return { ok: true, data: new Float32Array(bytes.buffer) };
  } catch (e) {
    return { ok: false, reason: e instanceof Error ? e.message : String(e) };
  }
}

function lerp(a: number, b: number, t: number): number {
  return Math.round(a + (b - a) * t);
}

function ramp(colormap: string, t: number): [number, number, number] {
  if (colormap.startsWith("diverging")) {
    // terracotta -> cream -> teal (the engine's diverging_teal_terracotta, cream in the middle)
    if (t < 0.5) return [lerp(158, 247, t * 2), lerp(59, 242, t * 2), lerp(44, 233, t * 2)];
    return [lerp(247, 44, (t - 0.5) * 2), lerp(242, 95, (t - 0.5) * 2), lerp(233, 88, (t - 0.5) * 2)];
  }
  // sequential cream -> teal
  return [lerp(247, 26, t), lerp(242, 77, t), lerp(233, 68, t)];
}

/** colorize a validated payload into a canvas; NaN/0 (categorical none) -> transparent. */
export function colorize(layer: LayerPayload, decoded: Float32Array | Uint8Array, canvas: HTMLCanvasElement, upscale = 4): void {
  const [h, w] = layer.shape;
  const src = document.createElement("canvas");
  src.width = w;
  src.height = h;
  const ctx = src.getContext("2d");
  if (!ctx) return;
  const img = ctx.createImageData(w, h);
  const vmin = layer.vmin ?? 0;
  const vmax = layer.vmax ?? 1;
  const span = vmax - vmin || 1;
  for (let i = 0; i < w * h; i++) {
    const v = Number(decoded[i]);
    let r = 0;
    let g = 0;
    let b = 0;
    let a = 0;
    if (layer.dtype === "<u1") {
      const cat = layer.categories?.find((c) => c.value === v);
      if (v !== 0 || cat) {
        const hex = toneColor(cat?.tone);
        r = parseInt(hex.slice(1, 3), 16);
        g = parseInt(hex.slice(3, 5), 16);
        b = parseInt(hex.slice(5, 7), 16);
        a = 210;
      }
    } else if (Number.isFinite(v)) {
      const t = Math.max(0, Math.min(1, (v - vmin) / span));
      [r, g, b] = ramp(layer.colormap, t);
      a = 205;
    }
    img.data[i * 4] = r;
    img.data[i * 4 + 1] = g;
    img.data[i * 4 + 2] = b;
    img.data[i * 4 + 3] = a;
  }
  ctx.putImageData(img, 0, 0);
  // nearest-neighbour upscale BEFORE the GPU sees the texture (S1 spike workaround)
  if (upscale > 1 && w * upscale <= 4096 && h * upscale <= 4096) {
    canvas.width = w * upscale;
    canvas.height = h * upscale;
    const c2 = canvas.getContext("2d");
    if (c2) {
      c2.imageSmoothingEnabled = false;
      c2.drawImage(src, 0, 0, canvas.width, canvas.height);
      return;
    }
  }
  canvas.width = w;
  canvas.height = h;
  const fallback = canvas.getContext("2d");
  fallback?.drawImage(src, 0, 0);
}

export interface LegendStop {
  color: string;
  label: string;
}

/** the legend, from the SAME ramp as the overlay (single colour implementation) */
export function legendStops(layer: LayerPayload): { kind: "continuous"; stops: LegendStop[] } | {
  kind: "categorical";
  stops: LegendStop[];
} {
  if (layer.dtype === "<u1") {
    const cats = layer.categories ?? [];
    const stops: LegendStop[] = (cats.length ? cats : [fallbackCategory(1), fallbackCategory(2)]).map((c) => ({
      color: toneColor(c.tone),
      label: c.label,
    }));
    return { kind: "categorical", stops };
  }
  const vmin = layer.vmin ?? 0;
  const vmax = layer.vmax ?? 1;
  const stops: LegendStop[] = [];
  const N = 6;
  for (let i = 0; i <= N; i++) {
    const t = i / N;
    const [r, g, b] = ramp(layer.colormap, t);
    const v = vmin + (vmax - vmin) * t;
    stops.push({
      color: `rgb(${r},${g},${b})`,
      label: fmtLegendValue(v),
    });
  }
  return { kind: "continuous", stops };
}

function fmtLegendValue(v: number): string {
  if (!Number.isFinite(v)) return "—";
  const a = Math.abs(v);
  if (a === 0) return "0";
  if (a >= 1e4 || a < 1e-2) return v.toExponential(1);
  return String(Number(v.toPrecision(3)));
}

export { TOKENS };

// ---------------------------------------------------------------------------------------------
// S1/U6 antimeridian fix: maplibre-gl 5.24 image sources CANNOT draw a bounds rectangle that
// crosses the ±180 seam in one piece. Verified empirically (app-ui/spikes, deleted after):
//   * wrapped form (W=179.985, E=-179.985): the texture stretches the LONG way round
//     (~359.97°) — the overlay covers half the world instead of the 10 km box;
//   * unwrapped form (E=180.015): the image is not rendered at the seam and the basemap tile
//     enumeration breaks ("x=2048 … outside of bounds").
// The engine's tile bboxes/polygons may legitimately straddle the seam, so the overlay is split
// at ±180 into at most two image sources, each taking its own horizontal crop of the texture.
// Pure display logic on an already-engine-provided rectangle — no tiling math.
// ---------------------------------------------------------------------------------------------

export interface SeamSplit {
  /** the part's own rectangle, both sides normalised into [-180, 180] */
  bounds: [number, number, number, number];
  /** horizontal crop of the source texture in fractions [x0, x1] (of the full width) */
  crop: [number, number];
}

function wrap180(lng: number): number {
  let v = lng;
  while (v > 180) v -= 360;
  while (v < -180) v += 360;
  return v;
}

/** split [W, S, E, N] at the antimeridian when it crosses it; otherwise one part covering all.
 *  Handles all three engine-emittable forms: normal (W < E, both in [-180, 180]), the wrapped
 *  crossing (E < W, e.g. W=179.985 / E=-179.985) and the unwrapped crossing (E > 180 or W < -180). */
export function seamSplits(bounds: [number, number, number, number]): SeamSplit[] {
  const [w0, s, e0, n] = bounds;
  if (!bounds.every((v) => Number.isFinite(v))) return [];
  let w = w0;
  let e = e0;
  if (e < w) e += 360;                     // wrapped crossing of +180 -> unwrapped E (e === w stays zero-width -> refused below)
  if (w < -180) { w += 360; e += 360; }    // crossing of -180 with W below the range
  const span = e - w;
  if (span <= 0 || span > 360.5) return []; // degenerate; the caller reports it
  if (e <= 180) return [{ bounds: [w, s, e, n], crop: [0, 1] }];
  const fA = (180 - w) / span;             // texture fraction west of the seam
  return [
    { bounds: [w, s, 180, n], crop: [0, fA] },
    { bounds: [-180, s, wrap180(e), n], crop: [fA, 1] },
  ];
}
