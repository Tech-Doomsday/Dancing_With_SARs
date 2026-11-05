/** Display formatting only — no science, no thresholds. */

export const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "3 h ago" / "just now" / "in 2 min" for an ISO timestamp; "—" when it cannot be parsed. */
export function timeAgo(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return "—";
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return "—";
  const s = Math.round((now - t) / 1000);
  const abs = Math.abs(s);
  const unit =
    abs < 45 ? null : abs < 3600 ? [Math.round(abs / 60), "min"] : abs < 86400 ? [Math.round(abs / 3600), "h"] : [Math.round(abs / 86400), "d"];
  if (!unit) return "just now";
  return s >= 0 ? `${unit[0]} ${unit[1]} ago` : `in ${unit[0]} ${unit[1]}`;
}

/** "2026-10-28 05:10 UTC" */
export function fmtUtc(iso: string | null | undefined): string {
  if (!iso) return "—";
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return "—";
  return new Date(t).toISOString().slice(0, 16).replace("T", " ") + " UTC";
}

/** Compact number for evidence chips: 4 significant figures, no trailing noise. */
export function fmtNum(v: number): string {
  if (!Number.isFinite(v)) return String(v);
  if (v === 0) return "0";
  const a = Math.abs(v);
  if (a >= 1e5 || a < 1e-3) return v.toExponential(2);
  return String(Number(v.toPrecision(4)));
}

/** Share of a fraction as text that never rounds a real, non-zero amount down to "0%". */
export function fmtShare(f: number | null | undefined): string {
  if (f == null || !Number.isFinite(f)) return "—";
  if (f <= 0) return "0%";
  if (f < 0.01) return "<1%";
  return `${Math.round(f * 100)}%`;
}

/** What coherence validation did, in words: "faded below coherence 0.6 · 48% nearly transparent". "" when off. */
export function qualityLegend(q: { mode?: string; applied?: boolean; hi?: number; lo?: number;
                                   hidden_fraction?: number } | null | undefined): string {
  if (!q || q.mode !== "coherence" || !q.applied) return "";
  return `faded below coherence ${fmtNum(q.hi ?? 0)} · ${fmtShare(q.hidden_fraction)} nearly transparent (≤ ${fmtNum(q.lo ?? 0)})`;
}

/** One legend line for an engine preview: colour map, scale, the colour range (marked when it was fixed rather
 *  than stretched from the image), the DATA's own min…max (so a range that misses the data is visible at a
 *  glance), the unit and the no-data share. Display only: every number comes from the engine's metadata. */
export function previewLegend(m: {
  cmap?: string | null; scale?: string | null; lo?: number | null; hi?: number | null; unit?: string | null;
  range_fixed?: boolean; nan_fraction?: number | null;
  stats?: { min: number | null; max: number | null } | null;
}): string {
  const n = (v: number | null | undefined) => (v == null ? "—" : fmtNum(v));
  const parts = [
    m.cmap || "—", m.scale || "—",
    `colours ${n(m.lo)} … ${n(m.hi)}${m.range_fixed ? " (fixed)" : ""}`,
    `data ${n(m.stats?.min)} … ${n(m.stats?.max)}`,
    `unit ${m.unit || "—"}`,
    `no-data ${fmtShare(m.nan_fraction)}`,
  ];
  return parts.join(" · ");
}
