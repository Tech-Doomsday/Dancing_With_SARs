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

/** fetch with the bearer header when a token is held. 401 -> GateError("unauthorized"); 503 whose
 *  engine text names NISAR_BACKGROUND_TOKEN -> GateError("unconfigured"); any other 503 ->
 *  EngineUnavailableError with the engine's verbatim text (not a gate problem). */
export async function bgFetch(path: string, init?: RequestInit & { signal?: AbortSignal }): Promise<Response> {
  const token = getToken();
  const headers = new Headers(init?.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  // paths are engine-relative ("/api/..."); resolve them against the engine in use
  const url = /^https?:\/\//i.test(path) ? path : `${engineBase()}${path}`;
  const res = await fetch(url, { ...init, headers, cache: "no-store" });
  if (res.status === 401) {
    throw new GateError("unauthorized", await engineText(res, "missing or wrong background token"));
  }
  if (res.status === 503) {
    const msg = await engineText(res, "the engine answered 503 (service unavailable)");
    if (msg.includes("NISAR_BACKGROUND_TOKEN")) throw new GateError("unconfigured", msg);
    throw new EngineUnavailableError(msg);
  }
  return res;
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
  if (body && typeof body === "object") {
    const b = body as Record<string, unknown>;
    if (typeof b.error === "string") return b.error;
    if (b.detail && typeof b.detail === "object") {
      const d = b.detail as Record<string, unknown>;
      if (typeof d.error === "string") return d.error;
    }
    if (typeof b.detail === "string") return b.detail;
  }
  return `${res.status} ${res.statusText}`;
}
