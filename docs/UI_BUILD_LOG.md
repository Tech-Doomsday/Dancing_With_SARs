# UI BUILD LOG — globe landing experience + Granule Explorer

Ledger for the globe-task prompt (§0 operating rules). One checkbox per deliverable; a box is
ticked only after its gate passes. "already present" = found in the pack with its test passing.
One line appended per commit: phase, commit, what was verified.

Environment note (baseline): the sandbox was missing two packages that
`requirements-retrieval.txt` already declares (`asf_search`, `zarr`) plus `shapely`; installing
them (no code change) moved the pytest baseline from "30 failed, 402 passed, 18 errors" to the
recorded baseline below. No engine file was edited to achieve this.

## Baseline (recorded before any change)

- [x] `python -m pytest -q` baseline recorded: **10 failed, 440 passed, 6 skipped**
  (pre-existing failures, all on the earthquake_coseismic science path in this environment:
  the synthetic-scene pattern-correlation screen returns −0.36 ≤ 0.25 → `NOT_SUPPORTED`, which
  fails test_api/test_debug_hub/test_sar_debug/test_g0_los_sign and the EQ pipeline suite;
  classification: UNPROVEN as environmental — test_g0_los_sign::test_g0c expects the normal scene's
  correlation > +0.25 and got −0.36, which is the sign-leak symptom that gate exists to catch; the
  scipy-version explanation was never reproduced. Recorded, not touched; see the post-audit section)
- [x] `cd app-ui && npm ci && npm run ci` baseline recorded — **green**
  (typecheck + no-science guard + build; route sizes: / 21.8 kB, /workbench 7.38 kB First Load)
- [x] `cd app-ui && npm run test:e2e` baseline recorded — **26 passed, 1 skipped, 0 failed**
  (an initial 10-failed run was purely the missing matching Playwright chromium build; after
  `npx playwright install chromium` the suite is green — environment, not code)
- [x] Pack integrity: MANIFEST.json verified — 458/460 sha256 match; 2 mismatches are
  generated HTML report artifacts (`tools/report.html`, `tools/sar_debug_report.html`), no source file affected

## Phase 0 — spikes (app-ui/spikes/, deleted after)

- [x] S1 maplibre-gl 5.x globe + Esri raster + image-source overlay — **PASS (2/2 Playwright specs)**.
  Verbatim: state `{"mounts":2,"cleanups":1,"errors":[],"projection":"globe"}` (StrictMode double-mount
  cleans up via map.remove()); non-background canvas coverage 1.0; LA box overlay interior
  `[201,199,188]` vs exterior `[133,136,119]` (diff 200) — overlay sits inside the GeoJSON outline
  of the same corners; polar (lat 80) interior `[158,157,155]` vs exterior alpha-0 (diff 470);
  globe↔flat toggle verified (`getProjection().type` globe↔mercator). Esri metadata (U2):
  **maxzoom 23** (max tileInfo.lods level, 24 lods, 0..23), **copyrightText
  "Source: Esri, Vantor, Earthstar Geographics, and the GIS User Community"** (exact, 256×256 tiles).
  **U6 antimeridian verdict (empirical, screenshots + equator scans kept in the spike run output):**
  - wrapped corners (W=179.985, E=−179.985): the image source stretches the LONG way round
    (~359.97°) — terracotta half west of the seam, teal half east, overlay far outside the outline;
  - unwrapped corners (E=180.015): overlay not rendered at the seam + basemap tile enumeration
    breaks (`x=2048 … outside of bounds`);
  - **fix (verified, ported to production): split seam-crossing overlays at ±180 into two image
    sources, each with its own horizontal crop of the texture** — interior `[120,79,74]` vs
    exterior `[9,25,38]` (diff 201), overlay confined to the outline. Implemented as
    `seamSplits()` in `src/lib/raster.ts` + `cropDataUrl()` in `GlobeMap.tsx`.
  Also re-confirmed in-spike: `raster-resampling: "nearest"` renders black on maplibre 5.24
  (production keeps default linear + pre-upscaled canvas).
- [x] S2 headless WebGL — **WORKS**. Flags that work:
  `--use-angle=swiftshader --enable-unsafe-swiftshader --ignore-gpu-blocklist --enable-webgl`
  (chromium headless shell 156 / Playwright v1248). Screenshots are non-blank; zero console
  errors; visual tests are NOT skipped.
- [x] S3 engine presence — `python3 tools/spike_s3.py` captured verbatim:
  `auth_available: False` / `auth_repr_safe: EarthdataAuth(token=None, username=None, password=None)`
  / `closed: True`; `tile_at(-118.25, 34.05) -> 11SLT86 epsg 32611 center [-118.24554, 34.01934]`;
  pyproj 3.7.2. (No worker-thread blocker for NisarClient.)
- [x] S4 geolocation — behaviour relied on documented in `src/lib/geolocation.ts` header; wrapper
  branch unit tests added to `tests-e2e/unit.spec.ts` (granted/prompt/denied/query-rejected→prompt/
  no-permissions/no-geolocation + success/denied/timeout/unavailable/none + DEC2 options
  `{enableHighAccuracy:false, timeout:8000, maximumAge:600000}`); 19/19 unit tests pass.
- [x] Spikes deleted after recording (app-ui/spikes/ + src/app/spikes/ + spike test-results).

## Phase 1 — engine/API: verify first, implement only gaps

- [x] E1 **already present** — `engine/api/routes_scanner.py` `tile_at_route` (point OR `tile=` id mode);
      tests `tests/test_scans_api.py::test_tile_at_known_values / test_tile_at_edges_and_errors /
      test_tile_at_by_tile_id` (400 answers carry the engine's own ValueError text)
- [x] E2 **already present** — `POST /api/scans` = a run (same polling); 403 disabled / 503
      credentials-absent (names env vars) / 409 busy (names the active run); progress
      {stage, step_index, step_total, tile}; cached with default params (custom params never);
      failure is a run status + exception text; tests `tests/test_scans_api.py` (11 tests, green)
- [x] E3 **already present** — `GET /api/runs/{id}?slim=1` strips grids, keeps layer inventory
      (`routes_runs._slim_state`); pipeline-scoped layer route with 422-on-violation; run fields
      `tile`/`bundle_id`/`kind`; results carry `tier` + `tier_label` (`engine/verdict/calibrate.py`)
- [x] E4 **already present** — `engine/lib/layer_payload.py` encoder/validator; every implemented
      pipeline incl. tsunami emits through it; 422 with problem list, never 500;
      `tests/test_layer_contract.py` green (row0-is-north + mercator-rows noted)
- [x] E5 **already present** — `detail.detections[]` (id/label/kind/layer/centroid/bbox/area/polarity/metrics)
      + `detections_truncated`; pinned by `test_layer_contract.py` (geometry equality) and
      `test_scans_api.py::test_scan_happy_path_is_a_run_with_tiers_and_detections`
- [x] E6 **already present** — `/api/health` reports `scans_enabled`, `earthdata_credentials`
      ("present"/"absent"), `cache_write` (+ `granule_search`); test `test_api.py::test_health_and_pipelines`
- [x] E7 **already present** — NO_SIGNAL has the quiet tier (`calibrate.py` D15;
      `test_status_table_complete.py`), stale runs reset on engine start (`test_scans_api.py::
      test_stale_run_is_reset_on_start_not_409_forever`), CORS via NISAR_UI_ORIGINS
      (`engine/api/main.py::_ui_origins`, no wildcard)
- [x] E8 **added (+11 tests)** — bbox-based `GET /api/granules` alongside the legacy per-tile
      shape (which is UNCHANGED for the old Data tab):
      `engine/common/granule_id.py` (filename decoder — token layout VERIFIED against live CMR
      metadata: [cycle, track, A/D, frame, sec_cycle, bandwidth(MHz×100), pol, 4 timestamps,
      CRID, orbit, status, center, version] for pair products; single-acquisition GCOV layout
      with the frequency token; SH/SHNA→HH verified; multi-frequency bandwidth kept raw),
      `engine/api/granules_bbox.py` (enrichment + P01-convention stacks with engine-computed
      epochs/gaps_days/min/median/max + nominal_repeat_days: null — no verified engine source),
      `GranuleRef.extra` (provider metadata pass-through: collectionName/crid/orbitType/
      mainBandPolarization/rangeBandwidth), `JobRunner.granule_search_bbox` (same single worker
      pool), route bbox mode with antimeridian-box 400, south>=north 400, unknown level 400,
      429 + Retry-After sliding window, truncation + degraded flags, credentials 503.
      **Test vector reproduced**: GUNW; ascending; track 34; frame 19; ref 2026-09-20T13:08:23Z
      (cycle 31); sec 2026-10-14T13:08:23Z (cycle 33); baseline 24 d; polarization HH;
      bandwidth 40 MHz; CRID P05023; collection NISAR_L2_GUNW_PROVISIONAL_V1 (provider);
      version 001. **NOT verifiable**: the orbit letter — CMR shows BOTH N and F tokens with
      ORBIT_TYPE=MOE, so "N = near-real-time" cannot be established; the engine keeps it raw
      (`orbit_accuracy.filename = "N"`, flag "N (orbit token)") and the report says so.
- [x] E9 **already present** — `supabase/migrations/0002_locations_and_featured.sql`
      (verdicts location columns + public-read featured_areas), `tools/feature_area.py`
      add/list/remove (engine-resolved tiles only), `tools/backfill_supabase.py --fill-locations/
      --retier`; `tests/test_phase2_tools.py` green
- [x] E10 **already present** — docs reflect the above (IMPLEMENTATION_PLAN §10.1 lists tile-at/
      scans/slim/pipeline-scoped layers; §11.4 carries the SUPERSEDED note; SUPABASE_CACHE.md
      documents 0002 + the featured-area CLI + backfill steps)
- [x] Gate: `python -m pytest -q` — 451 passed / 10 failed / 6 skipped = the recorded baseline
      (same 10 pre-existing EQ-path failures; **no new failures**)

## Phase 2 — Supabase

- [x] Migration SQL INSPECTED, not executed (no Postgres in this sandbox — stated in the report):
      `0002_locations_and_featured.sql` adds the verdicts location columns (center_lon/center_lat/
      bbox_lonlat + range checks + tile index) and creates `featured_areas` (public SELECT policy
      only; insert/update/delete/truncate revoked from anon+authenticated; service-role bypasses
      RLS). It touches no verdicts policy (0001's `verdicts_public_read` stays). featured_areas is
      seeded EMPTY (no INSERT anywhere in the migration) — the owner adds real entries with
      `tools/feature_area.py`. Idempotent (if-not-exists everywhere).
- [x] `tests/test_phase2_tools.py` green (6 passed) — the CLI/feature-area contract

## Phase 3 — UI

- [x] 7.1 **already present + extended** — route groups existed ((globe)/(site), URLs unchanged);
      ADDED: `app/(globe)/granules/page.tsx` (full-bleed dark, Suspense), shared `AppNav`
      (Globe · Granules · Workbench) rendered by (site)/layout.tsx header, the globe page and the
      Explorer; root layout metadata already describes the globe app
- [x] 7.2 **already present** — search pill (explicit Lat/lon, paste parser, Center on my location,
      Globe/Flat), panel states, mobile BottomSheet (3 snap points, draggable), tokens, space
      background, floating cream cards with blur; map text never bare
- [x] 7.3 **already present + spike-verified** — Esri maxzoom 23 + exact attribution; projection
      toggle persisted (try/catch localStorage); pin→tile-at debounced 300 ms + AbortController +
      out-of-order ignore; cached/featured pins clustered with tier colours; / focuses latitude,
      Esc clears, aria-labels; WebGL fallback message (really exercised in webgl-off.spec.ts);
      unmount: map.remove() + ResizeObserver disconnect (S1 StrictMode-verified). ADDED: the U6
      antimeridian seam-split for overlays
- [x] 7.4 **already present + extended** — Explore (Featured|Cached with grouping/tier chips/
      filters/honest empties), Pin selected (Scan gating with the exact reasons, precise copy,
      "N of M pipelines are not modelled yet" from /api/pipelines), Running (verbatim stages,
      step_index/step_total, elapsed, aria-live, leave-and-return, 409 with Follow run), Results
      (tier-grouped findings, rank-score meter with the not-a-probability note, layer chips +
      opacity, detections fitBounds, Evidence reuse, layer inventory from result.layers, live
      slim + cached forBundle through one LayerSource). ADDED: the §7.4 "Check coverage" button
      (GET /api/granules for the tile bbox: counts per stack, last acquisition, largest gap,
      truncation/degradation notices, Open in Granule Explorer; never automatic)
- [x] 7.5 **already present** — validateLayerPayload before use, decode→ImageData→canvas→dataURL→
      image source, ONE colour implementation (colorize + legendStops), footer note, LRU 12
- [x] 7.6 **already present** — lat/lon/z/tile/bundle/pipeline/layer/scan (+panel), validated,
      debounced router.replace, invalid → default view
- [x] 7.7 **already present + verified** — strict TS (no any/@ts-ignore), no dangerouslySetInnerHTML,
      AA contrast, focus-visible; build route sizes: maplibre lives in ONE async client chunk,
      zero references from /workbench, /runs/*, /verdicts/* (verified in the build output)
- [x] 7.8 **ADDED** — /granules Granule Explorer: top bar (box/level multi-select/UTC from-to/
      Search/Reset, ?tile= resolved by the ENGINE), filter row (day-of-month, UTC time-of-day incl.
      midnight wrap, applies-to, direction, track, text, only-in-view, Reset; pure parsers, invalid
      → inline message, not applied), summary strip (N of M · stacks · distinct epochs · first→last ·
      per-stack min/med/max gap · time since last), canvas timeline (adaptive UTC ticks hours→months with year labels
      with label culling, gridlines, now marker, hover crosshair + tooltip, per-stack lanes A-then-D
      (cyan/violet), greedy-packed sub-lanes, GUNW bars with acquisition dots, GCOV dots, engine
      day-gap brackets "Nd" at the band bottom, amber only when the engine supplies nominal_repeat
      (it never does — honest absence), selection amber + shift/ctrl extend, brush minimap with
      density histogram, wheel zoom at cursor, drag pan, dbl-click zoom-to-bar, ←/→/+/−/↑/↓/Enter/
      Home keys, roving focus + aria-live readout, DPR-aware, resize-safe, binary-search hit test),
      detail panel (full name + copy, MapLibre FLAT footprint mini-map with the whole stack,
      selection amber, box dashed amber, direction chip from the engine, key/value table, unknown
      = "—", undecoded raw + tag, Show on globe / Scan this tile bridges — never auto-scan),
      List view (sortable, same filters + selection), footer (query echo, generated_at, UTC note),
      states (offline banner with URL, searching spinner w/ abort, zero results, 429, truncated +
      degraded banners, invalid box inline), URL state west/south/east/north/level/from/to/track/
      dir/sel (validated, restorable, boot auto-search ONCE for pasted links)
      Perf: 5,000-bar pan measured avg 17.5 ms/frame, p95 16.8 ms (headless swiftshader)

## Phase 4 — tests, gates, visual verification

- [x] Pure-logic Playwright specs — pre-existing (parseCoordinates, validateLayerPayload, urlstate,
      timescale, status precedence) + ADDED geolocation branches (S4) and explorer logic
      (parseDayOfMonth incl. reversed-range rejection, parseTimeOfDay incl. midnight wrap, tick
      generator spans 3 h/3 d/3 mo/3 y/3 y-across-year-boundary monotonic bounded, interval packer
      no-overlap deterministic, filter combinations with applies-to modes, explorer URL state
      round-trip + junk) — 25 unit tests green
- [x] Browser engine-DOWN specs — pre-existing smoke suite green (globe canvas or WebGL message,
      search fields, engine-offline notice with URL, Scan disabled with reason, no console errors,
      no infinite spinners; /workbench old home; /runs/run-does-not-exist alert) + ADDED
      /granules offline banner + disabled Search
- [x] Browser specs with Supabase fixtures — pre-existing globe-cache suite green (featured/cached,
      grouping, click→fly→results→overlay→legend, empty-featured, 401/500 error states)
- [x] Browser with the real engine up — pre-existing LIVE suite green (uvicorn + temp
      NISAR_DATA_DIR via tests/common_fixtures.py, SYNTHETIC-labelled): health strip, tile-at flow,
      Data tab (26 d gap label, flipped pair), full scan flow, /workbench POST /api/runs
- [x] Explorer browser specs (ADDED, 7 tests) with the COMMITTED fixture generated through the
      REAL engine enrichment (tools/gen_explorer_fixture.py → tests-e2e/fixtures/granules-bbox.json):
      axis + N-of-M + the owner's test vector fields in the detail panel, filter chip changes,
      truncated/degraded banners, offline, 429, invalid box (no query fired), List-view parity,
      5,000-bar pan timing
- [x] Screenshots 1440×900 + 390×844 — 22 shots captured and each OPENED and critiqued:
      initial globe, pin+footprint, scan progress, results+overlay+legend, cached tab, featured
      empty, engine offline, WebGL unavailable (real GPU-less chromium), Explorer full range /
      zoomed-to-days-with-gap-brackets / selected-pair-with-footprint-map / truncated / List /
      mobile drawer. Fixes applied from the critique: the transport layer had eaten a `$` in the
      summary-strip template (byte-verified fix), gap brackets moved clear of stack headers, axis
      "UTC" label moved out of the last tick, axis label collision culling for narrow widths,
      cache-error text prefixed ("the verdict cache is unreachable: …"), explorer screenshot spec
      selects a RESOLVED pair. Remaining known flaws (honest): the engine-offline shot shows the
      map chrome before Esri imagery paints (honest, transient); the dev-only Next "1 Issue" badge
      (the dead Supabase port's ERR_CONNECTION_REFUSED in the live env) appears in some shots and
      overlaps the sheet handle at 390 px; the fixture GUNW ids carry bandwidth 4005 (multi-frequency)
      so Range bandwidth shows "—" with the raw-token flag — correct honest behaviour
- [x] Final gates: pytest 451 passed / 10 failed / 6 skipped = EXACTLY the recorded baseline
      failure set (EQ-path); `npm run ci` green (typecheck + guard + build); e2e 44 passed /
      1 skipped; live e2e 7 passed; no-science guard UNMODIFIED (sha256 == MANIFEST) + green

## Phase 5 (optional) — SAR base rasters

- [x] NOT REACHED (declared honestly): Phases 0–4 consumed the session; per §9 the proposal-first
      step (which bundle arrays are worth showing) is listed as a follow-up instead of a stub

## Commits

- baseline | 0a47d12 | pack extracted + verified; pytest/npm/e2e baselines recorded
- baseline | c56ddbe | e2e green after browser install (26P/1S); ledger updated
- phase 0  | 41b5e7b | spikes S1-S4 green (ledger); U6 antimeridian fix ported (seamSplits + cropDataUrl); spike files deleted
- phase 1  | b93284b | E1-E7+E9+E10 verified present; E8 added (bbox /api/granules + CMR-verified decoder + 11 offline tests); gate green (451P/10F = baseline)
- phase 2  | 8846a17 | 0002 migration inspected (public-read featured_areas, verdicts policies untouched, seeded empty)
- phase 3  | 3222638 | shared AppNav; /granules Explorer (canvas timeline, brush, filters, summary, detail, list, URL state); Check coverage on the pin panel; +6 unit specs; CI green
- phase 4  | f3eac74 | fixture-generator through real enrichment + 7 Explorer specs + offline/LIVE screenshot suites; §8.3 critique fixes; all gates green; ledger complete

---

# FINAL REPORT (§11)

## 1. Delivered per phase (commit hashes)

| Phase | Commit | Delivered |
|---|---|---|
| baseline | 0a47d12 / c56ddbe | pack verified (458/460 manifest hashes; 2 generated HTML artifacts differ), pytest baseline 10F/440P/6S recorded, npm ci green, e2e 26P/1S (after the matching Playwright chromium install — environment, not code) |
| 0 — spikes | 41b5e7b | S1/S2/S3/S4 recorded (below); the U6 antimeridian finding ported to production (`seamSplits()` in `src/lib/raster.ts` + `cropDataUrl()` in `GlobeMap.tsx`); spike files deleted after recording |
| 1 — engine | b93284b | E1–E7, E9, E10 verified ALREADY PRESENT (each with its passing test, cited in the ledger); E8 ADDED: bbox `GET /api/granules` (enrichment `engine/api/granules_bbox.py`, filename decoder `engine/common/granule_id.py` verified against LIVE CMR metadata, provider pass-through `GranuleRef.extra`, single-flight worker path, antimeridian 400, 429 + Retry-After) + 11 offline tests |
| 2 — Supabase | 8846a17 | migration 0002 INSPECTED (not executed — no Postgres here); verdicts read policies untouched; featured_areas public-read, service-write, seeded EMPTY |
| 3 — UI | 3222638 | shared AppNav; `/granules` Granule Explorer (§7.8: canvas timeline, brush minimap, filters, summary strip, detail panel with MapLibre-flat footprint mini-map, List view, URL state, honest states); §7.4 "Check coverage" on the pin panel; explorer unit specs |
| 4 — tests/visual | f3eac74 | committed fixture generated through the REAL enrichment (`tools/gen_explorer_fixture.py`), 7 Explorer browser specs, offline + LIVE screenshot suites, §8.3 critique fixes, all gates green |
| final | (this) | docs reflect E8 (main.py docstring + IMPLEMENTATION_PLAN); ledger fully ticked |

E-rows: E1–E7, E9, E10 = **already present**; E8 = **added (+11 tests)**.

## 2. Verified by running (commands and counts)

- `python -m pytest -q` (full suite, final): **451 passed / 10 failed / 6 skipped** — the 10 failures
  are EXACTLY the recorded baseline set (earthquake_coseismic science path; see §3), no new failures.
- `cd app-ui && npm ci && npm run ci`: **green** (tsc --noEmit + no-science guard OK + next build;
  route sizes: / 25.4 kB, /granules 13.7 kB, /workbench 7.96 kB First Load; maplibre-gl lives in one
  async client chunk with ZERO references from /workbench, /runs/*, /verdicts/*).
- `npm run test:e2e`: **44 passed / 1 skipped** (unit 25, smoke, globe-cache fixtures, explorer 7,
  offline screenshots, webgl-off).
- `npm run test:e2e:live` (uvicorn engine + temp NISAR_DATA_DIR seeded via tests/common_fixtures.py,
  SYNTHETIC-labelled): **7 passed / 0 failed** (health strip, tile-at flow, Data tab, full scan flow,
  /workbench POST /api/runs, screenshots incl. the Explorer).
- Pack integrity: MANIFEST.json sha256 458/460 (the 2 mismatches are generated report HTML files).
- E8 test vector reproduced through the HTTP route (`tests/test_granules_bbox.py`).
- 5,000-bar pan timing (headless, swiftshader): avg 17.5 ms/frame, p95 16.8 ms (≈57 fps).

## 3. Not verified, and why

- **The orbit letter in NISAR filenames** ("N"/"F" after the CRID): CMR shows BOTH tokens with
  `ORBIT_TYPE: MOE`, so "N = near-real-time" is NOT establishable from live data, and the naming
  convention PDF (NISAR D-102255C) was not retrievable. The engine keeps the token RAW
  (`orbit_accuracy.filename = "N"`, flag "N (orbit token)", UI shows "ASF: MOE; filename: N" +
  UNDECODED tag). The task's expected "filename: near-real-time" is therefore deliberately NOT
  reproduced — the honest-raw rule (§0) wins.
- **The Supabase migration was not executed** (no Postgres in this sandbox); the SQL is inspected
  and the featured-area/backfill CLIs are covered by `tests/test_phase2_tools.py` (6 passed).
- **Live ASF behaviour of the bbox search**: verified offline through the retriever seam
  (FakeRetriever) + the CMR-side filename verification; asf_search's `dataset="NISAR"` currently
  lists only clock/cal products (L2 collections not yet in its dataset index) — an OPERATOR concern
  recorded in §6, not fixable without changing the search layer (retrieval was touched ONLY to pass provider
  metadata through: `GranuleRef.extra` in `types.py` + its population in `nisar_client.py`).
- The two baseline test groups that depend on external services (EQ-science screen) are recorded
  as baseline, not verified anew.
- `navigator.geolocation` was verified by unit tests of the wrapper branches + the documented
  contract; on-device permission prompts were not exercised (no device).

## 4. Spike results S1–S4 (verbatim in the ledger; summary)

- **S1** maplibre-gl 5.24.0 globe + Esri + image overlays: PASS. StrictMode `{"mounts":2,
  "cleanups":1,"errors":[]}`; Esri **maxzoom 23**, copyrightText exact
  ("Source: Esri, Vantor, Earthstar Geographics, and the GIS User Community"); LA-box overlay
  interior/exterior pixel diff 200; polar (lat 80) diff 470; globe↔flat toggle works.
  **U6 verdict**: wrapped seam-crossing corners stretch the image the long way round (~360°);
  unwrapped corners break tile enumeration ("x=2048 outside of bounds"); the SPLIT-at-±180 fix
  renders correctly (diff 201) and is ported to production.
- **S2** headless WebGL: WORKS with `--use-angle=swiftshader --enable-unsafe-swiftshader
  --ignore-gpu-blocklist --enable-webgl`; screenshots non-blank; zero console errors; visual tests
  never skipped.
- **S3**: `auth_available: False`, `EarthdataAuth(token=None, username=None, password=None)`,
  `closed: True`; `tile_at(-118.25, 34.05) -> 11SLT86 epsg 32611`; pyproj 3.7.2.
- **S4**: wrapper branches (granted/prompt/denied/query-rejected→prompt/no-permissions/
  no-geolocation; success/denied/timeout/unavailable/none; DEC2 options) unit-tested — 25/25.

## 5. Deviations with reasons (all conservative, all recorded)

1. **Nominal repeat**: never rendered — the engine has no verified source for
   `nominal_repeat_days` (it stays null), so the amber "missing cycle" path exists but is
   unreachable. The task forbids showing one unless the engine supplies it.
2. **Explorer mobile detail**: a capped (max-h-45vh) scrollable bottom area instead of the globe
   panel's draggable 3-snap BottomSheet — same snap-tool exists and is used by the globe page;
   the Explorer's is a static sheet (recorded as a known simplification).
3. **Fixture ids fixed to the verified layout**: `tests/common_fixtures.py::gunw_id` previously
   emitted ids WITHOUT the secondary-cycle token (the old pre-CMR reading). The decoder rightly
   rejected them, so the fixtures now emit the CMR-verified layout (test-side only; P01 parsing
   unchanged and re-verified green). The deliberately-unresolved fixture id keeps its old shape —
   that is exactly what that test pins.
4. **Boot auto-search on /granules**: a pasted link with a complete box runs the search ONCE
   (otherwise a restored URL would show an empty state — the opposite of "a pasted link restores
   the same view"). The Search button remains the only other trigger.
5. **Rate limiting** applies to the bbox mode only (sliding window, 30/min) — the legacy per-tile
   mode is pinned by existing tests and left untouched.
6. The sandbox's output transport intermittently ate 2-char sequences (`[h`, `[m`, `/$`) in tool
   RESULTS; one written template literal lost a `$`. Detected in the §8.3 screenshot critique,
   byte-verified fixed, and the final files are byte-checked. No other occurrence found (swept).

## 6. Owner actions

1. Apply the Supabase migrations (0001, then 0002) in the SQL editor — idempotent.
2. Set engine env: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`; `NISAR_ENABLE_SCANS=1` to enable scans;
   Earthdata credentials (`EARTHDATA_TOKEN` or `EARTHDATA_USERNAME` + `EARTHDATA_PASSWORD`).
   Never expose secret values; the UI/health report presence only.
3. `app-ui/.env.local`: `NEXT_PUBLIC_SUPABASE_URL` + `NEXT_PUBLIC_SUPABASE_ANON_KEY`
   (see app-ui/.env.example); optionally `NEXT_PUBLIC_ENGINE_API`.
4. Backfill: `python tools/backfill_supabase.py --dry-run` then without; `--fill-locations` for
   pre-0002 rows; `--retier` for pre-D15 rows.
5. Curate featured areas: `python tools/feature_area.py add --slug … --title … --lon … --lat …`
   (tiles are engine-resolved; the list starts EMPTY on purpose).
6. **Put authentication in front of `POST /api/scans` (and `POST /api/runs`) before public
   exposure** — an unauthenticated scan makes the engine fetch hundreds of MB from ASF.
7. Granule search on ASF: `asf_search`'s `dataset="NISAR"` currently lists only clock/cal products;
   when the L2 collections are searchable there the bbox Explorer works against live data as-is
   (the route and enrichment are done and tested). Consider adding the CMR collection short name
   as the search key if ASF keeps the dataset index behind.
8. Investigate the 10 recorded EQ-path failures BEFORE trusting any earthquake verdict: the metamorphic
   LOS-sign gate (test_g0c) gets correlation −0.36 where it expects > +0.25. No scipy version is pinned
   anywhere (only `scipy>=1.11`), so "environment" is a hypothesis, not a finding. Bisect on a machine
   with pyproj/h5py installed: run `tests/test_g0_los_sign.py` and print the screen correlation.

## 7. Known limitations and follow-ups (non-goals confirmed)

- Filename tokens decoded ONLY where verified: cycle/track/direction/frame/secondary-cycle,
  bandwidth (MHz×100, single-frequency), SH/SHNA→HH (main band), the 4 pair timestamps, CRID,
  version. LEFT RAW ("undecoded" flag): the orbit letter, multi-frequency bandwidth tokens
  (e.g. 4005 = 40+5), status/center letters (returned raw as fields; only the orbit letter carries the `undecoded` flag), the PR pipeline token, any id matching no verified
  layout (shown raw end-to-end).
- Overlays draw on the engine's lon/lat bounding rectangle (documented approximation); seam-crossing
  rectangles are split at ±180 (S1-verified fix) — tiles wider than the split logic assumes
  (>360°) are refused by validateLayerPayload.
- /granules is a viewer + bridge only: no clipping/prefetch (DEC14), no cancel, no time-slider
  animation, no place-name search, no multi-tile scans, no parameter overrides — all non-goals,
  listed as follow-ups.
- Phase 5 (SAR base rasters from bundles) was NOT reached — it is a proposal-first item and is
  left as a follow-up, not stubbed.
- The engine-offline screenshot shows map chrome before Esri imagery paints (transient, honest);
  the Next dev "1 Issue" badge in some shots is the live env's dead Supabase port
  (ERR_CONNECTION_REFUSED) and is dev-only.

---

# POST-AUDIT FIXES (independent review of this pack)

Static review only (no pytest/npm/Playwright in the review sandbox); every fix below has a regression
test and was exercised in isolation (python regressions run directly, TS logic run under node).

- `engine/common/granule_id.py::_polarization` — only a 2nd character of `H` decodes (→ `HH`, the CMR-verified
  case). `HV` used to decode to `VV` (wrong) and `SV` to `VV` (unverified); both now stay raw + flagged.
- `engine/api/granules_bbox.py` — `truncated` is now per search (a level that hit the budget), not
  `len(union) >= budget`, which falsely flagged multi-level searches. Docstrings now say the 503 comes from
  `JobRunner.granule_search_bbox`, where it is.
- `app-ui/src/lib/raster.ts::seamSplits` — a zero-width box (`W === E`) is refused (`[]`) instead of becoming a
  world-wide overlay.
- `app-ui/src/lib/urlstate.ts` — `from`/`to` written as `YYYY-MM-DD HH:MM` were accepted by the pattern then
  silently dropped; they are now normalised to `T`.
- New tests: `tests/test_granules_bbox.py` (decoder polarization x7, per-level truncation, proven to fail on the
  old rule) and `tests-e2e/unit.spec.ts` "post-audit regressions" (seamSplits had no unit test before).
- Known, NOT fixed (design, not bugs): a running scan blocks the bbox search (one worker pool, 600 s wait); the
  bbox rate-limit window counts a 503 (no credentials) call; the 10 EQ-path failures (see owner action 8).
