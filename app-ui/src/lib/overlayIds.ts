/** Map sources for an overlay are named `<overlay spec id>:<part>` (GlobeMap.applyPart), and every spec id already
 *  starts with "overlay:" (results: `overlay:<key>`, SAR: `overlay:sar:...`). Returns the spec id a source belongs
 *  to, or null when the source is not an overlay part. Display bookkeeping only. */
export const OVERLAY_PREFIX = "overlay:";

export function overlaySpecIdOfSource(sourceId: string): string | null {
  if (!sourceId.startsWith(OVERLAY_PREFIX)) return null;
  const i = sourceId.lastIndexOf(":");
  return i >= OVERLAY_PREFIX.length ? sourceId.slice(0, i) : null;
}
