/** Typed client for the engine API. All engine interaction goes through here. */
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

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { cache: "no-store" });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body?.error ?? `${res.status} ${res.statusText}`);
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
  if (!res.ok) throw new Error(json?.error ?? `${res.status} ${res.statusText}`);
  return json as T;
}

export const api = {
  health: () => get<Health>("/api/health"),
  pipelines: () => get<{ ok: true; pipelines: PipelineInfo[]; implemented_count: number }>("/api/pipelines"),
  bundles: () => get<{ ok: true; bundles: BundleListItem[] }>("/api/bundles"),
  events: (lon: number, lat: number, days = 30) =>
    get<{ ok: true; events: EventItem[]; source: string }>(
      `/api/events?lon=${lon}&lat=${lat}&days=${days}`,
    ),
  startRun: (bundleId: string, pipelines: string[], params?: Record<string, number>) =>
    post<{ ok: true; run_id: string }>("/api/runs", {
      bundle_id: bundleId,
      pipelines,
      params,
    }),
  runs: () => get<{ ok: true; runs: RunSummary[] }>("/api/runs"),
  run: (id: string) => get<{ ok: true; run: RunState }>(`/api/runs/${id}`),
  layer: (id: string, layer: string) =>
    get<LayerResponse>(`/api/runs/${id}/layers/${layer}`),
};

/** Self-scheduling poll: next request only after the previous settles (>=1.5 s).
 *  The pattern the reference app's tsunami panel proved under load. */
export function pollWhile<T>(
  fn: () => Promise<{ done: boolean; value: T }>,
  onTick: (value: T) => void,
  minIntervalMs = 1500,
): () => void {
  let stopped = false;
  const tick = async () => {
    while (!stopped) {
      const t0 = Date.now();
      try {
        const { done, value } = await fn();
        if (stopped) return;
        onTick(value);
        if (done) return;
      } catch {
        /* transient poll errors: keep the loop alive, the UI shows the last state */
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
