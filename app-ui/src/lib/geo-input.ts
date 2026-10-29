/** Coordinate input parsing — pure, unit-tested, display-only.
 *  Accepted (DEC7: coordinates only, no geocoding):
 *    "34.05, -118.25" · "34.05 -118.25" · "34.05N 118.25W" · "N 34.05, W 118.25" · "34.05° N" · NBSP
 *  Rejected with a reason (never silently swapped or coerced):
 *    latitude out of range (118 → rejected, never swapped), longitude out of range, NaN, empty,
 *    exponent form, comma-decimal ("34,05"), anything that is not exactly two numbers. */

export interface ParsedCoords {
  lat: number;
  lon: number;
}

type ParseResult = { ok: true; value: ParsedCoords } | { ok: false; error: string };

export function parseCoordinates(input: string): ParseResult {
  const cleaned = input.replace(/\u00a0/g, " ").trim();
  if (!cleaned) return { ok: false, error: "enter coordinates, e.g. 34.05, -118.25" };
  if (/\d\s*[eE]\s*[+-]?\d/.test(cleaned)) {
    return { ok: false, error: "exponent form is not accepted — use plain degrees" };
  }
  // a comma-DECIMAL ("34,05") is rejected: a comma immediately between two numbers where NEITHER
  // side already has a period. "34.05, -118.25" and "-33.87,151.21" are separators and pass.
  const commaDecimal = /(^|\s)(\d{1,3}),(\d{1,3})(?=\s|$)/.exec(cleaned);
  if (commaDecimal && !commaDecimal[0].includes(".")) {
    return {
      ok: false,
      error: "comma-decimal is not accepted — use a period for decimals and a comma between the pair",
    };
  }
  // detach hemisphere letters from numbers so tokens are clean
  const spaced = cleaned
    .replace(/[°]/g, " ")
    .replace(/([0-9])\s*([NSEWnsew])/g, "$1 $2")
    .replace(/([NSEWnsew])\s*([0-9-])/g, "$1 $2");

  const tokens = spaced.split(/[\s,;/]+/).filter(Boolean);
  const numbers: number[] = [];
  const signs: number[] = []; // sign per number, from an attached hemisphere letter (1 or -1); 0 = none
  let pendingSign = 0;
  let lastWasNumber = false;
  let style: "prefix" | "suffix" | null = null; // "N 34.05" vs "34.05 N" — decided by the first letter
  const letters: string[] = [];
  for (const tok of tokens) {
    if (/^[NSEW]$/i.test(tok)) {
      const L = tok.toUpperCase();
      letters.push(L);
      const sign = L === "S" || L === "W" ? -1 : 1;
      if (style === null) style = lastWasNumber ? "suffix" : "prefix";
      if (style === "suffix") {
        signs[signs.length - 1] = sign;   // "34.05 N": the letter signs the number it follows
      } else {
        pendingSign = sign;               // "N 34.05": the letter signs the number that follows
      }
      lastWasNumber = false;
      continue;
    }
    const n = Number(tok);
    if (!Number.isFinite(n)) {
      return { ok: false, error: `"${tok}" does not read as a number` };
    }
    numbers.push(n);
    signs.push(pendingSign);
    pendingSign = 0;
    lastWasNumber = true;
  }
  if (letters.length > 0 && letters.length !== 2) {
    return { ok: false, error: "give both hemispheres (N/S and E/W) or none" };
  }
  if (numbers.length !== 2) {
    return {
      ok: false,
      error: "expected exactly two numbers: latitude then longitude (e.g. 34.05, -118.25)",
    };
  }
  const lat = signs[0] !== 0 ? Math.abs(numbers[0]) * signs[0] : numbers[0];
  const lon = signs[1] !== 0 ? Math.abs(numbers[1]) * signs[1] : numbers[1];
  return check(lat, lon);
}

function check(lat: number, lon: number): ParseResult {
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
    return { ok: false, error: "those do not read as two numbers" };
  }
  if (Math.abs(lat) > 90) {
    return { ok: false, error: `latitude ${lat} outside [-90, 90] — latitude comes first` };
  }
  if (Math.abs(lon) > 180) {
    return { ok: false, error: `longitude ${lon} outside [-180, 180]` };
  }
  return { ok: true, value: { lat, lon } };
}
