/** Typed client for the engine API. All engine interaction goes through here.
 *  Zero science: transport only. */
import type {
  BundleListItem,
  Health,
  LayerResponse,
  PipelineInfo,
  RunState,
  RunSummary,
  EventItem,
} from "./types";

const BASE = process.env.NEXT_PUBLIC_ENGINE_API ?? "http://127.0.0.1:8900";

/** FastAPI wraps HTTPException payloads under `detail`; the engine's own error
 *  convention nests {ok: false, error} there. Unwrap every shape in one place. */
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

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { cache: "no-store" });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(extractError(body, res));
  }
  return res.json() as Promise<T>;
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const json = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(extractError(json, res));
  return json as T;
}

export const api = {
  health: () => get<Health>("/api/health"),
  pipelines: () =>
    get<{ ok: true; pipelines: PipelineInfo[]; implemented_count: number }>("/api/pipelines"),
  bundles: () => get<{ ok: true; bundles: BundleListItem[] }>("/api/bundles"),
  events: (lon: number, lat: number, days = 30) =>
    get<{ ok: true; events: EventItem[]; source: string }>(
      `/api/events?lon=${lon}&lat=${lat}&days=${days}`,
    ),
  startRun: (bundleId: string, pipelines: string[], params?: Record<string, number>) =>
    post<{ ok: true; run_id: string }>("/api/runs", {
      bundle_id: bundleId,
      pipelines,
      params: params ?? {},
    }),
  runs: () => get<{ ok: true; runs: RunSummary[] }>("/api/runs"),
  /** The engine returns the run state FLAT: {ok, id, status, progress, error, results}. */
  run: (id: string) => get<RunState & { ok: true }>(`/api/runs/${id}`),
  layer: (id: string, layer: string) =>
    get<LayerResponse>(`/api/runs/${id}/layers/${layer}`),
};

/** Self-scheduling poll: next request only after the previous settles (>=1.5 s).
 *  Poll errors are SURFACED through onError (the old loop swallowed them, which left
 *  the run page stuck on "Loading run…" when the API said 404/500). */
export function pollWhile<T>(
  fn: () => Promise<{ done: boolean; value: T }>,
  onTick: (value: T) => void,
  minIntervalMs = 1500,
  onError?: (message: string) => void,
): () => void {
  let stopped = false;
  const tick = async () => {
    while (!stopped) {
      const t0 = Date.now();
      try {
        const { done, value } = await fn();
        if (stopped) return;
        onTick(value);
        onError?.("");
        if (done) return;
      } catch (e) {
        if (stopped) return;
        onError?.(e instanceof Error ? e.message : String(e));
      }
      const wait = Math.max(minIntervalMs - (Date.now() - t0), minIntervalMs);
      await new Promise((r) => setTimeout(r, wait));
    }
  };
  void tick();
  return () => {
    stopped = true;
  };
}
