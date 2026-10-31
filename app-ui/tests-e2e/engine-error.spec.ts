/** Error classifier — pure node, no browser. Fetch is stubbed (test fixtures, labelled as such). */
import { test, expect } from "@playwright/test";
import { engineFetch, EngineError } from "../src/lib/engineError";

const BASE = "https://example-tunnel.trycloudflare.com";
const json = (b: unknown, status = 200) =>
  new Response(JSON.stringify(b), { status, headers: { "content-type": "application/json" } });

test("tunnel down: request and health both fail -> unreachable, never 'Failed to fetch'", async () => {
  const f = (async () => { throw new TypeError("Failed to fetch"); }) as typeof fetch;
  const e = await engineFetch(BASE, "/api/x", {}, { fetchImpl: f }).catch((x) => x);
  expect(e).toBeInstanceOf(EngineError);
  expect(e.kind).toBe("unreachable");
  expect(e.message).not.toContain("Failed to fetch");
  expect(e.message).toContain("example-tunnel.trycloudflare.com");
  expect(e.message).toMatch(/paste the current tunnel URL/);
});

test("request fails but health answers with busy -> busy, names the run and step", async () => {
  const f = (async (url: string) => {
    if (String(url).endsWith("/api/health"))
      return json({ ok: true, busy: { kind: "scan", run_id: "run-abc", step: "P02 clip", started_at: 1 } });
    throw new TypeError("Failed to fetch");
  }) as unknown as typeof fetch;
  const e = await engineFetch(BASE, "/api/x", {}, { fetchImpl: f }).catch((x) => x);
  expect(e.kind).toBe("busy");
  expect(e.message).toContain("run-abc");
  expect(e.message).toContain("P02 clip");
  expect(e.busy?.kind).toBe("scan");
});

test("request fails, health ok and idle -> cut (connection dropped mid-request)", async () => {
  const f = (async (url: string) => {
    if (String(url).endsWith("/api/health")) return json({ ok: true, busy: null });
    throw new TypeError("Failed to fetch");
  }) as unknown as typeof fetch;
  const e = await engineFetch(BASE, "/api/x", {}, { fetchImpl: f }).catch((x) => x);
  expect(e.kind).toBe("cut");
  expect(e.message).toMatch(/Tunnels drop requests/);
});

test("no answer within the timeout and health also silent -> timeout with the seconds", async () => {
  const f = ((url: string, init?: RequestInit) => new Promise((_res, rej) => {
    init?.signal?.addEventListener("abort", () => rej(new DOMException("aborted", "AbortError")));
    void url;
  })) as unknown as typeof fetch;
  const e = await engineFetch(BASE, "/api/x", {}, { fetchImpl: f, timeoutMs: 50 }).catch((x) => x);
  expect(e.kind).toBe("timeout");
  expect(e.message).toMatch(/after 0 s|after 1 s|No answer/);
});

test("HTTP errors are returned to the caller untouched (engine's own text is used there)", async () => {
  const f = (async () => json({ detail: { ok: false, error: "engine says no" } }, 409)) as unknown as typeof fetch;
  const r = await engineFetch(BASE, "/api/x", {}, { fetchImpl: f });
  expect(r.status).toBe(409);
});

test("the caller's own abort is not turned into an engine error", async () => {
  const ac = new AbortController();
  const f = ((url: string, init?: RequestInit) => new Promise((_res, rej) => {
    init?.signal?.addEventListener("abort", () => rej(new DOMException("aborted", "AbortError")));
    void url;
  })) as unknown as typeof fetch;
  const p = engineFetch(BASE, "/api/x", { signal: ac.signal }, { fetchImpl: f }).catch((x) => x);
  ac.abort();
  const e = await p;
  expect(e).not.toBeInstanceOf(EngineError);
  expect(e.name).toBe("AbortError");
});
