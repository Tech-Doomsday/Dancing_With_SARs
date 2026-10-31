"use client";

/** One shared fetch of GET /api/background/explain for the whole tab (module-level promise cache).
 *  A failed load clears the cache so the next call (e.g. after a token is saved) asks again. */
import { bg } from "./bg";
import type { ExplainPayload } from "./types";

let inflight: Promise<ExplainPayload> | null = null;

export function loadExplain(): Promise<ExplainPayload> {
  if (!inflight) {
    inflight = bg.explain().catch((err) => {
      inflight = null;
      throw err;
    });
  }
  return inflight;
}

/** unknown -> an em dash, never the strings "null" / "undefined" / "NaN" */
export function dash(v: string | number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number" && !Number.isFinite(v)) return "—";
  const t = String(v).trim();
  return t === "" || t === "null" || t === "undefined" || t === "NaN" ? "—" : t;
}
