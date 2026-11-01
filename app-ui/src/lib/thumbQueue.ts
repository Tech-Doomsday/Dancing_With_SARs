"use client";

/** Thumbnail transport for the Layers tab: a small queue (at most THUMB_CONCURRENCY previews in flight),
 *  an in-memory cache keyed by granule|layer|bbox|scale, abort support, and a back-off that never loops.
 *  Images are kept as data URLs, so there is no object URL to leak or revoke. No science here: the
 *  engine picks colours, scale and unit (cmap=auto, scale=auto); this only fetches and remembers. */
import { bg, PreviewHttpError } from "./bg";
import type { PreviewMeta } from "./types";

export const THUMB_CONCURRENCY = 2;
export const THUMB_MAX_PX = 96;
const CACHE_MAX = 80;
const BUSY_RETRIES = 3;          // the engine runs ONE uncached preview at a time (409 = busy): bounded retry
const BUSY_DELAY_MS = 700;

export interface Thumb { dataUrl: string; meta: PreviewMeta }

export class ThumbError extends Error {
  constructor(message: string, readonly rateLimited = false, readonly retryAfterS: number | null = null) {
    super(message);
    this.name = "ThumbError";
  }
}

const cache = new Map<string, Thumb>();
let blockedUntil = 0;            // set by a 429: nothing is requested before this time
let running = 0;
type Job = { key: string; run: () => Promise<void> };
const pending: Job[] = [];

export const thumbKey = (granule: string, layer: string, bbox: readonly number[], scale: string) =>
  `${granule}|${layer}|${bbox.join(",")}|${scale}`;
export const thumbCached = (key: string): Thumb | undefined => cache.get(key);
export function clearThumbCache(): void { cache.clear(); blockedUntil = 0; }

function pump() {
  while (running < THUMB_CONCURRENCY && pending.length) {
    const job = pending.shift()!;
    running++;
    void job.run().finally(() => { running--; pump(); });
  }
}

function blobToDataUrl(b: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result));
    r.onerror = () => reject(new Error("thumbnail could not be read"));
    r.readAsDataURL(b);
  });
}

const sleep = (ms: number, signal: AbortSignal) => new Promise<void>((resolve) => {
  const t = setTimeout(resolve, ms);
  signal.addEventListener("abort", () => { clearTimeout(t); resolve(); }, { once: true });
});

/** Resolve a thumbnail (cache first). Rejects with ThumbError (rate limit / failure) or an AbortError-like
 *  ThumbError("aborted") when the caller's signal fires; the caller shows "—" and the message. */
export function requestThumb(p: {
  granuleId: string; layerKey: string; bbox: [number, number, number, number]; scale?: string; signal: AbortSignal;
}): Promise<Thumb> {
  const scale = p.scale ?? "auto";
  const key = thumbKey(p.granuleId, p.layerKey, p.bbox, scale);
  const hit = cache.get(key);
  if (hit) return Promise.resolve(hit);
  return new Promise<Thumb>((resolve, reject) => {
    const aborted = () => reject(new ThumbError("aborted"));
    if (p.signal.aborted) return aborted();
    const job: Job = {
      key,
      run: async () => {
        if (p.signal.aborted) return aborted();
        if (Date.now() < blockedUntil) {
          const s = Math.ceil((blockedUntil - Date.now()) / 1000);
          return reject(new ThumbError(`rate limited by the engine — wait about ${s} s, then retry`, true, s));
        }
        for (let attempt = 0; ; attempt++) {
          try {
            const { blob, meta } = await bg.previewBlob({
              id: p.granuleId, layer: p.layerKey, west: p.bbox[0], south: p.bbox[1], east: p.bbox[2], north: p.bbox[3],
              maxPx: THUMB_MAX_PX, cmap: "auto", scale }, p.signal);
            if (p.signal.aborted) return aborted();
            if (!blob) return reject(new ThumbError("the engine returned no image"));
            const t: Thumb = { dataUrl: await blobToDataUrl(blob), meta };
            if (p.signal.aborted) return aborted();
            if (cache.size >= CACHE_MAX) cache.delete(cache.keys().next().value as string);
            cache.set(key, t);
            return resolve(t);
          } catch (err) {
            if (p.signal.aborted) return aborted();
            if (err instanceof PreviewHttpError && err.status === 429) {
              const s = err.retryAfterS ?? 10;
              blockedUntil = Date.now() + s * 1000;
              return reject(new ThumbError(`rate limited by the engine — wait about ${s} s, then retry`, true, s));
            }
            if (err instanceof PreviewHttpError && err.status === 409 && attempt < BUSY_RETRIES) {
              await sleep(BUSY_DELAY_MS, p.signal);
              continue;
            }
            return reject(new ThumbError(err instanceof Error ? err.message : String(err)));
          }
        }
      },
    };
    p.signal.addEventListener("abort", () => {
      const i = pending.indexOf(job);
      if (i >= 0) { pending.splice(i, 1); aborted(); }
    }, { once: true });
    pending.push(job);
    pump();
  });
}
