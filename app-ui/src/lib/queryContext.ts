"use client";

/** Query context — the ONE place the app remembers "what area / window / level the user is
 *  looking at", so switching between Map and Background never makes them re-type it.
 *
 *  Before this existed the two screens kept completely separate state:
 *    · Map (GlobeApp)            → React state + ?lat=&lon=&z=&tile=       (a point + the engine's tile)
 *    · Background › Retrieval    → React state + ?west=&south=&east=&north= (a box)
 *  and the nav link to Background was a hard-coded "/background/retrieval" with no query string,
 *  so the Retrieval form always opened blank while the map was still mid-load.
 *
 *  Storage is sessionStorage (per tab, survives in-app navigation and reloads, gone when the tab
 *  closes). Everything read back is re-validated — a corrupt or stale value is ignored, never
 *  trusted. Nothing here is science: it only carries inputs the user already chose or the engine
 *  already returned (the tile's own bbox_lonlat). */

export type Bbox = [number, number, number, number]; // west, south, east, north

export interface QueryContext {
  /** the pin the user chose on the map */
  lat?: number;
  lon?: number;
  /** the engine's MGRS tile id for that pin (resolved by the engine, never client-side) */
  tile?: string;
  /** the engine's bbox for that tile, or the user's own box */
  bbox?: Bbox;
  /** comma list of GUNW|GCOV|GOFF */
  level?: string;
  /** window, YYYY-MM-DD (UTC) — what the <input type="date"> fields in Retrieval accept */
  from?: string;
  to?: string;
}

const KEY = "nisar:query-context:v1";
const EVENT = "nisar:query-context";

const LEVEL_RE = /^(GUNW|GCOV|GOFF)(,(GUNW|GCOV|GOFF))*$/;
const TILE_RE = /^\d{1,2}[C-X][A-HJ-NP-Z]{2}\d*$/;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

function inRange(v: unknown, lo: number, hi: number): v is number {
  return typeof v === "number" && Number.isFinite(v) && v >= lo && v <= hi;
}

/** keep only well-formed fields */
export function sanitize(raw: unknown): QueryContext {
  if (!raw || typeof raw !== "object") return {};
  const r = raw as Record<string, unknown>;
  const out: QueryContext = {};
  if (inRange(r.lat, -90, 90) && inRange(r.lon, -180, 180)) {
    out.lat = r.lat as number;
    out.lon = r.lon as number;
  }
  if (typeof r.tile === "string" && TILE_RE.test(r.tile)) out.tile = r.tile;
  if (Array.isArray(r.bbox) && r.bbox.length === 4) {
    const [w, s, e, n] = r.bbox as unknown[];
    if (inRange(w, -180, 180) && inRange(e, -180, 180) && inRange(s, -90, 90) && inRange(n, -90, 90)
        && (w as number) <= (e as number) && (s as number) < (n as number)) {
      out.bbox = [w as number, s as number, e as number, n as number];
    }
  }
  if (typeof r.level === "string" && LEVEL_RE.test(r.level.toUpperCase())) out.level = r.level.toUpperCase();
  if (typeof r.from === "string" && DATE_RE.test(r.from)) out.from = r.from;
  if (typeof r.to === "string" && DATE_RE.test(r.to)) out.to = r.to;
  return out;
}

/** "2026-07-02T10:00:00Z" → "2026-07-02" (date inputs reject anything longer) */
export function toDateOnly(iso: string | undefined | null): string | undefined {
  if (!iso) return undefined;
  const d = iso.slice(0, 10);
  return DATE_RE.test(d) ? d : undefined;
}

export function loadQueryContext(): QueryContext {
  try {
    const txt = window.sessionStorage.getItem(KEY);
    return txt ? sanitize(JSON.parse(txt)) : {};
  } catch {
    return {}; // private mode / corrupt JSON — start blank, never crash
  }
}

/** Merge `patch` into the stored context. A key set to `undefined` is REMOVED (so a new pin can
 *  drop the previous pin's bbox instead of leaving a mismatched box behind). */
export function saveQueryContext(patch: { [K in keyof QueryContext]: QueryContext[K] | undefined }): void {
  try {
    const merged: Record<string, unknown> = { ...loadQueryContext() };
    for (const [k, v] of Object.entries(patch)) {
      if (v === undefined) delete merged[k];
      else merged[k] = v;
    }
    window.sessionStorage.setItem(KEY, JSON.stringify(sanitize(merged)));
    window.dispatchEvent(new Event(EVENT));
  } catch { /* storage unavailable: the URL params still carry the state */ }
}

export function clearQueryContext(): void {
  try {
    window.sessionStorage.removeItem(KEY);
    window.dispatchEvent(new Event(EVENT));
  } catch { /* ignore */ }
}

/** The Retrieval URL for a context. A complete box makes Retrieval run the search ONCE on open
 *  (its existing "pasted link" behaviour), so the user lands on results, not an empty form. */
export function retrievalHref(ctx: QueryContext): string {
  const p = new URLSearchParams();
  if (ctx.bbox) {
    const [w, s, e, n] = ctx.bbox;
    p.set("west", String(Number(w.toFixed(4))));
    p.set("south", String(Number(s.toFixed(4))));
    p.set("east", String(Number(e.toFixed(4))));
    p.set("north", String(Number(n.toFixed(4))));
  }
  if (ctx.tile) p.set("tile", ctx.tile);
  if (ctx.level) p.set("level", ctx.level);
  if (ctx.from) p.set("from", ctx.from);
  if (ctx.to) p.set("to", ctx.to);
  const qs = p.toString();
  return qs ? `/background/retrieval?${qs}` : "/background/retrieval";
}

/** The Map URL for a context (used by the Background → Map link). */
export function mapHref(ctx: QueryContext): string {
  const p = new URLSearchParams();
  if (ctx.lat !== undefined && ctx.lon !== undefined) {
    p.set("lat", String(ctx.lat));
    p.set("lon", String(ctx.lon));
  } else if (ctx.tile) {
    p.set("tile", ctx.tile);
  }
  const qs = p.toString();
  return qs ? `/map?${qs}` : "/map";
}

/** subscribe to changes (same tab via custom event, other tabs via the storage event) */
export function subscribeQueryContext(cb: () => void): () => void {
  const onStorage = (e: StorageEvent) => { if (e.key === KEY || e.key === null) cb(); };
  window.addEventListener(EVENT, cb);
  window.addEventListener("storage", onStorage);
  return () => {
    window.removeEventListener(EVENT, cb);
    window.removeEventListener("storage", onStorage);
  };
}
