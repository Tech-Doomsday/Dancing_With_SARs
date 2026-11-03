/** Explore > Events: the post-launch disaster list (disasters_after_nisar_launch.csv -> src/lib/disasters.json),
 *  ranked so that the events where (1) the point on the map is trustworthy and (2) SAR can actually say something
 *  come first. Pure functions, no React: unit-tested in tests-e2e/unit.spec.ts.
 *
 *  Nothing here is a verdict. "SAR-ready" is a triage hint built from the CSV's own columns (coordinate confidence,
 *  its suggested-NISAR-angle text, size, depth, date); every point added or removed is listed in `reasons`. */

export interface DisasterEvent {
  id: string;
  category: string;
  event: string;
  date_text: string;
  region: string;
  lat: number | null;
  lon: number | null;
  coord_conf: "A" | "B" | null;
  size: string;
  deaths: number | null;
  facts: string;
  sar_angle: string;
  sources: string;
}

export type SarReadiness = "high" | "medium" | "low" | "none";
export type LocationQuality = "exact" | "approximate" | "regional" | "none";

export interface RankedEvent extends DisasterEvent {
  rank: number;
  score: number;
  sar: SarReadiness;
  location: LocationQuality;
  /** last date mentioned in date_text as ISO yyyy-mm-dd, null if only a season / year is given */
  end_date: string | null;
  age_days: number | null;
  reasons: string[];
  /** zoom to fly to: tight for a surveyed epicentre, wide for a regional centroid */
  zoom: number;
}

const DAY = 86_400_000;

/** Last yyyy-mm(-dd) in a free-text date field ("2025-08-12 to 2025-08-14", "2026-06-07 23:37", "2025-11-25 and ongoing"). */
export function lastIsoDate(text: string): string | null {
  let best: string | null = null;
  for (const m of text.matchAll(/(20\d\d)-(\d\d)(?:-(\d\d))?/g)) {
    const y = Number(m[1]), mo = Number(m[2]);
    if (mo < 1 || mo > 12) continue;                          // "2025-26 rainy season" is a season, not a month
    const dim = new Date(Date.UTC(y, mo, 0)).getUTCDate();
    const d = m[3] ? Math.min(Number(m[3]), dim) : dim;       // a bare month counts as its last day
    const iso = `${m[1]}-${m[2]}-${String(d).padStart(2, "0")}`;
    if (!best || iso > best) best = iso;
  }
  return best;
}

export function locationQuality(e: DisasterEvent): LocationQuality {
  if (e.lat == null || e.lon == null) return "none";
  const t = `${e.event} ${e.facts} ${e.sar_angle} ${e.date_text}`.toLowerCase();
  if (/regional|centroid|not a point event|coordinates approximate|season/.test(t)) return "regional";
  return e.coord_conf === "A" ? "exact" : "approximate";
}

const POSITIVE = /insar|coseismic|offsets?|surface rupture|deformation|burn[- ]scar|landslide|debris|flood extent|lava|scar|path|inventory/g;
const WEAK = /limited|offshore|\blow\b|deep|little land impact|below insar|context only|context\b/;

function magnitude(size: string): number | null {
  const m = size.match(/\bM[w]?w?\s?(\d(?:\.\d)?)/);
  return m ? Number(m[1]) : null;
}
function depthKm(size: string): number | null {
  const m = size.match(/depth\s*~?\s*(\d+)\s*km/i);
  return m ? Number(m[1]) : null;
}

const CATEGORY_BASE: Record<string, number> = {
  Earthquake: 12, Volcano: 8, "Flood/landslide": 8, Wildfire: 4, Cyclone: 0, Heat: -40,
};

export function rankEvents(events: DisasterEvent[], now: number = Date.now()): RankedEvent[] {
  const scored = events.map((e) => {
    const reasons: string[] = [];
    let score = 0;
    const add = (pts: number, why: string) => { score += pts; reasons.push(`${pts > 0 ? "+" : "−"}${Math.abs(pts)} ${why}`); };

    const location = locationQuality(e);
    if (location === "exact") add(30, "exact coordinates (confidence A)");
    else if (location === "approximate") add(12, "approximate point (confidence B)");
    else if (location === "regional") add(-3, "regional centroid / approximate, not a point event");
    else add(-60, "no map point");

    add(CATEGORY_BASE[e.category] ?? 0, `${e.category.toLowerCase()}: base SAR value`);

    const angle = e.sar_angle.toLowerCase();
    if (/priority/.test(angle)) add(25, "flagged PRIORITY in the list");
    const hits = new Set(Array.from(angle.matchAll(POSITIVE), (m) => m[0]));
    if (hits.size) add(Math.min(18, 6 * hits.size), `SAR can measure it (${Array.from(hits).slice(0, 3).join(", ")})`);
    if (WEAK.test(angle)) add(-25, "list says SAR value is limited (offshore / deep / low)");

    if (e.category === "Earthquake") {
      const mag = magnitude(e.size), dep = depthKm(e.size);
      if (mag != null) {
        if (mag >= 7) add(10, `Mw ${mag} is large`);
        else if (mag >= 6) add(5, `Mw ${mag}`);
        else if (mag < 5.5) add(-10, `Mw ${mag} is small for InSAR`);
      }
      if (dep != null) {
        if (dep >= 100) add(-20, `deep source (${dep} km)`);
        else if (dep <= 20) add(6, `shallow source (${dep} km)`);
      }
    }

    const end = lastIsoDate(e.date_text);
    const age = end ? Math.floor((now - Date.parse(`${end}T23:59:59Z`)) / DAY) : null;
    if (age != null && age >= -1) {
      if (age <= 21) add(25, `fresh: ${Math.max(0, age)} d old, scenes still arriving`);
      else if (age <= 60) add(15, `recent: ${age} d old`);
      else if (age <= 120) add(8, `${age} d old`);
    }

    const sar: SarReadiness = location === "none" ? "none" : score >= 75 ? "high" : score >= 45 ? "medium" : "low";
    const zoom = location === "exact" ? 10.5 : location === "approximate" ? 8.5 : location === "regional" ? 6 : 3;
    return { ...e, score, sar, location, end_date: end, age_days: age, reasons, zoom };
  });
  scored.sort((a, b) =>
    b.score - a.score
    || (b.deaths ?? -1) - (a.deaths ?? -1)
    || (b.end_date ?? "").localeCompare(a.end_date ?? "")
    || a.event.localeCompare(b.event));
  return scored.map((e, i) => ({ ...e, rank: i + 1 }));
}
