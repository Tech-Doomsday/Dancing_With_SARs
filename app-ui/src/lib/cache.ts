/** Read side of the Supabase verdict cache — transport only, plain fetch against PostgREST.
 *  Public read (RLS) with the anon key; the browser can never write: only the engine does.
 *  Not configured -> every call rejects with a clear message and the UI says so (no silent empty). */
import type { CachedSummary, CachedVerdict, FeaturedArea, LayerPayload } from "./types";

const URL_BASE = process.env.NEXT_PUBLIC_SUPABASE_URL?.replace(/\/$/, "");
const ANON_KEY = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

export const cacheConfigured = Boolean(URL_BASE && ANON_KEY);

const enc = encodeURIComponent;
const LIGHT =
  "tile,pipeline,bundle_id,epoch,status,tier,raw_p,summary,run_id,computed_at,first_computed_at,"
  + "center_lon,center_lat,bbox_lonlat";

/** Validate any value interpolated into a PostgREST filter: MGRS tile ids only, same grammar as
 *  engine/tiling/mgrs.py::_ID — then encodeURIComponent (defence in depth). */
export function assertTileId(tile: string): string {
  if (!/^\d{1,2}[C-X][A-HJ-NP-Z]{2}\d*$/.test(tile)) {
    throw new Error(`not an MGRS tile id: ${JSON.stringify(tile)}`);
  }
  return enc(tile);
}

async function rest<T>(query: string, table: "verdicts" | "featured_areas" = "verdicts"): Promise<T> {
  if (!URL_BASE || !ANON_KEY) {
    throw new Error("the Supabase cache is not configured (NEXT_PUBLIC_SUPABASE_URL / NEXT_PUBLIC_SUPABASE_ANON_KEY)");
  }
  const res = await fetch(`${URL_BASE}/rest/v1/${table}?${query}`, {
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

  /** Globe pins: newest cached verdicts per tile with their location (0002 columns). `Prefer:
   *  count=exact` returns the total in Content-Range so the UI can say "N of M". */
  pins: async (limit = 200): Promise<{ rows: CachedSummary[]; total: number | null }> => {
    const res = await fetch(
      `${URL_BASE}/rest/v1/verdicts?select=${LIGHT}&order=computed_at.desc&limit=${limit}`,
      {
        headers: {
          apikey: ANON_KEY as string,
          Authorization: `Bearer ${ANON_KEY}`,
          Prefer: "count=exact",
        },
        cache: "no-store",
      },
    );
    if (!res.ok) {
      const body = (await res.json().catch(() => ({}))) as { message?: string };
      throw new Error(`cache read failed (HTTP ${res.status})${body.message ? `: ${body.message}` : ""}`);
    }
    const rows = (await res.json()) as CachedSummary[];
    let total: number | null = null;
    const range = res.headers.get("Content-Range"); // e.g. "0-199/412"
    const m = range ? /\/(\d+|\*)$/.exec(range) : null;
    if (m) total = m[1] === "*" ? null : Number(m[1]);
    return { rows, total };
  },

  /** Every cached verdict for ONE tile (light columns; keyed by the server's tile id). */
  forTile: (tile: string) =>
    rest<CachedSummary[]>(
      `select=${LIGHT}&tile=eq.${assertTileId(tile)}&order=computed_at.desc`,
    ),

  /** The owner-curated featured list (DEC4; seeded empty — nothing is invented). */
  featured: () =>
    rest<FeaturedArea[]>(
      `select=slug,title,blurb,tile,center_lon,center_lat,zoom,sort_order,created_at`
      + `&order=sort_order.asc,slug.asc`,
      "featured_areas",
    ),
};
