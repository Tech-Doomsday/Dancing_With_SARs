/**
 * Restrained scientific colormaps (warm light theme, no blue/indigo).
 */

export type RGB = [number, number, number];

function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

function ramp(stops: RGB[], t: number): RGB {
  const x = Math.max(0, Math.min(1, t)) * (stops.length - 1);
  const i = Math.min(stops.length - 2, Math.floor(x));
  const f = x - i;
  return [
    Math.round(lerp(stops[i][0], stops[i + 1][0], f)),
    Math.round(lerp(stops[i][1], stops[i + 1][1], f)),
    Math.round(lerp(stops[i][2], stops[i + 1][2], f)),
  ];
}

/** Diverging displacement scale: toward (terracotta) ← 0 → away (teal). */
const TOWARD: RGB[] = [
  [143, 74, 43],
  [201, 124, 84],
  [232, 196, 168],
];
const ZERO: RGB = [244, 239, 231];
const AWAY: RGB[] = [
  [207, 224, 218],
  [95, 145, 135],
  [44, 95, 88],
];

/** t in [-1, 1] → css color */
export function displacementColor(t: number): string {
  const c =
    t < 0 ? ramp(TOWARD, 1 + t) : t === 0 ? ZERO : ramp(AWAY, t);
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}

/** Sequential coherence 0..1 (light → dark warm gray) */
const COH: RGB[] = [
  [245, 241, 232],
  [203, 195, 181],
  [138, 131, 122],
  [74, 68, 60],
];
export function coherenceColor(t: number): string {
  const c = ramp(COH, t);
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}

/** Sequential uncertainty (light → deep terracotta) */
const SIG: RGB[] = [
  [245, 241, 232],
  [226, 199, 172],
  [176, 113, 78],
  [122, 74, 43],
];
export function sigmaColor(t: number): string {
  const c = ramp(SIG, t);
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}

/**
 * Cyclic colormap for wrapped radar phase (advanced layer). Muted, restrained
 * tones that cycle seamlessly: terracotta → sand → sage → umber → terracotta.
 * Each full cycle = one phase wrap = λ/2 of ground movement along the radar
 * line of sight.
 */
const PHASE: RGB[] = [
  [148, 82, 55], // terracotta
  [226, 198, 160], // sand
  [128, 148, 124], // sage
  [84, 66, 52], // umber
];
export function phaseColor(frac: number): string {
  // frac in [0, 1) wraps naturally through the 4-stop cycle (8 segments
  // including the return legs); identical at 0 and 1 by construction.
  const t = ((frac % 1) + 1) % 1;
  const x = t * PHASE.length;
  const i = Math.floor(x) % PHASE.length;
  const j = (i + 1) % PHASE.length;
  const f = x - Math.floor(x);
  const c: RGB = [
    Math.round(lerp(PHASE[i][0], PHASE[j][0], f)),
    Math.round(lerp(PHASE[i][1], PHASE[j][1], f)),
    Math.round(lerp(PHASE[i][2], PHASE[j][2], f)),
  ];
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}

/** Nice round color-scale maximum for displacement (metres). */
export function niceScaleMax(absMaxM: number): number {
  const mm = Math.max(absMaxM * 1000, 1);
  const steps = [2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000];
  for (const s of steps) if (mm <= s) return s / 1000;
  return Math.ceil(mm / 1000);
}

export function rgba(c: string, alpha: number): string {
  return c.replace('rgb(', 'rgba(').replace(')', `,${alpha})`);
}
