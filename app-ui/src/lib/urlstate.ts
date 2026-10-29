/** URL state (§7.7) — pure parse/format, validated and ignorable. A pasted link restores the same
 *  view; unknown/invalid values fall back to the default view without crashing. */

export type PanelMode = "explore" | "cached" | "pin" | "results" | "granules";

export interface UrlState {
  lat?: number;
  lon?: number;
  z?: number;
  tile?: string;
  bundle?: string;
  pipeline?: string;
  layer?: string;
  scan?: string;
  panel: PanelMode;
}

const PANELS: PanelMode[] = ["explore", "cached", "pin", "results", "granules"];
const TILE_RE = /^\d{1,2}[C-X][A-HJ-NP-Z]{2}\d*$/; // same grammar as engine/tiling/mgrs.py::_ID

export function parseUrlState(params: URLSearchParams | Record<string, string | string[] | undefined>): UrlState {
  const get = (k: string): string | undefined => {
    if (params instanceof URLSearchParams) return params.get(k) ?? undefined;
    const v = params[k];
    return Array.isArray(v) ? v[0] : v;
  };
  const num = (k: string, lo: number, hi: number): number | undefined => {
    const raw = get(k);
    if (raw === undefined || raw === "") return undefined;
    const v = Number(raw);
    if (!Number.isFinite(v) || v < lo || v > hi) return undefined;
    return v;
  };
  const id = (k: string, re: RegExp): string | undefined => {
    const raw = get(k);
    if (!raw) return undefined;
    try {
      const decoded = decodeURIComponent(raw);
      return re.test(decoded) ? decoded : undefined;
    } catch {
      return undefined;
    }
  };
  const panelRaw = get("panel");
  const panel: PanelMode = panelRaw && (PANELS as string[]).includes(panelRaw) ? (panelRaw as PanelMode) : "explore";
  return {
    lat: num("lat", -90, 90),
    lon: num("lon", -180, 180),
    z: num("z", 0, 22),
    tile: id("tile", TILE_RE),
    bundle: id("bundle", /^[A-Za-z0-9_-]{1,128}$/),
    pipeline: id("pipeline", /^[a-z0-9_]{1,64}$/),
    layer: id("layer", /^[A-Za-z0-9_-]{1,64}$/),
    scan: id("scan", /^run-[0-9a-f]{12}$/),
    panel,
  };
}

/** apply the state to a URLSearchParams in place (debounced router.replace feeds this) */
export function writeUrlState(params: URLSearchParams, state: UrlState): void {
  const setOrDrop = (k: string, v: string | number | undefined) => {
    if (v === undefined || v === null || v === "") params.delete(k);
    else params.set(k, String(v));
  };
  setOrDrop("lat", state.lat);
  setOrDrop("lon", state.lon);
  setOrDrop("z", state.z);
  setOrDrop("tile", state.tile);
  setOrDrop("bundle", state.bundle);
  setOrDrop("pipeline", state.pipeline);
  setOrDrop("layer", state.layer);
  setOrDrop("scan", state.scan);
  setOrDrop("panel", state.panel === "explore" ? undefined : state.panel);
}
