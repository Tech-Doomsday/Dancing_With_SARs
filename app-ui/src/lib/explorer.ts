/** Pure Granule Explorer logic (§7.8) — filters, parsers, lane packing. No DOM, no fetches;
 *  unit-tested in tests-e2e/unit.spec.ts. All time handling is UTC. Zero science: boxes,
 *  counts and times come from the engine; this module only filters and positions them. */

const DAY_MS = 86_400_000;

// ---------------------------------------------------------------------------------------------
// filters — parsers return null on junk (the UI shows the inline message and does NOT apply)
// ---------------------------------------------------------------------------------------------

/** "5", "17", "20-25", "20-25, 5" -> day-of-month set. Reversed ranges and junk are rejected. */
export function parseDayOfMonth(text: string): number[] | null {
  const t = text.trim();
  if (!t) return [];
  const out = new Set<number>();
  for (const part of t.split(/[,;]\s*/)) {
    if (!part) continue;
    const m = /^(\d{1,2})(?:\s*-\s*(\d{1,2}))?$/.exec(part);
    if (!m) return null;
    const a = Number(m[1]);
    const b = m[2] !== undefined ? Number(m[2]) : a;
    if (!Number.isInteger(a) || !Number.isInteger(b) || a < 1 || b < 1 || a > 31 || b > 31) return null;
    if (m[2] !== undefined && b < a) return null; // a reversed range is junk, not "wrap around"
    for (let d = a; d <= b; d++) out.add(d);
  }
  return [...out].sort((x, y) => x - y);
}

/** minutes since 00:00 UTC */
export interface TimeOfDay {
  fromMin: number;
  toMin: number;
}

/** "13:00", "13:00-14:30", "23:00-01:00" (midnight wrap) -> range(s) of minutes. */
export function parseTimeOfDay(text: string): TimeOfDay[] | null {
  const t = text.trim();
  if (!t) return [];
  const m = /^(\d{1,2}):(\d{2})(?:\s*-\s*(\d{1,2}):(\d{2}))?$/.exec(t);
  if (!m) return null;
  const hh1 = Number(m[1]);
  const mm1 = Number(m[2]);
  if (hh1 > 23 || mm1 > 59) return null;
  if (m[3] === undefined) return [{ fromMin: hh1 * 60 + mm1, toMin: hh1 * 60 + mm1 }];
  const hh2 = Number(m[3]);
  const mm2 = Number(m[4]);
  if (hh2 > 23 || mm2 > 59) return null;
  const a = hh1 * 60 + mm1;
  const b = hh2 * 60 + mm2;
  if (a <= b) return [{ fromMin: a, toMin: b }];
  // midnight wrap: 23:00-01:00 = [23:00..24:00) + [00:00..01:00]
  return [{ fromMin: a, toMin: 24 * 60 }, { fromMin: 0, toMin: b }];
}

export type AppliesTo = "either" | "reference" | "secondary";

export interface ExplorerFilters {
  dom: number[];                       // day-of-month allowlist (empty = all)
  tod: TimeOfDay[];                    // time-of-day ranges (empty = all)
  applies: AppliesTo;
  direction: "both" | "A" | "D";
  track: number | null;
  text: string;                        // file-name substring
}

export function emptyFilters(): ExplorerFilters {
  return { dom: [], tod: [], applies: "either", direction: "both", track: null, text: "" };
}

/** UTC day-of-month + minutes-of-day of an ISO instant (NaN-safe). */
export function utcParts(iso: string | null | undefined): { dom: number; minutes: number } | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return null;
  const d = new Date(t);
  return { dom: d.getUTCDate(), minutes: d.getUTCHours() * 60 + d.getUTCMinutes() };
}

function timeHits(g: { ref_time: string | null; sec_time: string | null; start: string | null },
                  applies: AppliesTo,
                  dom: number[],
                  tod: TimeOfDay[]): boolean {
  const cand: (string | null)[] =
    applies === "reference" ? [g.ref_time]
    : applies === "secondary" ? [g.sec_time]
    : [g.ref_time, g.sec_time, g.start]; // "either"; GCOV contributes its single start
  const live = cand.filter((x): x is string => Boolean(x));
  if (live.length === 0) return dom.length === 0 && tod.length === 0; // nothing to test -> only unfiltered passes
  return live.some((iso) => {
    const p = utcParts(iso);
    if (!p) return false;
    if (dom.length && !dom.includes(p.dom)) return false;
    if (tod.length) {
      const hit = tod.some((r) => p.minutes >= r.fromMin && p.minutes <= r.toMin);
      if (!hit) return false;
    }
    return true;
  });
}

/** client-side filter over the fetched set (the engine already narrowed box/window/level). */
export function filterGranules(
  granules: { id: string; direction: "A" | "D" | null; track: number | null; ref_time: string | null;
              sec_time: string | null; start: string | null }[],
  f: ExplorerFilters,
): Set<string> {
  const keep = new Set<string>();
  const text = f.text.trim().toLowerCase();
  for (const g of granules) {
    if (f.direction !== "both" && g.direction !== f.direction) continue;
    if (f.track !== null && g.track !== f.track) continue;
    if (text && !g.id.toLowerCase().includes(text)) continue;
    if (!timeHits(g, f.applies, f.dom, f.tod)) continue;
    keep.add(g.id);
  }
  return keep;
}

/** "N of M granules match" — never clipped by the client filter (the engine says its own truncation). */
export function matchCount(granules: { id: string }[], keep: Set<string>): { n: number; m: number } {
  return { n: keep.size, m: granules.length };
}

// ---------------------------------------------------------------------------------------------
// lanes — greedy pack bars into sub-lanes (deterministic; bars never overlap visually)
// ---------------------------------------------------------------------------------------------

export interface BarInterval {
  key: string;         // stable id (granule id + role)
  start: number;       // ms
  end: number;         // ms (>= start)
}

export interface PackedBar extends BarInterval {
  lane: number;
}

/** greedy pack: sort by (start, end, key), place each bar in the first lane whose last bar
 *  ends before this one starts (+ minGapMs). Deterministic for equal inputs. */
export function packLanes(intervals: BarInterval[], minGapMs = 0): PackedBar[] {
  const sorted = [...intervals].sort((a, b) =>
    a.start - b.start || a.end - b.end || (a.key < b.key ? -1 : a.key > b.key ? 1 : 0));
  const laneEnds: number[] = [];
  const out: PackedBar[] = [];
  for (const bar of sorted) {
    let lane = laneEnds.findIndex((end) => end + minGapMs <= bar.start);
    if (lane === -1) {
      laneEnds.push(bar.end);
      lane = laneEnds.length - 1;
    } else {
      laneEnds[lane] = bar.end;
    }
    out.push({ ...bar, lane });
  }
  return out;
}

/** ms-per-day and friends used by the canvas; exported for tests */
export const EXPLORER_DAY_MS = DAY_MS;
