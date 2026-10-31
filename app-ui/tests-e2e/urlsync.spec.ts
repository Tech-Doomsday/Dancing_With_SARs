import { test, expect } from "@playwright/test";
import { createUrlWriter, pathInside, URL_WRITE_DELAY_MS, type UrlEnv } from "../src/lib/urlSync";

/** Deterministic unit tests for the URL write-back (no browser, no real timers).
 *  A fake clock + a fake window; `go()` plays the role of history.pushState / a link click. */

function fakeWindow(path: string, search = "") {
  let now = 0;
  let seq = 0;
  const timers = new Map<number, { at: number; fn: () => void }>();
  const state = { next: { marker: "next-internals" } };
  const calls: { data: unknown; url: string | null | undefined }[] = [];
  const loc = { pathname: path, search };
  const env: UrlEnv = {
    location: loc,
    history: {
      state,
      replaceState(data, _u, url) {
        calls.push({ data, url });
        if (url) {
          const [p, q = ""] = url.split("?");
          loc.pathname = p;
          loc.search = q ? `?${q}` : "";
        }
      },
    },
    setTimeout(fn, ms) { const id = ++seq; timers.set(id, { at: now + ms, fn }); return id; },
    clearTimeout(id) { timers.delete(id as number); },
  };
  return {
    env, calls, state,
    /** what a link click / history.pushState does */
    go(p: string, q = "") { loc.pathname = p; loc.search = q ? `?${q}` : ""; },
    advance(ms: number) {
      now += ms;
      for (const [id, t] of [...timers]) if (t.at <= now) { timers.delete(id); t.fn(); }
    },
    pending: () => timers.size,
    url: () => loc.pathname + loc.search,
  };
}

type P = { a?: number; b?: string };
const build = (cur: URLSearchParams, patch: Partial<P>) => {
  for (const [k, v] of Object.entries(patch)) {
    if (v === undefined) cur.delete(k); else cur.set(k, String(v));
  }
  return cur;
};
const make = (w: ReturnType<typeof fakeWindow>, prefix = "/map") =>
  createUrlWriter<P>({ pathPrefix: prefix, build, env: () => w.env });

test.describe("createUrlWriter", () => {
  test("(a) a write armed on /map is dropped when the user already left for another page", () => {
    const w = fakeWindow("/map", "?lat=1");
    const wr = make(w);
    wr.schedule({ a: 2 });
    expect(w.pending()).toBe(1);
    w.go("/background/retrieval", "x=1");             // the click inside the debounce window
    w.advance(URL_WRITE_DELAY_MS + 1);
    expect(w.calls).toHaveLength(0);                  // no replaceState
    expect(w.url()).toBe("/background/retrieval?x=1"); // and the URL is exactly what the user navigated to
  });

  test("(a2) still on /map: the write happens (guard does not block the normal case)", () => {
    const w = fakeWindow("/map", "?lat=1");
    make(w).schedule({ a: 2 });
    w.advance(URL_WRITE_DELAY_MS - 1);
    expect(w.calls).toHaveLength(0);                  // debounce respected
    w.advance(2);
    expect(w.calls).toHaveLength(1);
    expect(w.url()).toBe("/map?lat=1&a=2");
  });

  test("(b) dispose() cancels the pending write, and a disposed writer stays silent", () => {
    const w = fakeWindow("/map");
    const wr = make(w);
    wr.schedule({ a: 1 });
    wr.dispose();
    w.advance(URL_WRITE_DELAY_MS * 3);
    expect(w.calls).toHaveLength(0);
    wr.schedule({ a: 2 });                            // late async callback after unmount
    w.advance(URL_WRITE_DELAY_MS * 3);
    expect(w.calls).toHaveLength(0);
  });

  test("(c) two schedules inside the window make exactly one write with the merged patch", () => {
    const w = fakeWindow("/map", "?lat=1");
    const wr = make(w);
    wr.schedule({ a: 1, b: "x" });
    w.advance(100);
    wr.schedule({ a: 5 });                            // later key wins, earlier keys survive
    w.advance(100);
    wr.schedule({ b: undefined });                    // undefined clears a param
    w.advance(URL_WRITE_DELAY_MS + 1);
    expect(w.calls).toHaveLength(1);
    expect(w.url()).toBe("/map?lat=1&a=5");
  });

  test("(d) the existing history.state object is passed through unchanged (never null)", () => {
    const w = fakeWindow("/map");
    make(w).schedule({ a: 1 });
    w.advance(URL_WRITE_DELAY_MS + 1);
    expect(w.calls).toHaveLength(1);
    expect(w.calls[0].data).toBe(w.state);            // same reference
    expect(w.calls[0].data).not.toBeNull();
  });

  test("no write when the URL would not change (no history churn)", () => {
    const w = fakeWindow("/map", "?a=1");
    make(w).schedule({ a: 1 });
    w.advance(URL_WRITE_DELAY_MS + 1);
    expect(w.calls).toHaveLength(0);
  });

  test("flush() fires now but is still guarded by the pathname", () => {
    const w = fakeWindow("/map");
    const wr = make(w);
    wr.schedule({ a: 1 });
    wr.flush();
    expect(w.calls).toHaveLength(1);
    wr.schedule({ a: 2 });
    w.go("/background/overview");
    wr.flush();
    expect(w.calls).toHaveLength(1);
  });

  test("pathInside is boundary-aware", () => {
    expect(pathInside("/map", "/map")).toBe(true);
    expect(pathInside("/map/x", "/map")).toBe(true);
    expect(pathInside("/mapping", "/map")).toBe(false);
    expect(pathInside("/background/retrieval", "/background/retrieval")).toBe(true);
    expect(pathInside("/background/preprocessing", "/background/retrieval")).toBe(false);
  });
});
