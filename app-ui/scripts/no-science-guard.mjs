#!/usr/bin/env node
/** No-science CI guard — the UI is a THIN CLIENT of engine/api (port design decision 1).
 *
 *  Scans app-ui/src for science code that must never appear in the browser:
 *   - science identifiers / model names (okada, nodal planes, LOS math, thresholds, …)
 *   - numeric constants from the manifest appendix (screening values are the ENGINE's)
 *   - imports of anything except the transport layer in src/lib
 *
 *  A hit is a build-breaking CI failure. Exempt: transport types, UI labels that merely
 *  NAME statuses/parameters, this script, and tests.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

const SRC = new URL("../src", import.meta.url).pathname;

const FORBIDDEN_PATTERNS = [
  // elastic models & geodesy the engine owns
  /\bokada\b/i,
  /\bnodal[_ -]?plane/i,
  /\brake\b/i,
  /\bdc3d\b/i,
  /\bmoment[_ -]?tensor/i,
  /\bhanks\b|kanamori/i,
  /\bcoppersmith\b/i,
  /\bdifferential[_ -]?evolution\b/i,
  /\bleast[ _-]?squares\b/i,
  // LOS / geometry math
  /los[_ -]?project|los[_ -]?enu|losUnitVector|projectDisplacementToLOS/i,
  /\bhaversine\b/i,
  // statistics the engine owns
  /\bpearson\b/i,
  /mad[_ -]?sigma|sigma[_ -]?eff\b/i,
  /cram[eé]r[- ]rao/i,
  /\bmulberry32\b/i,
  // evidence/score computations
  /within[_ -]?tier[_ -]?score\b/i,
  /amplitude[_ -]?ratio\s*=/,
  /pattern[_ -]?correlation\s*=/,
  /detection[_ -]?floor\s*[=:]/,
  /significance[_ -]?k\s*[=:]/,
  /coherence[_ -]?threshold\s*[=:]/,
  // source scaling computed client-side
  /momentFromMw|ruptureAreaFromMw|slipFromMoment\b/i,
];

// The Speckle landing story file is pure PRESENTATION copy — it NAMES engine
// concepts in prose ("nodal plane" in the Chapter VIII earthquake narrative),
// taken verbatim from the engine repo's manifests/docs, and computes nothing.
// Same exemption category as the "UI labels that merely NAME" rule above.
const ALLOWED_FILES = /no-science-guard|\.test\.|playwright|speckle[/\\]data\.ts$/;

function* walk(dir) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    const st = statSync(p);
    if (st.isDirectory()) yield* walk(p);
    else if (/\.(ts|tsx)$/.test(name)) yield p;
  }
}

let failures = 0;
for (const file of walk(SRC)) {
  if (ALLOWED_FILES.test(file)) continue;
  const rel = relative(SRC, file);
  const lines = readFileSync(file, "utf8").split("\n");
  lines.forEach((line, i) => {
    for (const re of FORBIDDEN_PATTERNS) {
      if (re.test(line)) {
        console.error(`✗ ${rel}:${i + 1} — science code in the UI (${re}): ${line.trim().slice(0, 120)}`);
        failures += 1;
      }
    }
  });
}

// src/lib is transport-only: the MODULE TARGETS it pulls in are checked across the
// whole file (multi-line imports included)
const LIB_OK_MODULES = ["next", "react", "react-dom", "leaflet", "lucide-react"];
for (const file of walk(join(SRC, "lib"))) {
  if (ALLOWED_FILES.test(file)) continue;
  const rel = relative(SRC, file);
  const content = readFileSync(file, "utf8");
  for (const m of content.matchAll(/from\s+["']([^"']+)["']/g)) {
    const target = m[1];
    const ok = target.startsWith(".") || target.startsWith("@/") ||
      LIB_OK_MODULES.some((p) => target === p || target.startsWith(p + "/"));
    if (!ok) {
      console.error(`✗ ${rel} — src/lib must stay transport-only (import of "${target}")`);
      failures += 1;
    }
  }
}

if (failures) {
  console.error(`\nno-science guard: ${failures} violation(s). The UI is a thin client of engine/api — move the science into the engine.`);
  process.exit(1);
}
console.log("no-science guard: OK — src/ carries no science code.");
