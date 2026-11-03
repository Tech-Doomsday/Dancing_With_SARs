"use client";

/** AreasPanel (Explore state) — Events | Featured | Cached tabs. Events = the post-launch disaster list, ranked by how
 *  trustworthy the map point is and how much SAR can say (src/lib/events.ts). Featured rows come from the owner's
 *  featured_areas table (seeded EMPTY — the empty state says exactly that and how to fill it),
 *  joined to cached rows by tile. Cached rows are grouped by tile with the newest bundle current.
 *  Empty/unconfigured/error states are honest and specific. */
import { useMemo, useState } from "react";
import Link from "next/link";
import { timeAgo } from "@/lib/format";
import { mostNotableTier, TIER_PIN_COLORS } from "@/lib/status";
import type { CachedSummary, FeaturedArea } from "@/lib/types";
import { rankEvents, type DisasterEvent, type RankedEvent } from "@/lib/events";
import disasters from "@/lib/disasters.json";

const EVENTS = disasters as unknown as DisasterEvent[];

export interface AreasPanelProps {
  featured: FeaturedArea[] | null;
  cachedRows: CachedSummary[] | null;
  cachedTotal: number | null;
  cacheError: string | null;
  cacheConfigured: boolean;
  onSelectTile: (tile: string, lon: number, lat: number, zoom?: number) => void;
  onOpenResult: (bundleId: string) => void;
  onScan: (tile: string, lon: number, lat: number) => void;
  /** fly to a point and open the Pin panel there (Events tab) */
  onSelectPoint: (lat: number, lon: number, zoom: number) => void;
  selectedTile: string | null;
}

interface TileGroup {
  tile: string;
  rows: CachedSummary[];
  current: CachedSummary;
  earlier: CachedSummary[];
  center: [number, number] | null;
}

function groupByTile(rows: CachedSummary[]): TileGroup[] {
  const by = new Map<string, CachedSummary[]>();
  for (const r of rows) {
    const list = by.get(r.tile) ?? [];
    list.push(r);
    by.set(r.tile, list);
  }
  const out: TileGroup[] = [];
  for (const [tile, list] of by) {
    const sorted = [...list].sort((a, b) => (a.computed_at < b.computed_at ? 1 : -1));
    const current = sorted[0];
    const withLoc = sorted.find((r) => r.center_lon != null && r.center_lat != null);
    out.push({
      tile,
      rows: sorted,
      current,
      earlier: sorted.slice(1),
      center: withLoc ? [withLoc.center_lon as number, withLoc.center_lat as number] : null,
    });
  }
  return out.sort((a, b) => (a.current.computed_at < b.current.computed_at ? 1 : -1));
}

export default function AreasPanel(props: AreasPanelProps) {
  const [tab, setTab] = useState<"events" | "featured" | "cached">("events");
  const [filter, setFilter] = useState("");
  const [tierFilter, setTierFilter] = useState<string>("");

  const groups = useMemo(() => (props.cachedRows ? groupByTile(props.cachedRows) : []), [props.cachedRows]);
  const tiers = useMemo(() => Array.from(new Set(groups.map((g) => mostNotableTier(g.rows.map((r) => r.tier))))), [groups]);
  const filtered = groups.filter(
    (g) =>
      (!tierFilter || mostNotableTier(g.rows.map((r) => r.tier)) === tierFilter) &&
      (!filter || g.tile.toLowerCase().includes(filter.toLowerCase())),
  );
  const cachedByTile = useMemo(() => {
    const m = new Map<string, TileGroup>();
    for (const g of groups) m.set(g.tile, g);
    return m;
  }, [groups]);

  return (
    <div className="p-3">
      <div className="flex gap-1" role="tablist" aria-label="Explore lists">
        {(["events", "featured", "cached"] as const).map((t) => (
          <button
            key={t}
            role="tab"
            aria-selected={tab === t}
            onClick={() => setTab(t)}
            className={`rounded-full px-3 py-1 text-xs font-medium capitalize ${
              tab === t ? "bg-[#2c5f58] text-white" : "border border-[#e7e2da] bg-white text-[#4a443c] hover:border-[#2c5f58]"
            }`}
          >
            {t}
          </button>
        ))}
      </div>

      {tab === "events" && <EventsList onSelectPoint={props.onSelectPoint} />}

      {tab === "featured" && (
        <div className="mt-3 space-y-2">
          {!props.cacheConfigured && (
            <p role="status" className="rounded-lg border border-[#b08d57]/40 bg-[#f7f2e7] px-3 py-2 text-[11px] text-[#7a6438]">
              The Supabase cache is not configured in this UI (NEXT_PUBLIC_SUPABASE_URL /
              NEXT_PUBLIC_SUPABASE_ANON_KEY) — featured and cached areas cannot load.
            </p>
          )}
          {props.cacheConfigured && props.cacheError && (
            <p role="alert" className="mono rounded-lg border border-[#9e3b2c] bg-[#fbf3ef] px-3 py-2 text-[11px] text-[#9e3b2c]">
              {props.cacheError}
            </p>
          )}
          {props.cacheConfigured && props.featured && props.featured.length === 0 && !props.cacheError && (
            <p className="rounded-lg border border-[#e7e2da] bg-white px-3 py-2 text-[11px] text-[#8a837b]">
              No featured areas have been added yet — the owner curates this list with
              <span className="mono"> tools/feature_area.py</span>; nothing is invented here.
            </p>
          )}
          {props.featured?.map((f) => {
            const group = cachedByTile.get(f.tile);
            return (
              <div key={f.slug} className="rounded-lg border border-[#e7e2da] bg-white p-2.5">
                <div className="flex items-center justify-between gap-2">
                  <button
                    onClick={() => props.onSelectTile(f.tile, f.center_lon, f.center_lat, f.zoom ?? undefined)}
                    className={`text-left text-sm font-semibold hover:text-[#2c5f58] ${
                      props.selectedTile === f.tile ? "text-[#2c5f58]" : "text-[#1c1a17]"
                    }`}
                  >
                    {f.title}
                  </button>
                  <span className="mono text-[10px] text-[#8a837b]">{f.tile}</span>
                </div>
                {f.blurb && <p className="mt-0.5 text-[11px] text-[#4a443c]">{f.blurb}</p>}
                {group ? (
                  <div className="mt-1.5 flex items-center gap-2">
                    <span
                      className="inline-block h-2 w-2 rounded-full"
                      style={{ background: TIER_PIN_COLORS[mostNotableTier(group.rows.map((r) => r.tier))] }}
                      aria-hidden
                    />
                    <span className="text-[11px] text-[#4a443c]">
                      {group.current.status.replace(/_/g, " ").toLowerCase()}
                      {" · "}
                      {timeAgo(group.current.computed_at)}
                      {group.earlier.length > 0 && ` · ${group.earlier.length} earlier scan(s)`}
                    </span>
                    <button
                      onClick={() => props.onOpenResult(group.current.bundle_id)}
                      className="ml-auto rounded-md border border-[#2c5f58] px-2 py-0.5 text-[11px] text-[#2c5f58] hover:bg-[#eff5f3]"
                    >
                      open result
                    </button>
                  </div>
                ) : (
                  <div className="mt-1.5 flex items-center gap-2">
                    <span className="text-[11px] text-[#8a837b]">not scanned yet</span>
                    <button
                      onClick={() => props.onScan(f.tile, f.center_lon, f.center_lat)}
                      className="ml-auto rounded-md border border-[#2c5f58] px-2 py-0.5 text-[11px] text-[#2c5f58] hover:bg-[#eff5f3]"
                    >
                      Scan
                    </button>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {tab === "cached" && (
        <div className="mt-3">
          {props.cacheConfigured && props.cachedRows && (
            <div className="mb-2 flex flex-wrap items-center gap-1.5">
              <input suppressHydrationWarning
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                placeholder="filter by tile id"
                aria-label="Filter cached tiles by id"
                className="mono w-32 rounded-md border border-[#e7e2da] px-2 py-1 text-[11px]"
              />
              {tiers.map((t) => (
                <button
                  key={t}
                  onClick={() => setTierFilter(tierFilter === t ? "" : t)}
                  aria-pressed={tierFilter === t}
                  className={`rounded-full border px-2 py-0.5 text-[10px] ${
                    tierFilter === t ? "border-[#2c5f58] bg-[#eff5f3] text-[#2c5f58]" : "border-[#e7e2da] text-[#4a443c]"
                  }`}
                >
                  {t}
                </button>
              ))}
              {props.cachedTotal != null && (
                <span className="ml-auto text-[10px] text-[#8a837b]">
                  {props.cachedRows.length} of {props.cachedTotal} cached verdicts
                </span>
              )}
            </div>
          )}
          {props.cacheConfigured && props.cachedRows && props.cachedRows.length === 0 && !props.cacheError && (
            <p className="rounded-lg border border-[#e7e2da] bg-white px-3 py-2 text-[11px] text-[#8a837b]">
              Nothing is cached yet — the cache fills when the engine finishes scans (engine-default
              runs only).
            </p>
          )}
          {filtered.map((g) => {
            const tier = mostNotableTier(g.rows.map((r) => r.tier));
            const center = g.center;
            return (
              <div key={g.tile} className="mb-2 rounded-lg border border-[#e7e2da] bg-white p-2.5">
                <div className="flex items-center justify-between gap-2">
                  <button
                    onClick={() =>
                      center
                        ? props.onSelectTile(g.tile, center[0], center[1])
                        : props.onSelectTile(g.tile, Number.NaN, Number.NaN)
                    }
                    className={`mono text-sm font-semibold hover:text-[#2c5f58] ${
                      props.selectedTile === g.tile ? "text-[#2c5f58]" : "text-[#1c1a17]"
                    }`}
                  >
                    {g.tile}
                  </button>
                  <span
                    className="inline-block h-2.5 w-2.5 rounded-full border border-white shadow"
                    style={{ background: TIER_PIN_COLORS[tier] }}
                    title={`tier: ${tier}`}
                    aria-hidden
                  />
                </div>
                <p className="mt-0.5 text-[11px] text-[#4a443c]">
                  {g.current.status.replace(/_/g, " ").toLowerCase()}
                  {g.current.center_lon != null && g.current.center_lat != null && (
                    <span className="mono"> · ({g.current.center_lon.toFixed(3)}, {g.current.center_lat.toFixed(3)})</span>
                  )}
                  {" · "}
                  {timeAgo(g.current.computed_at)}
                </p>
                <p className="mt-0.5 text-[11px] text-[#8a837b]">
                  {g.rows.length} pipeline verdict(s)
                </p>
                {g.earlier.length > 0 && (
                  <details className="mt-1">
                    <summary className="cursor-pointer text-[11px] text-[#2c5f58]">
                      {g.earlier.length} earlier scan(s)
                    </summary>
                    <ul className="mt-1 space-y-1">
                      {g.earlier.map((r) => (
                        <li key={r.bundle_id + r.pipeline} className="flex items-center justify-between text-[10px] text-[#4a443c]">
                          <span className="mono">{r.bundle_id.slice(0, 18)}…</span>
                          <button onClick={() => props.onOpenResult(r.bundle_id)} className="text-[#2c5f58] hover:underline">
                            open
                          </button>
                        </li>
                      ))}
                    </ul>
                  </details>
                )}
                <div className="mt-1.5 flex gap-2">
                  <button
                    onClick={() => center && props.onSelectTile(g.tile, center[0], center[1])}
                    className="rounded-md border border-[#e7e2da] px-2 py-0.5 text-[11px] text-[#4a443c] hover:border-[#2c5f58]"
                  >
                    show on globe
                  </button>
                  <Link
                    href={`/verdicts/${encodeURIComponent(g.current.bundle_id)}`}
                    className="rounded-md border border-[#2c5f58] px-2 py-0.5 text-[11px] text-[#2c5f58] hover:bg-[#eff5f3]"
                  >
                    full report
                  </Link>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Events tab                                                          */
/* ------------------------------------------------------------------ */

const SAR_CHIP: Record<RankedEvent["sar"], { label: string; cls: string; title: string }> = {
  high: { label: "SAR-ready", cls: "border-[#2c5f58] bg-[#eff5f3] text-[#2c5f58]", title: "Good point, and the list names something SAR can measure" },
  medium: { label: "SAR: maybe", cls: "border-[#b08d57] bg-[#f7f2e7] text-[#7a6438]", title: "Usable, with a caveat (point, size, depth or age)" },
  low: { label: "SAR: limited", cls: "border-[#e7e2da] bg-white text-[#8a837b]", title: "Little for SAR to add, or the point is loose" },
  none: { label: "no point", cls: "border-[#e7e2da] bg-white text-[#8a837b]", title: "No coordinates: cannot be placed on the map" },
};
const LOC_LABEL: Record<RankedEvent["location"], string> = {
  exact: "exact point", approximate: "approx. point", regional: "regional centroid", none: "no point",
};

function EventsList({ onSelectPoint }: { onSelectPoint: AreasPanelProps["onSelectPoint"] }) {
  const [cat, setCat] = useState("");
  const [q, setQ] = useState("");
  const [showAll, setShowAll] = useState(false);
  // ranked once per mount: freshness depends on today's date
  const ranked = useMemo(() => rankEvents(EVENTS), []);
  const cats = useMemo(() => Array.from(new Set(ranked.map((e) => e.category))), [ranked]);
  const rows = ranked.filter(
    (e) => (!cat || e.category === cat)
      && (!q || `${e.event} ${e.region}`.toLowerCase().includes(q.toLowerCase())),
  );
  const shown = showAll || cat || q ? rows : rows.slice(0, 15);

  return (
    <div className="mt-3" data-testid="events-list">
      <p className="mb-2 text-[11px] text-[#4a443c]">
        {ranked.length} events since the NISAR launch. Ordered by <b>how sure the map point is</b>, then by <b>how much SAR can
        measure</b> (size, depth, what the list says SAR sees) and <b>how fresh</b> the scenes are. A triage hint, not a verdict.
      </p>
      <div className="mb-2 flex flex-wrap items-center gap-1.5">
        <input suppressHydrationWarning
          value={q} onChange={(e) => setQ(e.target.value)} placeholder="search event or country"
          aria-label="Search events"
          className="w-40 rounded-md border border-[#e7e2da] px-2 py-1 text-[11px]" />
        {cats.map((c) => (
          <button key={c} onClick={() => setCat(cat === c ? "" : c)} aria-pressed={cat === c}
            className={`rounded-full border px-2 py-0.5 text-[10px] ${cat === c ? "border-[#2c5f58] bg-[#eff5f3] text-[#2c5f58]" : "border-[#e7e2da] text-[#4a443c]"}`}>
            {c}
          </button>
        ))}
      </div>
      <ol className="space-y-2">
        {shown.map((e) => {
          const chip = SAR_CHIP[e.sar];
          const can = e.lat != null && e.lon != null;
          return (
            <li key={e.id} className="rounded-lg border border-[#e7e2da] bg-white p-2.5" data-testid="event-row">
              <div className="flex items-start justify-between gap-2">
                <button
                  disabled={!can}
                  onClick={() => can && onSelectPoint(e.lat as number, e.lon as number, e.zoom)}
                  className="text-left text-sm font-semibold text-[#1c1a17] hover:text-[#2c5f58] disabled:cursor-default disabled:text-[#8a837b] disabled:hover:text-[#8a837b]"
                  title={can ? "fly to this location and open the Pin panel" : "no coordinates in the list"}
                >
                  <span className="mono mr-1.5 text-[10px] font-normal text-[#8a837b]">#{e.rank}</span>{e.event}
                </button>
                <span title={chip.title} className={`shrink-0 rounded-full border px-2 py-0.5 text-[10px] ${chip.cls}`}>{chip.label}</span>
              </div>
              <p className="mt-0.5 text-[11px] text-[#4a443c]">
                {e.category} · {e.date_text} · {e.region}
              </p>
              <p className="mt-0.5 text-[11px] text-[#8a837b]">
                {LOC_LABEL[e.location]}
                {can ? <span className="mono"> ({e.lat?.toFixed(2)}, {e.lon?.toFixed(2)})</span> : null}
                {e.size ? ` · ${e.size}` : ""}
                {e.deaths != null ? ` · ${e.deaths.toLocaleString("en")} reported deaths` : ""}
              </p>
              {e.sar_angle && <p className="mt-1 text-[11px] text-[#2c5f58]">SAR angle: {e.sar_angle}</p>}
              <details className="mt-1">
                <summary className="cursor-pointer text-[10px] text-[#8a837b]">why #{e.rank} (score {e.score})</summary>
                <ul className="mono mt-1 space-y-0.5 text-[10px] text-[#4a443c]">
                  {e.reasons.map((r, i) => <li key={i}>{r}</li>)}
                </ul>
                {e.facts && <p className="mt-1 text-[10px] text-[#8a837b]">{e.facts}</p>}
                <p className="mt-0.5 text-[10px] text-[#8a837b]">source: {e.sources}</p>
              </details>
            </li>
          );
        })}
      </ol>
      {!showAll && !cat && !q && rows.length > shown.length && (
        <button onClick={() => setShowAll(true)} className="mt-2 w-full rounded-md border border-[#e7e2da] py-1 text-[11px] text-[#2c5f58] hover:border-[#2c5f58]">
          show all {rows.length}
        </button>
      )}
      {rows.length === 0 && <p className="text-[11px] text-[#8a837b]">no event matches.</p>}
    </div>
  );
}
