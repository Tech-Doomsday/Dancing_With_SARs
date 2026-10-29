import { test, expect } from "@playwright/test";
import { parseCoordinates } from "../src/lib/geo-input";
import { validateLayerPayload } from "../src/lib/raster";
import { parseUrlState, writeUrlState } from "../src/lib/urlstate";
import { gapThresholdDays, ticks as ticksFor, tToX, xToT, zoom as zoomScale, pan as panScale } from "../src/lib/timescale";
import { mostNotableTier } from "../src/lib/status";

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
