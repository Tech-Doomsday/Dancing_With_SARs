import { test, expect } from "@playwright/test";
import { parseCoordinates } from "../src/lib/geo-input";
import { validateLayerPayload, seamSplits } from "../src/lib/raster";
import { parseUrlState, writeUrlState } from "../src/lib/urlstate";
import { gapThresholdDays, ticks as ticksFor, tToX, xToT, zoom as zoomScale, pan as panScale } from "../src/lib/timescale";
import { mostNotableTier } from "../src/lib/status";
import { currentPosition, geoState, type GeoNav } from "../src/lib/geolocation";
import {
  emptyFilters, filterGranules, packLanes, parseDayOfMonth, parseTimeOfDay,
  type BarInterval,
} from "../src/lib/explorer";
import { parseExplorerState, writeExplorerState } from "../src/lib/urlstate";
import { groupTimes, groupKindLabel, variantFactsLabel, compareEnds, zoomBox, panBox } from "../src/lib/groups";
import { ticks as ticksForExplorer } from "../src/lib/timescale";
import { mapHref, retrievalHref, sanitize, toDateOnly } from "../src/lib/queryContext";
import { normalizeEngineUrl } from "../src/lib/engineUrl";

/** §8.2 pure-logic tests — no browser needed. Playwright transpiles the TS imports directly. */

const DAY = 86_400_000;

test.describe("parseCoordinates", () => {
  const ok = (s: string) => {
    const r = parseCoordinates(s);
    if (!r.ok) throw new Error(`${s} should parse: ${r.error}`);
    return r.value;
  };
  const bad = (s: string, hint?: string) => {
    const r = parseCoordinates(s);
    expect(r.ok, `${s} should be rejected`).toBe(false);
    if (!r.ok && hint) expect(r.error).toContain(hint);
  };

  test("accepted forms", () => {
    expect(ok("34.05, -118.25")).toEqual({ lat: 34.05, lon: -118.25 });
    expect(ok("34.05 -118.25")).toEqual({ lat: 34.05, lon: -118.25 });
    expect(ok("34.05N 118.25W")).toEqual({ lat: 34.05, lon: -118.25 });
    expect(ok("-33.87,151.21")).toEqual({ lat: -33.87, lon: 151.21 });
    expect(ok("\u00a034.05,\u00a0-118.25")).toEqual({ lat: 34.05, lon: -118.25 });
    expect(ok("N 34.05, W 118.25")).toEqual({ lat: 34.05, lon: -118.25 });
    expect(ok("0, 0")).toEqual({ lat: 0, lon: 0 });
  });

  test("rejected with a clear message (never silently swapped)", () => {
    bad("", "enter coordinates");
    bad("   ");
    bad("NaN, 0");
    bad("91, 0", "outside [-90, 90]");
    bad("0, 181", "outside [-180, 180]");
    // lat 118 with a lon that would fit: REJECTED, never swapped into range
    bad("118.25, 34.05", "outside [-90, 90]");
    bad("34,05", "comma-decimal");
    bad("3.4e1, 10", "exponent");
    bad("34.05", "two numbers");
    bad("a, b");
  });
});

test.describe("validateLayerPayload (client side)", () => {
  const validF4 = {
    name: "displacement", dtype: "<f4", shape: [2, 2],
    data_b64: Buffer.from(new Float32Array([1, 2, 3, 4]).buffer).toString("base64"),
    bounds_lonlat: [-118.3, 34.0, -118.2, 34.1],
    vmin: 1, vmax: 4, units: "m", colormap: "diverging", note: "",
  };
  const validU1 = {
    ...validF4,
    name: "detected", dtype: "<u1",
    data_b64: Buffer.from(new Uint8Array([0, 1, 2, 0]).buffer).toString("base64"),
  };

  test("valid payloads pass", () => {
    expect(validateLayerPayload(validF4)).toEqual([]);
    expect(validateLayerPayload(validU1)).toEqual([]);
  });
  test("bad base64 length", () => {
    const problems = validateLayerPayload({ ...validF4, data_b64: Buffer.from([1, 2]).toString("base64") });
    expect(problems.join(" ")).toContain("decoded data_b64 is 2 bytes");
  });
  test("nested legacy bounds", () => {
    const problems = validateLayerPayload({ ...validF4, bounds_lonlat: [[-118.3, 34.0], [-118.2, 34.1]] });
    expect(problems.join(" ")).toContain("four finite numbers");
  });
  test("missing fields", () => {
    const { bounds_lonlat, ...without } = validF4;
    expect(validateLayerPayload(without).join(" ")).toContain("missing field: bounds_lonlat");
  });
  test("degenerate bounds", () => {
    expect(validateLayerPayload({ ...validF4, bounds_lonlat: [10, 20, 10, 30] }).join(" ")).toContain("W == E");
    expect(validateLayerPayload({ ...validF4, bounds_lonlat: [10, 30, 20, 20] }).join(" ")).toContain("S >= N");
  });
});

test.describe("urlstate", () => {
  test("round-trips valid params", () => {
    const p = new URLSearchParams("lat=34.05&lon=-118.25&z=11.5&tile=11SLT86&panel=granules");
    const st = parseUrlState(p);
    expect(st.lat).toBe(34.05);
    expect(st.lon).toBe(-118.25);
    expect(st.z).toBe(11.5);
    expect(st.tile).toBe("11SLT86");
    expect(st.panel).toBe("granules");
    const out = new URLSearchParams();
    writeUrlState(out, st);
    expect(out.get("panel")).toBe("granules");
    expect(out.get("tile")).toBe("11SLT86");
  });
  test("ignores invalid values without crashing", () => {
    const st = parseUrlState(new URLSearchParams("lat=999&lon=abc&panel=nope&tile=DROP TABLE"));
    expect(st.lat).toBeUndefined();
    expect(st.lon).toBeUndefined();
    expect(st.panel).toBe("explore");
    expect(st.tile).toBeUndefined();
  });
  test("panel defaults to explore; explore is not written", () => {
    const out = new URLSearchParams();
    writeUrlState(out, { panel: "explore" });
    expect(Array.from(out.keys())).toEqual([]);
  });
});

test.describe("timescale", () => {
  const bounds = { min: Date.UTC(2026, 5, 17), max: Date.UTC(2026, 9, 15) };
  const scale = { t0: Date.UTC(2026, 6, 1), t1: Date.UTC(2026, 6, 21), widthPx: 700 };

  test("t <-> px round-trips", () => {
    const t = Date.UTC(2026, 6, 10, 12, 30);
    const x = tToX(scale, t);
    expect(xToT(scale, x)).toBeCloseTo(t, 0);
  });
  test("day ticks under ~45 days, month ticks beyond 9 months", () => {
    const short = ticksFor({ t0: bounds.min, t1: bounds.min + 20 * DAY, widthPx: 700 });
    expect(short.length).toBeGreaterThan(2);
    const long = ticksFor({ t0: bounds.min, t1: bounds.min + 300 * DAY, widthPx: 700 });
    expect(long.length).toBeGreaterThan(2);
    expect(Number(long[1].ms) - Number(long[0].ms)).toBeGreaterThanOrEqual(28 * DAY);
  });
  test("zoom keeps the window inside bounds and min span >= 1 day", () => {
    let s = zoomScale(scale, 8, 350, bounds);
    expect(s.t0).toBeGreaterThanOrEqual(bounds.min);
    expect(s.t1).toBeLessThanOrEqual(bounds.max);
    s = zoomScale(s, 1e9, 350, bounds);
    expect(s.t1 - s.t0).toBeGreaterThanOrEqual(DAY - 1);
    s = zoomScale({ ...s, t1: s.t0 + 400 * DAY }, 1, 0, bounds);
    expect(s.t1 - s.t0).toBeLessThanOrEqual(bounds.max - bounds.min);
  });
  test("pan clamps to bounds", () => {
    const panned = panScale(scale, -100000, bounds); // drag right past the end
    expect(panned.t1).toBeLessThanOrEqual(bounds.max + 1);
    const panned2 = panScale(scale, 100000, bounds);
    expect(panned2.t0).toBeGreaterThanOrEqual(bounds.min - 1);
  });
  test("gap threshold derives from the row's own median (1.5x), not hard-coded", () => {
    const times = [0, 12 * DAY, 24 * DAY, 36 * DAY];
    expect(gapThresholdDays(times)).toBeCloseTo(18, 5);
    expect(gapThresholdDays([0])).toBeNull();
    // duplicates (a row mixes bar starts/ends and epoch marks; one bar's end equals the next
    // bar's start) must not drag the median to 0 — distinct times drive the threshold
    const dup = [0, 12 * DAY, 12 * DAY, 24 * DAY, 24 * DAY, 36 * DAY];
    expect(gapThresholdDays(dup)).toBeCloseTo(18, 5);
  });
});

test.describe("status display precedence", () => {
  test("most notable tier follows the display order", () => {
    expect(mostNotableTier(["error", "supported"])).toBe("supported");
    expect(mostNotableTier(["error", "insufficient"])).toBe("insufficient");
    expect(mostNotableTier(["not_modelled", "error"])).toBe("not_modelled");
    expect(mostNotableTier([null, undefined])).toBe("insufficient");
  });
});

test.describe("geolocation wrapper (S4/DEC2 branches)", () => {
  const navWith = (over: Partial<GeoNav>): GeoNav => over;

  test("permissions query branches: granted / prompt / denied / query-rejected / no-permissions / no-geolocation", async () => {
    expect(await geoState(navWith({
      geolocation: {} as GeoNav["geolocation"],
      permissions: { query: async () => ({ state: "granted" }) },
    }))).toBe("granted");
    expect(await geoState(navWith({
      geolocation: {} as GeoNav["geolocation"],
      permissions: { query: async () => ({ state: "prompt" }) },
    }))).toBe("prompt");
    expect(await geoState(navWith({
      geolocation: {} as GeoNav["geolocation"],
      permissions: { query: async () => ({ state: "denied" }) },
    }))).toBe("denied");
    // Safari <= 16 rejects the query -> treated as prompt (the browser dialog decides)
    expect(await geoState(navWith({
      geolocation: {} as GeoNav["geolocation"],
      permissions: { query: async () => { throw new Error("TypeError"); } },
    }))).toBe("prompt");
    // no Permissions API at all
    expect(await geoState(navWith({ geolocation: {} as GeoNav["geolocation"] }))).toBe("prompt");
    // insecure context: no geolocation -> unavailable, never prompts
    expect(await geoState(navWith({}))).toBe("unavailable");
  });

  test("currentPosition branches: success / denied / timeout / unavailable / no-geolocation", async () => {
    const success = await currentPosition(navWith({
      geolocation: {
        getCurrentPosition: (ok) => ok({ coords: { latitude: 34.05, longitude: -118.25 } }),
      },
    }));
    expect(success).toEqual({ ok: true, position: { lat: 34.05, lon: -118.25 } });

    const denied = await currentPosition(navWith({
      geolocation: {
        getCurrentPosition: (_ok, err) => err!({ code: 1, message: "user denied" }),
      },
    }));
    expect(denied.ok).toBe(false);
    if (!denied.ok) expect(denied.reason).toBe("denied");

    const timeout = await currentPosition(navWith({
      geolocation: {
        getCurrentPosition: (_ok, err) => err!({ code: 3, message: "timeout" }),
      },
    }));
    expect(timeout.ok).toBe(false);
    if (!timeout.ok) expect(timeout.reason).toBe("timeout");

    const unavailable = await currentPosition(navWith({
      geolocation: {
        getCurrentPosition: (_ok, err) => err!({ code: 2, message: "position unavailable" }),
      },
    }));
    expect(unavailable.ok).toBe(false);
    if (!unavailable.ok) expect(unavailable.reason).toBe("unavailable");

    const none = await currentPosition(navWith({}));
    expect(none.ok).toBe(false);
    if (!none.ok) expect(none.reason).toBe("unavailable");
  });

  test("currentPosition passes the DEC2 options (low accuracy, 8 s timeout, 10 min max age)", async () => {
    let seen: Record<string, unknown> | null = null;
    await currentPosition(navWith({
      geolocation: {
        getCurrentPosition: (ok2, _err, options) => { seen = options ?? null; ok2({ coords: { latitude: 0, longitude: 0 } }); },
      },
    }));
    expect(seen).toEqual({ enableHighAccuracy: false, timeout: 8000, maximumAge: 600_000 });
  });
});

test.describe("explorer logic (§7.8)", () => {
  test("parseDayOfMonth: single, range, list, reversed range and junk", () => {
    expect(parseDayOfMonth("5")).toEqual([5]);
    expect(parseDayOfMonth("17")).toEqual([17]);
    expect(parseDayOfMonth("20-25")).toEqual([20, 21, 22, 23, 24, 25]);
    expect(parseDayOfMonth("20-25, 5")).toEqual([5, 20, 21, 22, 23, 24, 25]);
    expect(parseDayOfMonth("")).toEqual([]);
    expect(parseDayOfMonth("25-20")).toBeNull();      // reversed range is junk, not a wrap
    expect(parseDayOfMonth("0")).toBeNull();
    expect(parseDayOfMonth("32")).toBeNull();
    expect(parseDayOfMonth("banana")).toBeNull();
    expect(parseDayOfMonth("1-2-3")).toBeNull();
  });

  test("parseTimeOfDay: single, range, midnight wrap, junk", () => {
    expect(parseTimeOfDay("13:00")).toEqual([{ fromMin: 780, toMin: 780 }]);
    expect(parseTimeOfDay("13:00-14:30")).toEqual([{ fromMin: 780, toMin: 870 }]);
    // midnight wrap: 23:00-01:00 spans the seam
    expect(parseTimeOfDay("23:00-01:00")).toEqual([{ fromMin: 1380, toMin: 1440 }, { fromMin: 0, toMin: 60 }]);
    expect(parseTimeOfDay("")).toEqual([]);
    expect(parseTimeOfDay("24:00")).toBeNull();
    expect(parseTimeOfDay("13:60")).toBeNull();
    expect(parseTimeOfDay("now")).toBeNull();
  });

  test("tick generator: 3 h, 3 d, 3 mo, 3 y and 3 y across a year boundary; UTC, monotonic, bounded", () => {
    const mk = (t0: number, t1: number) => ticksForExplorer({ t0, t1, widthPx: 900 });
    const h = mk(Date.UTC(2026, 6, 1, 0), Date.UTC(2026, 6, 1, 3));
    expect(h.length).toBeGreaterThan(2);
    expect(h.some((t) => /^\d{2}:\d{2}$/.test(t.label))).toBe(true);  // hour labels present (midnight renders as a day)
    const d3 = mk(Date.UTC(2026, 6, 1), Date.UTC(2026, 6, 4));
    expect(d3.length).toBeGreaterThan(2);
    const m3 = mk(Date.UTC(2026, 5, 1), Date.UTC(2026, 7, 31));
    expect(m3.length).toBeGreaterThan(2);
    const y3 = mk(Date.UTC(2025, 0, 1), Date.UTC(2028, 0, 1));
    expect(y3.length).toBeGreaterThan(2);
    const yBoundary = mk(Date.UTC(2025, 10, 1), Date.UTC(2028, 1, 1));
    expect(yBoundary.length).toBeGreaterThan(2);
    for (const list of [h, d3, m3, y3, yBoundary]) {
      for (let i = 1; i < list.length; i++) {
        expect(list[i].ms).toBeGreaterThan(list[i - 1].ms);  // monotonic
      }
      expect(list.length).toBeLessThan(400);                 // bounded count
      for (const t of list) expect(Number.isFinite(t.ms)).toBe(true);
    }
    // UTC labels: a January major is the month+year form
    const jan = mk(Date.UTC(2026, 0, 1), Date.UTC(2026, 2, 1));
    expect(jan.some((t) => t.label.includes("Jan 2026") || t.label.includes("2026"))).toBe(true);
  });

  test("interval packer: no overlaps, deterministic", () => {
    const bars: BarInterval[] = [
      { key: "a", start: 0, end: 10 },
      { key: "b", start: 5, end: 15 },
      { key: "c", start: 12, end: 20 },
      { key: "d", start: 0, end: 4 },
    ];
    const packed = packLanes(bars);
    expect(packed.map((p) => p.key).sort()).toEqual(["a", "b", "c", "d"]);
    // lanes: a -> lane 0; b overlaps a -> lane 1; c overlaps b -> lane 1? (c starts 12 after b ends 15? no: 12 < 15 -> lane 2); d fits lane 0? d ends 4 <= a start 0? no (gap 0) -> lane 1? d 0-4 vs b 5-15: d ends before b starts -> lane... deterministic; assert no two bars in the same lane overlap
    const byLane = new Map<number, BarInterval[]>();
    for (const p of packed) {
      const list = byLane.get(p.lane) ?? [];
      for (const other of list) {
        expect(p.start).toBeGreaterThanOrEqual(other.end);   // no overlap within a lane
      }
      list.push(p);
      byLane.set(p.lane, list);
    }
    expect(packLanes(bars)).toEqual(packed);                 // deterministic
  });

  test("filter combinations: applies-to modes over ref/sec/GCOV start", () => {
    const granules = [
      { id: "pair-a", direction: "A" as const, track: 34, ref_time: "2026-09-20T13:08:23Z", sec_time: "2026-10-14T13:08:23Z", start: null },
      { id: "cov-a", direction: "A" as const, track: 34, ref_time: null, sec_time: null, start: "2026-09-20T09:00:00Z" },
      { id: "pair-d", direction: "D" as const, track: 71, ref_time: "2026-09-21T01:00:00Z", sec_time: "2026-10-15T01:00:00Z", start: null },
    ];
    // direction filter
    expect(filterGranules(granules, { ...emptyFilters(), direction: "D" }).has("pair-d")).toBe(true);
    expect(filterGranules(granules, { ...emptyFilters(), direction: "D" }).has("pair-a")).toBe(false);
    // track filter
    expect(filterGranules(granules, { ...emptyFilters(), track: 71 }).has("pair-d")).toBe(true);
    expect(filterGranules(granules, { ...emptyFilters(), track: 71 }).has("cov-a")).toBe(false);
    // day-of-month: the 20th matches the pair's ref and the GCOV start, not the D pair (21st)
    expect(filterGranules(granules, { ...emptyFilters(), dom: [20] }).has("pair-a")).toBe(true);
    expect(filterGranules(granules, { ...emptyFilters(), dom: [20] }).has("cov-a")).toBe(true);
    expect(filterGranules(granules, { ...emptyFilters(), dom: [20] }).has("pair-d")).toBe(false);
    // applies to: reference-only excludes a match that only lives on the secondary
    expect(filterGranules(granules, { ...emptyFilters(), dom: [14], applies: "reference" }).has("pair-a")).toBe(false);
    expect(filterGranules(granules, { ...emptyFilters(), dom: [14], applies: "either" }).has("pair-a")).toBe(true);
    expect(filterGranules(granules, { ...emptyFilters(), dom: [14], applies: "secondary" }).has("pair-a")).toBe(true);
    // text substring
    expect(filterGranules(granules, { ...emptyFilters(), text: "cov" }).has("cov-a")).toBe(true);
    expect(filterGranules(granules, { ...emptyFilters(), text: "cov" }).has("pair-a")).toBe(false);
  });

  test("explorer URL state round-trips and ignores junk", () => {
    const p = new URLSearchParams("west=179.9&south=-1&east=-179.9&north=1&level=GUNW,GCOV&from=2026-06-01&to=2026-10-01&track=34&dir=A&sel=NISAR_x");
    const st = parseExplorerState(p);
    expect(st.west).toBe(179.9);
    expect(st.level).toBe("GUNW,GCOV");
    expect(st.track).toBe(34);
    expect(st.dir).toBe("A");
    const out = new URLSearchParams();
    writeExplorerState(out, st);
    expect(out.get("west")).toBe("179.9");
    expect(parseExplorerState(out).level).toBe("GUNW,GCOV");
    // junk: out-of-range numbers, bad level, bad dates -> dropped, no crash
    const junk = parseExplorerState(new URLSearchParams("west=999&level=SLC&from=notadate&track=0&dir=X"));
    expect(junk.west).toBeUndefined();
    expect(junk.level).toBeUndefined();
    expect(junk.from).toBeUndefined();
    expect(junk.track).toBeUndefined();
    expect(junk.dir).toBeUndefined();
  });
});

test.describe("post-audit regressions", () => {
  test("seamSplits: normal, wrapped, unwrapped, below -180, zero-width and non-finite", () => {
    expect(seamSplits([-118.3, 34, -118.2, 34.1])).toEqual([{ bounds: [-118.3, 34, -118.2, 34.1], crop: [0, 1] }]);
    const wrapped = seamSplits([179.985, 10, -179.985, 10.1]);
    expect(wrapped).toHaveLength(2);
    expect(wrapped[0].bounds[2]).toBe(180);
    expect(wrapped[1].bounds[0]).toBe(-180);
    expect(wrapped[0].crop[1]).toBeCloseTo(0.5, 6);
    expect(wrapped[1].crop[0]).toBeCloseTo(0.5, 6);
    expect(seamSplits([179.985, 10, 180.015, 10.1])).toHaveLength(2);      // unwrapped east
    expect(seamSplits([-180.015, 10, -179.985, 10.1])).toHaveLength(2);    // west below -180
    expect(seamSplits([-180, -10, 180, 10])).toHaveLength(1);              // whole world is one piece
    expect(seamSplits([10, 0, 10, 1])).toEqual([]);                        // zero width is refused, not a world-wide overlay
    expect(seamSplits([Number.NaN, 0, 1, 1])).toEqual([]);
  });

  test("explorer from/to accept a space-separated time and normalise it", () => {
    const st = parseExplorerState(new URLSearchParams("from=2026-10-01 12:00&to=2026-10-20T08:30:00Z"));
    expect(st.from).toBe("2026-10-01T12:00");
    expect(st.to).toBe("2026-10-20T08:30:00Z");
    expect(parseExplorerState(new URLSearchParams("from=2026-13-45")).from).toBeUndefined();
  });
});

test.describe("UI-build-2 — grouping display helpers (§4.2)", () => {
  test("groupTimes: pair bars use TRUE time order; singles use the start; null without times", () => {
    expect(groupTimes({ ref_time: "2026-10-14T13:08:23Z", sec_time: "2026-09-20T13:08:23Z", start: null }))
      .toEqual({ start: Date.parse("2026-09-20T13:08:23Z"), end: Date.parse("2026-10-14T13:08:23Z") });
    expect(groupTimes({ ref_time: null, sec_time: null, start: "2026-07-14T13:08:23Z" }))
      .toEqual({ start: Date.parse("2026-07-14T13:08:23Z"), end: Date.parse("2026-07-14T13:08:23Z") });
    expect(groupTimes({ ref_time: null, sec_time: null, start: null })).toBeNull();
  });

  test("groupKindLabel + variantFactsLabel: text always accompanies the glyph/colour", () => {
    expect(groupKindLabel({ kind: "pair", product: "GUNW", grouped: true })).toBe("GUNW pair");
    expect(groupKindLabel({ kind: "single", product: "GCOV", grouped: true })).toBe("GCOV single date");
    expect(groupKindLabel({ kind: "unknown", product: null, grouped: false })).toBe("ungrouped");
    expect(variantFactsLabel({ n_variants: 1, variant_facts: [] })).toBeNull();
    expect(variantFactsLabel({ n_variants: 2, variant_facts: ["range_bandwidth_mhz"] }))
      .toBe("2 variants (differ in bandwidth)");
  });

  test("compareEnds: first variant per end; absent ends stay null (never substituted)", () => {
    const a = { id: "a" } as never;
    const b = { id: "b" } as never;
    expect(compareEnds({ ref: { granules: [a, b] }, sec: { granules: [] } }))
      .toEqual({ before: a, after: null });
  });
});

test.describe("UI-build-2 — Compare view box math (§4.4/§4.5)", () => {
  test("zoomBox: zoom = a NEW sub-box around the focus, clamped, with a minimum span", () => {
    const box: [number, number, number, number] = [-2, -1, 2, 1];
    const z = zoomBox(box, 0.5, 0.5, 4);
    expect(z[0]).toBeCloseTo(-0.5, 6);
    expect(z[2]).toBeCloseTo(0.5, 6);
    expect(z[2] - z[0]).toBeCloseTo(1, 6);
    // clamped at the world edge
    const edge = zoomBox(box, 0, 0, 8);
    expect(edge[0]).toBeGreaterThanOrEqual(-180);
    expect(edge[3]).toBeLessThanOrEqual(90);
    // minimum span keeps the request sane
    const tiny = zoomBox([1, 1, 1.0001, 1.0001], 0.5, 0.5, 10);
    expect(tiny[2] - tiny[0]).toBeGreaterThanOrEqual(0.02 - 1e-9);
  });

  test("panBox: pans by a fraction of the span without changing size, clamped to the world", () => {
    const box: [number, number, number, number] = [-2, -1, 2, 1];
    const p = panBox(box, 0.5, -0.5);
    expect(p[2] - p[0]).toBe(4);
    expect(p[0]).toBeCloseTo(-4, 6);
    expect(p[3]).toBeCloseTo(2, 6);
    const clamped = panBox([-180, -90, -176, -86], 1, 1);   // pan left/down past the world edge
    expect(clamped[0]).toBe(-180);
    expect(clamped[1]).toBe(-90);
  });
});

// ---- shared map → Background query context -------------------------------------------------------
test.describe("queryContext (map → Background hand-off)", () => {
  const TILE_BOX: [number, number, number, number] = [-118.45, 33.7, -118.05, 34.3];

  test("retrievalHref carries a complete box so Retrieval auto-runs once on open", () => {
    const href = retrievalHref({ bbox: TILE_BOX, tile: "11SLT86", level: "GUNW,GCOV", from: "2026-07-02", to: "2026-10-30" });
    expect(href).toBe(
      "/background/retrieval?west=-118.45&south=33.7&east=-118.05&north=34.3&tile=11SLT86&level=GUNW%2CGCOV&from=2026-07-02&to=2026-10-30");
    // and the URL round-trips through the page's own validated parser
    const st = parseExplorerState(new URLSearchParams(href.split("?")[1]));
    expect([st.west, st.south, st.east, st.north]).toEqual(TILE_BOX);
    expect(st.level).toBe("GUNW,GCOV");
    expect(st.from).toBe("2026-07-02");
  });

  test("an empty context keeps the plain links (nothing invented)", () => {
    expect(retrievalHref({})).toBe("/background/retrieval");
    expect(mapHref({})).toBe("/map");
  });

  test("mapHref restores the pin, falling back to the tile id", () => {
    expect(mapHref({ lat: 34.05, lon: -118.25 })).toBe("/map?lat=34.05&lon=-118.25");
    expect(mapHref({ tile: "11SLT86" })).toBe("/map?tile=11SLT86");
  });

  test("sanitize drops anything malformed instead of trusting storage", () => {
    expect(sanitize({ lat: 999, lon: 5 })).toEqual({});
    expect(sanitize({ bbox: [10, 5, -10, 6] })).toEqual({});               // west > east
    expect(sanitize({ bbox: [0, 5, 1, 5] })).toEqual({});                  // south == north
    expect(sanitize({ tile: "DROP TABLE", level: "XYZ", from: "yesterday" })).toEqual({});
    expect(sanitize("nope")).toEqual({});
    expect(sanitize({ lat: 34, lon: -118, bbox: TILE_BOX, level: "gunw,gcov", from: "2026-07-02" }))
      .toEqual({ lat: 34, lon: -118, bbox: TILE_BOX, level: "GUNW,GCOV", from: "2026-07-02" });
  });

  test("toDateOnly turns the engine's ISO window into what <input type=date> accepts", () => {
    expect(toDateOnly("2026-07-02T10:00:00Z")).toBe("2026-07-02");
    expect(toDateOnly("garbage")).toBeUndefined();
    expect(toDateOnly(undefined)).toBeUndefined();
  });
});

test.describe("normalizeEngineUrl (paste-an-engine-URL box)", () => {
  const ok = (s: string) => {
    const r = normalizeEngineUrl(s);
    if (!r.ok) throw new Error(`${s} should normalise: ${r.error}`);
    return r.url;
  };
  test("accepts a plain tunnel URL and strips slashes / api tails", () => {
    expect(ok("https://abc-def.trycloudflare.com")).toBe("https://abc-def.trycloudflare.com");
    expect(ok("  https://abc-def.trycloudflare.com/  ")).toBe("https://abc-def.trycloudflare.com");
    expect(ok("https://abc-def.trycloudflare.com/api/health")).toBe("https://abc-def.trycloudflare.com");
    expect(ok("https://abc-def.trycloudflare.com/api/")).toBe("https://abc-def.trycloudflare.com");
  });
  test("accepts the Colab cell's NEXT_PUBLIC_ENGINE_API=… line, with or without quotes", () => {
    expect(ok("NEXT_PUBLIC_ENGINE_API=https://x.trycloudflare.com")).toBe("https://x.trycloudflare.com");
    expect(ok('NEXT_PUBLIC_ENGINE_API="https://x.trycloudflare.com"')).toBe("https://x.trycloudflare.com");
  });
  test("adds a scheme: https for hosts, http for localhost", () => {
    expect(ok("x.trycloudflare.com")).toBe("https://x.trycloudflare.com");
    expect(ok("127.0.0.1:8900")).toBe("http://127.0.0.1:8900");
    expect(ok("localhost:8900/")).toBe("http://localhost:8900");
  });
  test("rejects empty, spaced, non-http and remote-http input", () => {
    for (const bad of ["", "   ", "two words", "ftp://x.com", "http://x.trycloudflare.com", "https://"]) {
      expect(normalizeEngineUrl(bad).ok, `${bad} should be rejected`).toBe(false);
    }
  });
});
