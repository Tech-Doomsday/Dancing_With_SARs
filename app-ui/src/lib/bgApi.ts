"use client";

/** Background token gate (UI-build-2 §1). The ENGINE enforces the gate (every /api/background/*
 *  route requires Authorization: Bearer <NISAR_BACKGROUND_TOKEN>; unset env → 503, wrong → 401).
 *  This module is the UI half: it stores the token ONCE per session in sessionStorage (guarded
 *  with try/catch — private-mode failures degrade to asking every load), never puts it in a URL,
 *  never logs it, and maps the GATE's answers to typed errors the Background pages render as the
 *  gate prompt: 401 -> GateError("unauthorized"); 503 naming NISAR_BACKGROUND_TOKEN ->
 *  GateError("unconfigured"). Any OTHER 503 (e.g. the engine's "no Earthdata credentials") is not a
 *  gate problem: it becomes an EngineUnavailableError carrying the engine's text verbatim.
 *
 *  This is a STOPGAP: real authentication is an owner action (see the docs). */

import { engineBase } from "./engineUrl";
import { engineErrorText, engineFetch, ENGINE_LONG_MS, ENGINE_TIMEOUT_MS } from "./engineFetch";
import { bgWaitEnd, bgWaitStart, bgWaitUpdate } from "./bgWait";

const KEY = "nisar_bg_token";

export class GateError extends Error {
  readonly kind: "unauthorized" | "unconfigured";
  constructor(kind: "unauthorized" | "unconfigured", message: string) {
    super(message);
    this.kind = kind;
  }
}

/** A 503 that is NOT the gate (e.g. no Earthdata credentials on the engine). Never a GateError, so
 *  no token prompt: the views show `message` — the engine's own `detail.error` — in their normal
 *  error state. */
export class EngineUnavailableError extends Error {
  readonly status = 503;
  constructor(message: string) {
    super(message);
    this.name = "EngineUnavailableError";
  }
}

async function engineText(res: Response, fallback: string): Promise<string> {
  try {
    const body = await res.json();
    const t = body?.detail?.error ?? body?.error;
    return typeof t === "string" && t ? t : fallback;
  } catch {
    return fallback;
  }
}

export function getToken(): string {
  try {
    return window.sessionStorage.getItem(KEY) ?? "";
  } catch {
    return "";
  }
}

export function setToken(token: string): void {
  try {
    if (token) window.sessionStorage.setItem(KEY, token);
    else window.sessionStorage.removeItem(KEY);
  } catch {
    /* private mode / storage disabled: the token simply is not persisted */
  }
}

/** 401 -> GateError("unauthorized"); 503 naming NISAR_BACKGROUND_TOKEN -> GateError("unconfigured"); any other 503 ->
 *  EngineUnavailableError with the engine's verbatim text. Anything else passes. */
async function gateCheck(res: Response): Promise<void> {
  if (res.status === 401) {
    throw new GateError("unauthorized", await engineText(res.clone(), "missing or wrong background token"));
  }
  if (res.status === 503) {
    const msg = await engineText(res.clone(), "the engine answered 503 (service unavailable)");
    if (msg.includes("NISAR_BACKGROUND_TOKEN")) throw new GateError("unconfigured", msg);
    throw new EngineUnavailableError(msg);
  }
}

/** a read the engine could not finish inside its answer window: 202 + a job id (see engine/api/bg_jobs.py) */
interface JobInfo {
  pending: true;
  job_id: string;
  attempt?: number;
  last_error?: string | null;
  retry_in_s?: number | null;
  poll_interval_s?: number;
  retry_backoff_s?: number[];
}

function isJob(b: unknown): b is JobInfo {
  return !!b && typeof b === "object" && (b as JobInfo).pending === true && typeof (b as JobInfo).job_id === "string";
}

function abortError(): Error {
  return typeof DOMException !== "undefined" ? new DOMException("aborted", "AbortError") : Object.assign(new Error("aborted"), { name: "AbortError" });
}

function sleep(ms: number, signal?: AbortSignal | null): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) { reject(abortError()); return; }
    const t = setTimeout(() => { signal?.removeEventListener("abort", onAbort); resolve(); }, ms);
    const onAbort = () => { clearTimeout(t); reject(abortError()); };
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

/** fetch with the bearer header when a token is held. 401 -> GateError("unauthorized"); 503 whose
 *  engine text names NISAR_BACKGROUND_TOKEN -> GateError("unconfigured"); any other 503 ->
 *  EngineUnavailableError with the engine's verbatim text (not a gate problem).
 *
 *  NO TIME LIMIT on a read from NASA: when the engine answers 202 + job id (the read is still going), this keeps
 *  asking for the answer until there is one — through slow providers, engine-side retries and tunnel blips — and returns
 *  the same Response the route would have sent. It stops only when the caller aborts (the engine is told to cancel). */
export async function bgFetch(path: string, init?: RequestInit & { signal?: AbortSignal },
                              opts?: { timeoutMs?: number }): Promise<Response> {
  const token = getToken();
  const headers = new Headers(init?.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  // paths are engine-relative ("/api/..."); resolve them against the engine in use
  const url = /^https?:\/\//i.test(path) ? path : `${engineBase()}${path}`;
  // transport failures / timeouts become an EngineError that says what failed and what to do. The limit covers ONE
  // request (the engine answers within its window or hands back a job id), never the read itself.
  const res = await engineFetch(url, { ...init, headers }, { timeoutMs: opts?.timeoutMs ?? ENGINE_LONG_MS });
  await gateCheck(res);
  if (res.status !== 202 || !path.startsWith("/api/background/")) return res;
  const first: unknown = await res.clone().json().catch(() => null);
  if (!isJob(first)) return res;
  return awaitJob(path, init, opts, headers, first);
}

async function awaitJob(path: string, init: (RequestInit & { signal?: AbortSignal }) | undefined,
                        opts: { timeoutMs?: number } | undefined, headers: Headers, first: JobInfo): Promise<Response> {
  const signal = init?.signal;
  const jobUrl = `${engineBase()}/api/background/jobs/${encodeURIComponent(first.job_id)}`;
  const wid = bgWaitStart(path);
  let info: JobInfo = first;
  let linkFails = 0;
  let linkError: string | null = null;
  try {
    for (;;) {
      bgWaitUpdate(wid, info, linkError);
      await sleep(Math.max(250, (info.poll_interval_s ?? 1) * 1000), signal);
      let r: Response;
      try {
        r = await engineFetch(jobUrl, { headers, signal }, { timeoutMs: ENGINE_TIMEOUT_MS });
      } catch (err) {
        if (signal?.aborted) throw err;
        // this poll got no answer (tunnel blip, engine busy): the read is still running on the engine — keep asking
        linkFails += 1;
        linkError = err instanceof Error ? err.message : String(err);
        const steps = info.retry_backoff_s?.length ? info.retry_backoff_s : [5];
        await sleep(steps[Math.min(linkFails - 1, steps.length - 1)] * 1000, signal);
        continue;
      }
      linkFails = 0;
      linkError = null;
      await gateCheck(r);
      if (r.status === 202) {
        const b: unknown = await r.json().catch(() => null);
        if (isJob(b)) info = b;
        continue;
      }
      if (r.status === 404) {
        const msg = engineErrorText(await r.clone().json().catch(() => ({})));
        if (msg.includes("unknown or expired background job")) {   // the engine restarted / forgot it: ask again, still no limit
          bgWaitEnd(wid);
          return bgFetch(path, init, opts);
        }
      }
      return r;                                                    // 200 = the route's own body; an error keeps the engine's own text
    }
  } catch (err) {
    if (signal?.aborted) {
      void fetch(jobUrl, { method: "DELETE", headers, keepalive: true }).catch(() => undefined);   // best effort: free the engine
    }
    throw err;
  } finally {
    bgWaitEnd(wid);
  }
}

/** bgFetch + the engine's {ok:false,error} envelope unwrap (same shapes as lib/api.ts). */
export async function bgGet<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await bgFetch(path, { signal });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(extractError(body, res));
  }
  return res.json() as Promise<T>;
}

function extractError(body: unknown, res: Response): string {
  return engineErrorText(body) || `${res.status} ${res.statusText}`;
}
