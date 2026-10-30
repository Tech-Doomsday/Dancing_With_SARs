/** Display constants ONLY (labels / colours / display precedence). The engine supplies the tier
 *  and the status; nothing here derives or interprets them.
 *
 *  Colour is never the only carrier of meaning: every colour pairing below has a text label
 *  wherever it is used (badges, timeline, legend). */

import type { LayerCategory } from "./types";

/** design tokens (globals.css) as hex for canvas/SVG use */
export const TOKENS = {
  page: "#faf9f6",
  ink: "#1c1a17",
  muted: "#8a837b",
  teal: "#2c5f58",
  terracotta: "#9e3b2c",
  gold: "#b08d57",
  border: "#e7e2da",
  card: "#ffffff",
  space: "#0b0d12",
} as const;

/** which tier is "most notable" when a tile has several verdicts — display ordering only */
export const TIER_PRECEDENCE = [
  "supported",
  "candidate",
  "quiet",
  "not_supported",
  "insufficient",
  "pending",
  "not_applicable",
  "not_modelled",
  "error",
] as const;

export function mostNotableTier(tiers: (string | null | undefined)[]): string {
  for (const t of TIER_PRECEDENCE) {
    if (tiers.includes(t)) return t;
  }
  for (const t of tiers) {
    if (t) return t;
  }
  return "insufficient";
}

/** pin colour per tier (echoes the StatusBadge palette; always paired with a text label in the UI) */
export const TIER_PIN_COLORS: Record<string, string> = {
  supported: TOKENS.teal,
  candidate: "#5d8a83",
  quiet: TOKENS.muted,
  not_supported: "#4a443c",
  insufficient: TOKENS.gold,
  pending: "#c4a770",
  not_applicable: "#a8a196",
  not_modelled: "#b5aea3",
  error: TOKENS.terracotta,
};

/** Badge / chip class strings. Tailwind arbitrary values have to appear as complete literals, so the
 *  looks are written once here; the tier table and the status-only table below both pick from them. */
const LOOK = {
  solid: "bg-[#2c5f58] text-white border-[#2c5f58]",
  teal: "border-[#2c5f58] text-[#2c5f58] bg-[#eff5f3]",
  muted: "border-[#d8d2c6] bg-[#f7f4ee] text-[#8a837b]",
  mutedInk: "border-[#d8d2c6] bg-[#f7f4ee] text-[#4a443c]",
  dashed: "border-dashed border-[#d8d2c6] bg-[#f7f4ee] text-[#8a837b]",
  gold: "border-[#b08d57]/50 bg-[#f7f2e7] text-[#7a6438]",
  error: "border-[#9e3b2c] bg-[#fbf3ef] text-[#9e3b2c]",
} as const;

/** chip / badge classes per TIER — one entry for every tier the engine can emit (engine/verdict/calibrate.py
 *  TIER_LABEL; tests/test_status_table_complete.py pins that no tier is missing) */
export const TIER_CHIP_CLASSES: Record<string, string> = {
  supported: LOOK.solid,
  candidate: LOOK.teal,
  quiet: LOOK.muted,
  not_supported: LOOK.mutedInk,
  insufficient: LOOK.gold,
  pending: LOOK.gold,
  not_applicable: LOOK.muted,
  not_modelled: LOOK.dashed,
  error: LOOK.error,
};

/** chip classes for a tier the engine did not name (or a result with none) */
export const FALLBACK_CHIP_CLASSES = LOOK.mutedInk;

/** STATUS-ONLY path: for the few call sites that hold a raw engine status or a run status and no tier
 *  (RunProgress: queued/running/ok; PipelineGrid and RunLauncher: literal NOT_RUN / NOT_MODELLED; the
 *  legacy report pages). The UI does not derive a tier from a status, so these stay status-keyed. */
export const STATUS_CHIP: Record<string, { cls: string; label: string }> = {
  SUPPORTED: { cls: LOOK.solid, label: "supported by evidence" },
  POSSIBLE: { cls: LOOK.teal, label: "candidate / possible" },
  UNCATALOGUED_CANDIDATE: { cls: LOOK.teal, label: "uncatalogued candidate" },
  NOT_SUPPORTED: { cls: LOOK.mutedInk, label: "not supported by evidence" },
  INSUFFICIENT_EVIDENCE: { cls: LOOK.gold, label: "not enough evidence" },
  NO_SIGNAL: { cls: LOOK.muted, label: "no signal — quiet tile" },
  NOT_APPLICABLE: { cls: LOOK.muted, label: "did not apply here" },
  NOT_RUN: { cls: LOOK.muted, label: "not launched" },
  NOT_MODELLED: { cls: LOOK.dashed, label: "not modelled yet" },
  PENDING: { cls: LOOK.gold, label: "not run (yet)" },
  ERROR: { cls: LOOK.error, label: "run failed" },
  queued: { cls: LOOK.gold, label: "queued" },
  running: { cls: LOOK.gold, label: "running" },
  ok: { cls: LOOK.teal, label: "finished" },
};

/** The Data tab's ascending/descending bar colours: teal for ascending, terracotta for
 *  descending (contrast pair from the tokens). Hue alone is never the only cue — rows and
 *  selections always carry a text label ("A034 ascending" / "D013 descending"). */
export const DIRECTION_COLORS: Record<string, string> = {
  A: TOKENS.teal,
  D: TOKENS.terracotta,
};

export function directionLabel(direction: string | null | undefined): string {
  if (direction === "A") return "ascending";
  if (direction === "D") return "descending";
  return "direction unknown";
}

/** category tone -> colour (legend swatches for categorical layers; engine's labels shown) */
export function toneColor(tone: string | undefined): string {
  if (tone === "positive") return TOKENS.teal;
  if (tone === "negative") return TOKENS.terracotta;
  return TOKENS.muted;
}

/** fallback swatch for a categorical value with no engine category entry */
export function fallbackCategory(value: number): LayerCategory {
  return { value, label: `value ${value}`, tone: "neutral" };
}
