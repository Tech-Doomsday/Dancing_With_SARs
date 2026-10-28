/** Read side of the Supabase verdict cache — transport only, plain fetch against PostgREST.
 *  Public read (RLS) with the anon key; the browser can never write: only the engine does.
 *  Not configured -> every call rejects with a clear message and the UI says so (no silent empty). */
import type { CachedSummary, CachedVerdict, LayerPayload } from "./types";

const URL_BASE = process.env.NEXT_PUBLIC_SUPABASE_URL?.replace(/\/$/, "");
const ANON_KEY = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

export const cacheConfigured = Boolean(URL_BASE && ANON_KEY);

const enc = encodeURIComponent;
const LIGHT =
  "tile,pipeline,bundle_id,epoch,status,tier,raw_p,summary,run_id,computed_at,first_computed_at";

async function rest<T>(query: string): Promise<T> {
  if (!URL_BASE || !ANON_KEY) {
    throw new Error("the Supabase cache is not configured (NEXT_PUBLIC_SUPABASE_URL / NEXT_PUBLIC_SUPABASE_ANON_KEY)");
  }
  const res = await fetch(`${URL_BASE}/rest/v1/verdicts?${query}`, {
    headers: { apikey: ANON_KEY, Authorization: `Bearer ${ANON_KEY}` },
    cache: "no-store",
  });
  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as { message?: string };
    throw new Error(`cache read failed (HTTP ${res.status})${body.message ? `: ${body.message}` : ""}`);
  }
  return res.json() as Promise<T>;
}

export const cache = {
  /** Newest cached verdicts first. */
  list: (limit = 50) => rest<CachedSummary[]>(`select=${LIGHT}&order=computed_at.desc&limit=${limit}`),

  /** Every cached verdict of one bundle, with its full result row (layer grids excluded). */
  forBundle: (bundleId: string) =>
    rest<CachedVerdict[]>(`select=${LIGHT},result&bundle_id=eq.${enc(bundleId)}&order=pipeline.asc`),

  /** One layer grid, fetched on demand (a single JSON path, not the whole layers column). */
  layer: async (bundleId: string, pipeline: string, name: string): Promise<LayerPayload> => {
    if (!/^[A-Za-z0-9_]+$/.test(name)) throw new Error(`invalid layer name ${JSON.stringify(name)}`);
    const rows = await rest<{ layer: LayerPayload | null }[]>(
      `select=layer:layers->${name}&bundle_id=eq.${enc(bundleId)}&pipeline=eq.${enc(pipeline)}&limit=1`,
    );
    const layer = rows[0]?.layer;
    if (!layer) throw new Error(`layer "${name}" is not in the cached verdict`);
    return layer;
  },
};
