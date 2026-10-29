"use client";

import { FALLBACK_CHIP_CLASSES, STATUS_CHIP, TIER_CHIP_CLASSES } from "@/lib/status";

/** Engine status / tier -> colour + label (docs/IMPLEMENTATION_PLAN.md 11.3). The tables live in lib/status.ts.
 *  With a `tier` the colour comes from the tier table and the label is the caller's `label` (the engine's
 *  tier_label); without one it takes the status-only path. `compact` is the small chip the globe panels use. */
export default function StatusBadge({ status, tier, label, compact }: {
  status: string;
  tier?: string | null;
  label?: string | null;
  compact?: boolean;
}) {
  let cls: string;
  let text: string;
  if (tier !== undefined) {
    cls = TIER_CHIP_CLASSES[tier ?? ""] ?? FALLBACK_CHIP_CLASSES;
    text = label ?? status.replace(/_/g, " ").toLowerCase();
  } else {
    const style = STATUS_CHIP[status] ?? { cls: FALLBACK_CHIP_CLASSES, label: status };
    cls = style.cls;
    text = style.label;
  }
  const size = compact ? "shrink-0 px-2 text-[10px]" : "px-2.5 text-xs";
  return (
    <span className={`inline-flex items-center rounded-full border py-0.5 font-medium ${size} ${cls}`}>
      {text}
    </span>
  );
}
