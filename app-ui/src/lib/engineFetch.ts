/** engineFetch — the ONE place the UI talks to the engine over the network, and the one place a
 *  failed call is turned into words the user can act on. The browser only ever says
 *  "TypeError: Failed to fetch" for a dead tunnel, a refused connection, a CORS-less tunnel error
 *  page and a timeout alike; this module tells them apart and says what to do.
 *
 *  Kinds (EngineError.kind):
 *    unreachable  nothing answered (Colab runtime stopped / tunnel closed / wrong URL / CORS)
 *    timeout      no answer within the client time limit (default 25 s)
 *    busy         the request failed AND /api/health says the engine is running a retrieval
 *    http         the engine (or the tunnel in front of it) answered with an error status;
 *                 `message` is the engine's own text, verbatim
 *  A caller-initiated abort is NOT classified: the original AbortError is rethrown so existing
 *  `signal.aborted` checks keep working.
 *  Transport only; no science. `classifyEngineError` is pure (unit-tested). */
import { engineBase } from "./engineUrl";

export type EngineErrorKind = "unreachable" | "timeout" | "busy" | "http";

/** `busy` field of GET /api/health: what the retrieval worker is doing, or null when idle. */
export interface EngineBusy {
  kind: string;
  run_id?: string | null;
  step?: string | number | null;
  started_at?: string | number | null;
}

export const ENGINE_TIMEOUT_MS = 25_000;
/** Interim limit for calls whose engine route may still block longer than 25 s (bbox/tile granule
 *  search before it answers 202 + job id). Lower it to the default once the engine never holds a
 *  request open past 25 s. */
export const ENGINE_LONG_MS = 90_000;

export class EngineError extends Error {
  readonly kind: EngineErrorKind;
  readonly hint: string;
  readonly status?: number;
  readonly busy?: EngineBusy | null;
  constructor(kind: EngineErrorKind, message: string, hint: string, extra?: { status?: number; busy?: EngineBusy | null }) {
    super(message);
    this.name = "EngineError";
    this.kind = kind;
    this.hint = hint;
    this.status = extra?.status;
    this.busy = extra?.busy;
  }
  /** message + hint, for one-line displays */
  get full(): string {
    return this.hint ? `${this.message} ${this.hint}` : this.message;
  }
}

/** FastAPI wraps HTTPException payloads under `detail`; the engine's own convention nests
 *  {ok:false, error} there. Unwrap every shape; "" when the body carries no text. */
export function engineErrorText(body: unknown): string {
  if (typeof body === "string") return body.trim();
  if (body && typeof body === "object") {
    const b = body as Record<string, unknown>;
    if (typeof b.error === "string" && b.error) return b.error;
    if (b.detail && typeof b.detail === "object") {
      const d = b.detail as Record<string, unknown>;
      if (typeof d.error === "string" && d.error) return d.error;
    }
    if (typeof b.detail === "string" && b.detail) return b.detail;
  }
  return "";
}

function describeBusy(b: EngineBusy): string {
  const what = b.kind === "scan" ? "a scan" : b.kind === "retrieve" || b.kind === "retrieval" ? "a retrieval"
    : b.kind === "run" ? "a pipeline run" : `a ${b.kind} job`;
  const bits = [b.run_id ? String(b.run_id) : "", b.step !== undefined && b.step !== null && b.step !== "" ? `step ${b.step}` : ""]
    .filter(Boolean).join(", ");
  return `${what}${bits ? ` (${bits})` : ""}`;
}

export interface ClassifyInput {
  /** the thrown value from fetch (TypeError / AbortError / ...) — for transport failures */
  cause?: unknown;
  /** our own client timeout fired */
  timedOut?: boolean;
  timeoutMs?: number;
  /** an HTTP answer */
  status?: number;
  statusText?: string;
  /** parsed JSON body, or the raw text when it was not JSON */
  body?: unknown;
  /** what GET /api/health said right after the failure: its `busy` field, `undefined` when
   *  health did not answer (or predates the field) */
  health?: { busy?: EngineBusy | null } | null;
  /** engine base URL, for the message */
  base?: string;
}

/** Pure: a failure -> the EngineError the screen shows. Every message names what failed and
 *  what the user can do; none of them is a bare "Failed to fetch". */
export function classifyEngineError(i: ClassifyInput): EngineError {
  const where = i.base ? ` at ${i.base}` : "";
  const busy = i.health?.busy ?? null;
  if (i.status === undefined) {
    // transport failure: timeout / unreachable, upgraded to busy when health says so
    if (busy) {
      const secs = i.timedOut ? ` (no answer within ${Math.round((i.timeoutMs ?? ENGINE_TIMEOUT_MS) / 1000)} s)` : "";
      return new EngineError("busy",
        `The engine is busy with ${describeBusy(busy)}${secs}, so this request was not answered.`,
        "Nothing is lost — wait for it to finish (or cancel it), then try again.", { busy });
    }
    if (i.timedOut) {
      return new EngineError("timeout",
        `The engine${where} did not answer within ${Math.round((i.timeoutMs ?? ENGINE_TIMEOUT_MS) / 1000)} s.`,
        "It may be busy or the tunnel may be stalled. Check that the Colab serve cell is still running, then try again.");
    }
    return new EngineError("unreachable",
      `Can't reach the engine${where}.`,
      "The Colab runtime may have stopped or the tunnel closed. Re-run the Colab serve cell and paste the new URL into the engine pill; " +
      "if the URL is right, make sure this page is on localhost:3000 (the engine only allows that origin).");
  }
  const text = engineErrorText(i.body);
  const s = i.status;
  const tunnel = (s >= 520 && s <= 530) || s === 502 || s === 504;
  if (tunnel && (!text || /<\s*(html|!doctype)/i.test(text))) {
    return new EngineError("http",
      `The tunnel answered ${s}${i.statusText ? ` ${i.statusText}` : ""} instead of the engine.`,
      busy
        ? `The engine is busy with ${describeBusy(busy)}; wait for it, or re-run the Colab serve cell if it does not recover.`
        : "The engine is probably stopped or the tunnel closed. Re-run the Colab serve cell and paste the new URL into the engine pill.",
      { status: s, busy });
  }
  const msg = text || `${s}${i.statusText ? ` ${i.statusText}` : ""}`;
  const hint = s === 429 ? "Too many requests in a short time — wait a minute and try again."
    : s === 401 ? "Check the background token."
    : s >= 500 ? "This is an engine-side failure; the text above is the engine's own. See the Colab log for the traceback."
    : "";
  return new EngineError("http", msg, hint, { status: s });
}

/** GET /api/health with a short limit; resolves to its `busy` field wrapper, or undefined when
 *  health does not answer. Never throws. */
async function probeBusy(base: string, ms = 3000): Promise<{ busy?: EngineBusy | null } | undefined> {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), ms);
  try {
    const r = await fetch(`${base}/api/health`, { cache: "no-store", signal: ctrl.signal });
    if (!r.ok) return undefined;
    const j = (await r.json()) as { busy?: EngineBusy | null };
    return { busy: j?.busy ?? null };
  } catch {
    return undefined;
  } finally {
    clearTimeout(t);
  }
}

export interface EngineFetchOptions {
  /** client time limit (default ENGINE_TIMEOUT_MS) */
  timeoutMs?: number;
  /** override the engine base (default: the engine in use) */
  base?: string;
}

/** fetch against the engine. Returns the Response for ANY HTTP status (callers that need status
 *  handling, e.g. the background gate, keep it); throws EngineError for transport failures and
 *  timeouts. `path` is engine-relative ("/api/...") or an absolute URL. */
export async function engineFetch(
  path: string,
  init?: RequestInit & { signal?: AbortSignal | null },
  opts?: EngineFetchOptions,
): Promise<Response> {
  const base = opts?.base ?? engineBase();
  const url = /^https?:\/\//i.test(path) ? path : `${base}${path}`;
  const timeoutMs = opts?.timeoutMs ?? ENGINE_TIMEOUT_MS;
  const ctrl = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => { timedOut = true; ctrl.abort(); }, timeoutMs);
  const outer = init?.signal ?? undefined;
  const onOuter = () => ctrl.abort();
  if (outer) {
    if (outer.aborted) ctrl.abort();
    else outer.addEventListener("abort", onOuter, { once: true });
  }
  try {
    return await fetch(url, { cache: "no-store", ...init, signal: ctrl.signal });
  } catch (cause) {
    if (outer?.aborted && !timedOut) throw cause;          // the caller cancelled: not an engine failure
    const health = await probeBusy(base);
    throw classifyEngineError({ cause, timedOut, timeoutMs, health, base });
  } finally {
    clearTimeout(timer);
    outer?.removeEventListener("abort", onOuter);
  }
}

/** engineFetch + JSON, throwing EngineError (kind "http", the engine's own text) on non-2xx. */
export async function engineJson<T>(
  path: string,
  init?: RequestInit & { signal?: AbortSignal | null },
  opts?: EngineFetchOptions,
): Promise<T> {
  const res = await engineFetch(path, init, opts);
  const raw = await res.text().catch(() => "");
  let body: unknown = raw;
  try { body = raw ? JSON.parse(raw) : {}; } catch { /* non-JSON (tunnel page): keep the text */ }
  if (!res.ok) {
    const base = opts?.base ?? engineBase();
    const health = res.status >= 502 ? await probeBusy(base) : undefined;
    throw classifyEngineError({ status: res.status, statusText: res.statusText, body, health, base });
  }
  return body as T;
}
