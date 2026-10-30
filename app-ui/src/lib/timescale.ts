/** Pure UTC linear time-scale math for the Data tab's granule timeline (DEC11: no charting
 *  dependency — plain arithmetic). No DOM here; unit-tested.
 *
 *  t <-> px over a visible window; adaptive tick spacing (day ticks under ~45 days visible,
 *  week ticks under ~9 months, month ticks beyond); pan/zoom clamped to the full window. */

import { MONTHS } from "./format";

export interface Millis {
  ms: number;
}

export interface Tick {
  ms: number;
  label: string;
  major: boolean;
}

const DAY_MS = 86_400_000;
const WEEK_MS = 7 * DAY_MS;

/** one linear scale: pxPerMs is derived, everything else follows */
export interface TimeScale {
  t0: number; // visible window start (ms)
  t1: number; // visible window end (ms)
  widthPx: number;
}

export function pxPerMs(scale: TimeScale): number {
  return scale.widthPx / Math.max(1, scale.t1 - scale.t0);
}

export function tToX(scale: TimeScale, t: number): number {
  return (t - scale.t0) * pxPerMs(scale);
}

export function xToT(scale: TimeScale, x: number): number {
  return scale.t0 + x / pxPerMs(scale);
}

/** pan by pixels (drag); returns the new window */
export function pan(scale: TimeScale, dxPx: number, bounds: { min: number; max: number }): TimeScale {
  const dt = dxPx / pxPerMs(scale);
  let { t0, t1 } = scale;
  t0 -= dt;
  t1 -= dt;
  return clamp({ ...scale, t0, t1 }, bounds);
}

/** zoom keeping `anchorX` (px) fixed on the same instant; factor > 1 zooms in.
 *  The visible span never goes below one day (a tighter window has no granule meaning). */
export function zoom(scale: TimeScale, factor: number, anchorX: number, bounds: { min: number; max: number }): TimeScale {
  const anchorT = xToT(scale, anchorX);
  const capped = Math.max(1.02, Math.min(50, factor));
  let span = (scale.t1 - scale.t0) / capped;
  if (span < DAY_MS) span = DAY_MS;
  const frac = anchorX / Math.max(1, scale.widthPx);
  let t0 = anchorT - span * frac;
  const t1 = t0 + span;
  return clamp({ ...scale, t0, t1 }, bounds);
}

export function clamp(scale: TimeScale, bounds: { min: number; max: number }): TimeScale {
  const maxSpan = Math.max(bounds.max - bounds.min, DAY_MS);
  let span = Math.min(scale.t1 - scale.t0, maxSpan);
  let t0 = scale.t0;
  if (t0 < bounds.min) t0 = bounds.min;
  if (t0 + span > bounds.max) t0 = bounds.max - span;
  // a clamped window that would sit entirely outside the bounds snaps back
  if (t0 + span <= bounds.min) t0 = bounds.max - span;
  return { ...scale, t0, t1: t0 + span };
}

/** UTC day start of a ms instant */
function dayStart(ms: number): number {
  return Math.floor(ms / DAY_MS) * DAY_MS;
}

/** adaptive ticks for the visible window (UTC labels). Levels: hours/6h (tiny spans, the
 *  Explorer's zoom), days, weeks, months — as the span demands. The Data tab's behaviour for
 *  its spans is unchanged (the hour branch only covers spans <= 3 days, below its 1-day zoom floor). */
export function ticks(scale: TimeScale, targetCount = 8): Tick[] {
  const span = scale.t1 - scale.t0;
  const out: Tick[] = [];
  const d = new Date(0);
  const fmtDay = (ms: number) => {
    d.setTime(ms);
    return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`;
  };
  const fmtMonth = (ms: number) => {
    d.setTime(ms);
    return `${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
  };
  const fmtHour = (ms: number) => {
    d.setTime(ms);
    return `${String(d.getUTCHours()).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")}`;
  };
  if (span <= 3 * DAY_MS) {
    // hour ticks: 1 h when tight, 6 h when loose; the day rollover is the major label
    const stepHours = span <= 10 * 3_600_000 ? 1 : 6;
    let t = Math.ceil(scale.t0 / (stepHours * 3_600_000)) * stepHours * 3_600_000;
    while (t <= scale.t1) {
      const dt = new Date(t);
      const major = dt.getUTCHours() === 0 && dt.getUTCMinutes() === 0;
      out.push({ ms: t, label: major ? fmtDay(t) : fmtHour(t), major });
      t += stepHours * 3_600_000;
    }
    return out;
  }
  if (span <= 45 * DAY_MS) {
    const stepDays = Math.max(1, Math.round(span / targetCount / DAY_MS));
    let t = dayStart(scale.t0);
    while (t <= scale.t1) {
      const dt = new Date(t);
      const major = dt.getUTCDate() === 1;
      out.push({ ms: t, label: major ? fmtMonth(t) : fmtDay(t), major });
      t += stepDays * DAY_MS;
    }
    return out;
  }
  if (span <= 270 * DAY_MS) {
    // week ticks anchored on UTC Mondays
    let t = dayStart(scale.t0);
    const dow = new Date(t).getUTCDay();
    t -= ((dow + 6) % 7) * DAY_MS; // back to Monday
    while (t <= scale.t1) {
      const dt = new Date(t);
      const major = dt.getUTCDate() <= 7;
      out.push({ ms: t, label: major ? fmtMonth(t) : fmtDay(t), major });
      t += WEEK_MS;
    }
    return out;
  }
  // month ticks
  const start = new Date(scale.t0);
  let y = start.getUTCFullYear();
  let m = start.getUTCMonth();
  const stepMonths = Math.max(1, Math.round((span / (targetCount * DAY_MS)) / 30));
  while (true) {
    const t = Date.UTC(y, m, 1);
    if (t > scale.t1) break;
    if (t >= scale.t0 - DAY_MS) {
      out.push({ ms: t, label: fmtMonth(t), major: m === 0 });
    }
    m += stepMonths;
    y += Math.floor(m / 12);
    m = ((m % 12) + 12) % 12;
  }
  return out;
}

/** gap threshold for a row: 1.5x its median spacing (never a hard-coded judgement), or null
 *  when the row has fewer than two DISTINCT times. Distinct, not raw: a row mixes pair-bar
 *  starts/ends and epoch markers, and one acquisition's end equals the next bar's start — the
 *  duplicate 0-day spacings would otherwise drag the median to ~0 and label every normal
 *  cadence step a "gap". */
export function gapThresholdDays(times: number[]): number | null {
  const uniq = Array.from(new Set(times)).sort((a, b) => a - b);
  if (uniq.length < 2) return null;
  const gaps: number[] = [];
  for (let i = 1; i < uniq.length; i++) gaps.push((uniq[i] - uniq[i - 1]) / DAY_MS);
  gaps.sort((a, b) => a - b);
  const median = gaps[Math.floor((gaps.length - 1) / 2)];
  return 1.5 * Math.max(median, 0.5);
}
