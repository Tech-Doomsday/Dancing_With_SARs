"use client";

/** Typed client for the GATED /api/background/* routes (UI-build-2 §4.5/§5/§6). Transport only.
 *  The bearer token rides the Authorization header (never a URL); preview PNGs are fetched as
 *  blobs for the same reason. Types mirror the engine shapes in lib/types.ts. */
import { bgGet } from "./bgApi";
import type {
  BackgroundGroup,
  ExplorerGranule,
  GranuleDetailResponse,
  PipelineDetailResponse,
  PreprocessingResponse,
  PreviewMeta,
  RunSummary,
  StackPairResponse,
} from "./types";

export type { GateError } from "./bgApi";

export const bg = {
  granule: (id: string, signal?: AbortSignal) =>
    bgGet<GranuleDetailResponse>(
      `/api/background/granule?id=${encodeURIComponent(id)}`, signal),

  /** The PNG itself: fetched as a blob (Authorization header, never a URL token). */
  previewBlob: async (
    p: { id: string; layer: string; west: number; south: number; east: number; north: number;
         maxPx?: number; cmap?: string; scale?: string; format?: "json" },
    signal?: AbortSignal,
  ): Promise<{ blob: Blob | null; meta: PreviewMeta }> => {
    const q = new URLSearchParams({
      id: p.id, layer: p.layer,
      west: String(p.west), south: String(p.south), east: String(p.east), north: String(p.north),
      max_px: String(p.maxPx ?? 256), cmap: p.cmap ?? "auto", scale: p.scale ?? "auto",
    });
    if (p.format) q.set("format", p.format);
    const { bgFetch } = await import("./bgApi");
    const res = await bgFetch(`/api/background/preview?${q.toString()}`, { signal });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(
        (body as { detail?: { error?: string }; error?: string })?.detail?.error
        ?? (body as { error?: string })?.error
        ?? `${res.status} ${res.statusText}`);
    }
    if (p.format === "json") {
      const body = (await res.json()) as { meta: PreviewMeta };
      return { blob: null, meta: body.meta };
    }
    const meta = JSON.parse(res.headers.get("X-Img-Meta") ?? "{}") as PreviewMeta;
    return { blob: await res.blob(), meta };
  },

  probe: (p: { id: string; layer: string; lon: number; lat: number }, signal?: AbortSignal) =>
    bgGet<{ ok: true; value: number | null; finite: boolean; row: number; col: number;
            epsg: number; x: number; y: number; lon: number; lat: number; dtype: string }>(
      `/api/background/probe?id=${encodeURIComponent(p.id)}&layer=${encodeURIComponent(p.layer)}`
      + `&lon=${p.lon}&lat=${p.lat}`, signal),

  stackPair: (id: string, signal?: AbortSignal) =>
    bgGet<StackPairResponse>(`/api/background/stack-pair?id=${encodeURIComponent(id)}`, signal),

  runs: () => bgGet<{ ok: true; runs: RunSummary[] }>("/api/runs"),

  preprocessing: (runId: string, bundleId?: string, signal?: AbortSignal) =>
    bgGet<PreprocessingResponse>(
      `/api/background/runs/${encodeURIComponent(runId)}/preprocessing`
      + (bundleId ? `?bundle_id=${encodeURIComponent(bundleId)}` : ""), signal),

  traceArrayUrl: (runId: string, aid: string, opts?: { cmap?: string; lo?: number; hi?: number; maxPx?: number }) => {
    const q = new URLSearchParams();
    if (opts?.cmap) q.set("cmap", opts.cmap);
    if (opts?.lo != null) q.set("lo", String(opts.lo));
    if (opts?.hi != null) q.set("hi", String(opts.hi));
    if (opts?.maxPx) q.set("max_px", String(opts.maxPx));
    const qs = q.toString();
    return `/api/background/runs/${encodeURIComponent(runId)}/arrays/${encodeURIComponent(aid)}${qs ? `?${qs}` : ""}`;
  },

  traceProbe: (runId: string, aid: string, fx: number, fy: number, signal?: AbortSignal) =>
    bgGet<{ ok: true; row: number; col: number; stride: number; value: number | null;
            finite: boolean; lon?: number; lat?: number; epsg?: number }>(
      `/api/background/runs/${encodeURIComponent(runId)}/arrays/${encodeURIComponent(aid)}`
      + `/probe?fx=${fx}&fy=${fy}`, signal),

  pipelineDetail: (runId: string, name: string, signal?: AbortSignal) =>
    bgGet<PipelineDetailResponse>(
      `/api/background/runs/${encodeURIComponent(runId)}/pipelines/${encodeURIComponent(name)}`,
      signal),
};

/** groups helpers (display-only pure logic lives in lib/groups.ts) */
export function groupTimes(g: BackgroundGroup): { start: number; end: number } | null {
  const a = g.ref_time ?? g.start;
  const b = g.sec_time ?? g.start;
  const t0 = a ? Date.parse(a) : NaN;
  const t1 = b ? Date.parse(b) : NaN;
  if (!Number.isFinite(t0)) return null;
  return { start: Math.min(t0, Number.isFinite(t1) ? t1 : t0), end: Math.max(t0, Number.isFinite(t1) ? t1 : t0) };
}

export function groupOfGranule(groups: BackgroundGroup[] | undefined, id: string): BackgroundGroup | null {
  if (!groups) return null;
  return groups.find((g) => g.granule_ids.includes(id)) ?? null;
}

export type { ExplorerGranule, BackgroundGroup };
