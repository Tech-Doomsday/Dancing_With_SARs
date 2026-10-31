/** Pure display helpers for the Background retrieval tab (§4.1/§4.2/§4.4). No DOM, no fetches;
 *  unit-tested in tests-e2e/unit.spec.ts. All grouping decisions are the ENGINE's — these
 *  helpers only format what the engine already stated. */

import type { BackgroundGroup, ExplorerGranule } from "./types";

/** One bar's [start, end] in ms from the engine's group times (pairs: ref→sec in TRUE time
 *  order; singles/unknown: the single start). null when the engine supplied no usable time. */
export function groupTimes(g: Pick<BackgroundGroup, "ref_time" | "sec_time" | "start">): {
  start: number;
  end: number;
} | null {
  const a = g.ref_time ?? g.start;
  const b = g.sec_time ?? g.start;
  const t0 = a ? Date.parse(a) : NaN;
  const t1 = b ? Date.parse(b) : NaN;
  if (!Number.isFinite(t0)) return null;
  const end = Number.isFinite(t1) ? t1 : t0;
  return { start: Math.min(t0, end), end: Math.max(t0, end) };
}

/** Text label that always accompanies the product glyph/colour (never colour alone). */
export function groupKindLabel(g: Pick<BackgroundGroup, "kind" | "product" | "grouped">): string {
  if (!g.grouped) return "ungrouped";
  if (g.kind === "pair") return `${g.product ?? "pair"} pair`;
  if (g.kind === "single") return `${g.product ?? "single"} single date`;
  return "unknown layout";
}

/** The facts that differ across a group's variants, formatted for the badge/tooltip. */
export function variantFactsLabel(g: Pick<BackgroundGroup, "variant_facts" | "n_variants">): string | null {
  if (g.n_variants <= 1) return null;
  return g.variant_facts.length > 0
    ? `${g.n_variants} variants (differ in ${g.variant_facts.map(friendlyFact).join(", ")})`
    : `${g.n_variants} variants`;
}

export function friendlyFact(f: string): string {
  switch (f) {
    case "range_bandwidth_mhz": return "bandwidth";
    case "crid": return "CRID";
    case "version": return "version";
    case "collection": return "collection";
    case "polarization": return "polarization";
    default: return f;
  }
}

/** The two GCOV granules offered as a GUNW's before/after backscatter pictures (§4.1): the
 *  FIRST listed variant of each end. Absent ends come back as null — the UI says
 *  "no backscatter acquisition found for this date", it never substitutes. */
export function compareEnds(pair: { ref: { granules: ExplorerGranule[] }; sec: { granules: ExplorerGranule[] } }): {
  before: ExplorerGranule | null;
  after: ExplorerGranule | null;
} {
  return {
    before: pair.ref.granules[0] ?? null,
    after: pair.sec.granules[0] ?? null,
  };
}

/** Sub-box for a zoomed preview request (§4.5: zoom = a NEW request for a sub-box, never a
 *  client-side upscale). Shrinks the current box around a focus fraction with a minimum span. */
export function zoomBox(
  box: [number, number, number, number],
  focusX: number,       // 0..1 within the box
  focusY: number,       // 0..1, 0 = north
  factor: number,       // >1 zooms in
  minSpan = 0.02,
): [number, number, number, number] {
  const [w, s, e, n] = box;
  const spanX = Math.max((e - w) / factor, minSpan);
  const spanY = Math.max((n - s) / factor, minSpan);
  const cx = w + (e - w) * Math.min(1, Math.max(0, focusX));
  const cy = n - (n - s) * Math.min(1, Math.max(0, focusY));
  return [
    Math.max(-180, cx - spanX / 2),
    Math.max(-90, cy - spanY / 2),
    Math.min(180, cx + spanX / 2),
    Math.min(90, cy + spanY / 2),
  ];
}

/** Pan a box by a fraction of its span (clamped to the world). */
export function panBox(
  box: [number, number, number, number],
  dxFrac: number,
  dyFrac: number,
): [number, number, number, number] {
  const [w, s, e, n] = box;
  const spanX = e - w;
  const spanY = n - s;
  const w2 = Math.min(180 - spanX, Math.max(-180, w - spanX * dxFrac));
  const s2 = Math.min(90 - spanY, Math.max(-90, s - spanY * dyFrac));
  return [w2, s2, w2 + spanX, s2 + spanY];
}
