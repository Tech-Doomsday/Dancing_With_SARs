/** One place that turns "the request did not work" into a sentence the user can act on.
 *  Never surfaces the browser's bare "Failed to fetch". Transport only — zero science.
 *  No imports on purpose: unit-testable in plain node. */

export type EngineErrorKind = "unreachable" | "busy" | "cut" | "timeout" | "http";

export interface BusyInfo { kind: string; run_id: string | null; step: string | null; started_at: number | null }

export class EngineError extends Error {
  kind: EngineErrorKind;
  status?: number;
  busy?: BusyInfo | null;
  constructor(kind: EngineErrorKind, message: string, extra?: { status?: number; busy?: BusyInfo | null }) {
    super(message);
    this.name = "EngineError";
    this.kind = kind;
    this.status = extra?.status;
    this.busy = extra?.busy;
  }
}

const hostOf = (base: string) => base.replace(/^https?:\/\//, "").replace(/\/$/, "") || "(no engine URL set)";

export function messageFor(kind: EngineErrorKind, ctx: { base: string; busy?: BusyInfo | null; seconds?: number; detail?: string }): string {
  switch (kind) {
    case "unreachable":
      return `Can't reach the engine at ${hostOf(ctx.base)}. The Colab session may have ended or the tunnel link changed. `
        + "Open the engine pill, paste the current tunnel URL, then try again.";
    case "busy": {
      const b = ctx.busy;
      const what = b ? `a ${b.kind}${b.run_id ? ` (${b.run_id})` : ""}${b.step ? `, currently at "${b.step}"` : ""}` : "a retrieval";
      return `The engine is busy with ${what}. It is alive and still working; this request did not get an answer. `
        + "Wait for it to finish, or open that run from its link to follow it.";
    }
    case "cut":
      return "The connection was cut while the engine was still answering (the engine is reachable). "
        + "Tunnels drop requests that stay open too long. Try again; work already fetched is cached and will be faster.";
    case "timeout":
      return `No answer from the engine after ${Math.round(ctx.seconds ?? 30)} s. The engine may be busy or the tunnel may be stalled. `
        + "Try again, or check the engine pill.";
    case "http":
      return ctx.detail ?? "The engine answered with an error.";
  }
}

/** Ask /api/health (answers even during a retrieval) why a request failed at the network level. */
async function probe(base: string, fetchImpl: typeof fetch): Promise<{ reachable: boolean; busy: BusyInfo | null }> {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), 4000);
  try {
    const res = await fetchImpl(`${base}/api/health`, { cache: "no-store", signal: ctrl.signal });
    if (!res.ok) return { reachable: false, busy: null };
    const j = await res.json().catch(() => ({}));
    return { reachable: true, busy: (j && j.busy) || null };
  } catch {
    return { reachable: false, busy: null };
  } finally {
    clearTimeout(t);
  }
}

export function unwrapDetail(body: unknown, res: { status: number; statusText: string }): string {
  if (body && typeof body === "object") {
    const b = body as Record<string, unknown>;
    if (typeof b.error === "string") return b.error;
    if (b.detail && typeof b.detail === "object") {
      const d = b.detail as Record<string, unknown>;
      if (typeof d.error === "string") return d.error;
    }
    if (typeof b.detail === "string") return b.detail;
  }
  return `${res.status} ${res.statusText}`.trim();
}

/** fetch with classification. Resolves with the Response (even for HTTP errors — callers decide);
 *  rejects with EngineError for network-level failures, or rethrows the caller's own AbortError. */
export async function engineFetch(
  base: string, path: string, init: RequestInit = {},
  opts: { timeoutMs?: number; fetchImpl?: typeof fetch } = {},
): Promise<Response> {
  const fetchImpl = opts.fetchImpl ?? fetch;
  const timeoutMs = opts.timeoutMs ?? 30_000;
  const ctrl = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => { timedOut = true; ctrl.abort(); }, timeoutMs);
  const outer = init.signal;
  const onOuter = () => ctrl.abort();
  if (outer) { if (outer.aborted) ctrl.abort(); else outer.addEventListener("abort", onOuter, { once: true }); }
  try {
    return await fetchImpl(`${base}${path}`, { ...init, signal: ctrl.signal });
  } catch (e) {
    if (outer?.aborted) throw e;                       // the caller cancelled: not an error to explain
    if (timedOut) {
      const p = await probe(base, fetchImpl);
      if (p.reachable && p.busy) throw new EngineError("busy", messageFor("busy", { base, busy: p.busy }), { busy: p.busy });
      throw new EngineError("timeout", messageFor("timeout", { base, seconds: timeoutMs / 1000 }));
    }
    const p = await probe(base, fetchImpl);
    if (!p.reachable) throw new EngineError("unreachable", messageFor("unreachable", { base }));
    if (p.busy) throw new EngineError("busy", messageFor("busy", { base, busy: p.busy }), { busy: p.busy });
    throw new EngineError("cut", messageFor("cut", { base }));
  } finally {
    clearTimeout(timer);
    outer?.removeEventListener("abort", onOuter);
  }
}
