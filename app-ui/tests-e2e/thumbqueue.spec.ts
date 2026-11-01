import { test, expect } from "@playwright/test";
import { THUMB_CONCURRENCY, ThumbError, clearThumbCache, requestThumb } from "../src/lib/thumbQueue";

/** Node-level specs for the Layers-tab thumbnail queue (no browser, no engine): global fetch is replaced by a
 *  scripted fake, window.sessionStorage by an empty store. Asserts the concurrency cap, the cache, abort, the
 *  429 back-off (no automatic retry loop) and the bounded 409 retry. WRITTEN, NOT RUN in the authoring sandbox
 *  (no node_modules there): run `npx playwright test tests-e2e/thumbqueue.spec.ts`. */
type Script = (n: number, url: string) => { status: number; headers?: Record<string, string>; body?: unknown; delayMs?: number };

function install(script: Script) {
  const g = globalThis as unknown as Record<string, unknown>;
  g.window = { sessionStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} }, localStorage: { getItem: () => null } };
  const stats = { calls: 0, inFlight: 0, maxInFlight: 0 };
  g.fetch = async (url: string, init?: { signal?: AbortSignal }) => {
    const n = ++stats.calls;
    stats.inFlight++; stats.maxInFlight = Math.max(stats.maxInFlight, stats.inFlight);
    const r = script(n, String(url));
    try {
      await new Promise<void>((res, rej) => {
        const t = setTimeout(res, r.delayMs ?? 5);
        init?.signal?.addEventListener("abort", () => { clearTimeout(t); rej(new DOMException("aborted", "AbortError")); }, { once: true });
      });
    } finally { stats.inFlight--; }
    const meta = JSON.stringify({ cmap: "gray", scale: "db", lo: -25, hi: 0, unit: "dB" });
    const ok = r.status >= 200 && r.status < 300;
    return new Response(ok ? new Blob([new Uint8Array([137, 80, 78, 71])], { type: "image/png" }) : JSON.stringify(r.body ?? { error: "x" }),
      { status: r.status, headers: { "X-Img-Meta": meta, "Content-Type": ok ? "image/png" : "application/json", ...(r.headers ?? {}) } });
  };
  return stats;
}
const BOX: [number, number, number, number] = [1, 2, 3, 4];
const ask = (layer: string, signal = new AbortController().signal) =>
  requestThumb({ granuleId: "g", layerKey: layer, bbox: BOX, signal });

test.beforeEach(() => clearThumbCache());

test("never more than THUMB_CONCURRENCY previews in flight", async () => {
  const s = install(() => ({ status: 200, delayMs: 20 }));
  await Promise.all(Array.from({ length: 8 }, (_, i) => ask(`l${i}`).catch(() => null)));
  expect(s.maxInFlight).toBeLessThanOrEqual(THUMB_CONCURRENCY);
  expect(s.calls).toBe(8);
});

test("a repeated request is served from the cache (one fetch)", async () => {
  const s = install(() => ({ status: 200 }));
  await ask("a").catch(() => null);
  await ask("a").catch(() => null);
  expect(s.calls).toBe(1);
});

test("an aborted queued request never reaches the network", async () => {
  const s = install(() => ({ status: 200, delayMs: 30 }));
  const ctrl = new AbortController();
  const jobs = [ask("a").catch(() => null), ask("b").catch(() => null)];
  const queued = ask("c", ctrl.signal);
  ctrl.abort();
  await expect(queued).rejects.toBeInstanceOf(ThumbError);
  await Promise.all(jobs);
  expect(s.calls).toBe(2);
});

test("429 -> rate-limited ThumbError, no automatic retry, later requests are held back", async () => {
  const s = install(() => ({ status: 429, headers: { "Retry-After": "30" }, body: { error: "slow down" } }));
  const e = await ask("a").catch((x) => x) as ThumbError;
  expect(e).toBeInstanceOf(ThumbError);
  expect(e.rateLimited).toBe(true);
  const e2 = await ask("b").catch((x) => x) as ThumbError;
  expect(e2.rateLimited).toBe(true);
  expect(s.calls).toBe(1);               // the second request never left the browser
});

test("409 (engine busy) is retried a bounded number of times, then reported", async () => {
  const s = install(() => ({ status: 409, body: { error: "a preview is already running" } }));
  const e = await ask("a").catch((x) => x) as ThumbError;
  expect(e).toBeInstanceOf(ThumbError);
  expect(s.calls).toBe(4);               // 1 + 3 retries
});
