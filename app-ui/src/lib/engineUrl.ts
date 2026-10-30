"use client";

import { useSyncExternalStore } from "react";

/** Engine URL override — lets the person paste a (Colab/cloudflared) engine address into the UI
 *  instead of editing .env.local and restarting `npm run dev`. Transport config only, no science.
 *
 *  Precedence: stored override (localStorage) > NEXT_PUBLIC_ENGINE_API > http://127.0.0.1:8900.
 *  localStorage (not sessionStorage) on purpose: the last pasted URL survives a tab close; a new
 *  Colab session just means pasting the new one. All storage access is try/catch-guarded, so
 *  private mode degrades to "override lasts until reload is impossible" = default engine. */

const KEY = "nisar_engine_url";
const EVENT = "nisar-engine-url-change";

export const DEFAULT_ENGINE: string = (
  process.env.NEXT_PUBLIC_ENGINE_API ?? "http://127.0.0.1:8900"
).replace(/\/+$/, "");

/** Turn whatever got pasted into a clean origin-style base URL, or explain why not.
 *  Accepts: a bare URL, the Colab cell's `NEXT_PUBLIC_ENGINE_API=https://…` line (quotes ok),
 *  a missing scheme, trailing slashes, and a pasted `/api` or `/api/health` tail. */
export function normalizeEngineUrl(
  raw: string,
): { ok: true; url: string } | { ok: false; error: string } {
  let s = raw.trim();
  if (!s) return { ok: false, error: "Paste the engine URL first." };
  s = s.replace(/^(export\s+)?NEXT_PUBLIC_ENGINE_API\s*=\s*/i, "");
  s = s.trim().replace(/^["']|["']$/g, "").trim();
  if (/\s/.test(s)) return { ok: false, error: "That has spaces in it — paste just the URL." };
  if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(s)) {
    const local = /^(localhost|127\.0\.0\.1|\[::1\])(:\d+)?(\/|$)/i.test(s);
    s = `${local ? "http" : "https"}://${s}`;
  }
  let u: URL;
  try {
    u = new URL(s);
  } catch {
    return { ok: false, error: "That doesn't look like a URL." };
  }
  if (u.protocol !== "https:" && u.protocol !== "http:") {
    return { ok: false, error: "The URL must start with https:// (or http:// for localhost)." };
  }
  const isLocal = ["localhost", "127.0.0.1", "[::1]"].includes(u.hostname);
  if (u.protocol === "http:" && !isLocal) {
    return { ok: false, error: "Remote engines are served over https — use the https:// URL." };
  }
  let path = u.pathname.replace(/\/+$/, "");
  path = path.replace(/\/api(\/health)?$/i, "");
  return { ok: true, url: `${u.origin}${path}` };
}

/** The engine base in effect right now. Client: stored override or default. Server: default. */
export function engineBase(): string {
  if (typeof window === "undefined") return DEFAULT_ENGINE;
  try {
    const v = window.localStorage.getItem(KEY);
    if (v) return v;
  } catch {
    /* storage disabled */
  }
  return DEFAULT_ENGINE;
}

export function hasEngineOverride(): boolean {
  return engineBase() !== DEFAULT_ENGINE;
}

function notify(): void {
  try {
    window.dispatchEvent(new Event(EVENT));
  } catch {
    /* ignore */
  }
}

/** Persist a (normalised) URL. Returns false when storage is unavailable. */
export function setEngineBase(url: string): boolean {
  try {
    if (url === DEFAULT_ENGINE) window.localStorage.removeItem(KEY);
    else window.localStorage.setItem(KEY, url);
  } catch {
    return false;
  }
  notify();
  return true;
}

export function resetEngineBase(): void {
  try {
    window.localStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
  notify();
}

export function subscribeEngine(cb: () => void): () => void {
  const onStorage = (e: StorageEvent) => {
    if (e.key === KEY || e.key === null) cb();
  };
  window.addEventListener(EVENT, cb);
  window.addEventListener("storage", onStorage); // another tab changed it
  return () => {
    window.removeEventListener(EVENT, cb);
    window.removeEventListener("storage", onStorage);
  };
}

export interface EngineProbe {
  ok: boolean;
  /** Human-readable reason when !ok, or a one-line summary when ok. */
  message: string;
  scansEnabled?: boolean;
  earthdata?: string;
}

/** GET <base>/api/health with a timeout. Never throws. */
export async function probeEngine(base: string, timeoutMs = 8000): Promise<EngineProbe> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(`${base}/api/health`, { cache: "no-store", signal: ctrl.signal });
    if (!res.ok) {
      const tunnelDead = res.status >= 520 && res.status <= 530;
      return {
        ok: false,
        message: tunnelDead
          ? `The tunnel answered ${res.status} — it's probably closed. Re-run the serve cell on Colab and paste the new URL.`
          : `The server answered ${res.status} ${res.statusText}. Is that the engine URL?`,
      };
    }
    const body = (await res.json().catch(() => null)) as Record<string, unknown> | null;
    if (!body || typeof body !== "object" || !("ok" in body)) {
      return { ok: false, message: "It answered, but not like the engine (no health JSON)." };
    }
    const scans = body.scans_enabled === true;
    const ed = typeof body.earthdata_credentials === "string" ? body.earthdata_credentials : undefined;
    return {
      ok: body.ok === true,
      scansEnabled: scans,
      earthdata: ed,
      message: `engine online · ${scans ? "scans enabled" : "scans disabled"}${ed ? ` · Earthdata ${ed}` : ""}`,
    };
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") {
      return { ok: false, message: `No answer within ${Math.round(timeoutMs / 1000)} s — the engine or tunnel is down.` };
    }
    return {
      ok: false,
      message:
        "Couldn't reach it. Check the URL, that the Colab serve cell is still running, and that this page " +
        "is on localhost:3000 or 127.0.0.1:3000 (other origins are blocked by the engine's CORS list).",
    };
  } finally {
    clearTimeout(timer);
  }
}

/** React hook: the engine base, re-rendering on change. The server snapshot is the build-time
 *  default, so hydration matches; the stored override is applied right after mount. */
export function useEngineUrl(): string {
  return useSyncExternalStore(subscribeEngine, engineBase, () => DEFAULT_ENGINE);
}
