# UI BUILD LOG 2 — Landing · Map · Background (retrieval / preprocessing / pipelines)

Ledger for the UI-build-2 task (§0 operating rules override everything). One checkbox per
deliverable; a box is ticked only after its gate passed. Honest-raw and no-science rules carry
over from UI_BUILD_LOG.md unchanged; the post-audit fixes are NOT weakened.

## Baseline (recorded before any change)

- [x] `python -m pytest -q` baseline recorded: **10 failed, 504 passed, 5 skipped**.
  The 10 failures are NOT all earthquake science (corrected post-audit): **7 are earthquake-related**
  — test_api::test_run_equake_then_tile_verdicts (1), test_debug_hub (2),
  test_g0_los_sign::test_g0c… (1; correlation −0.36 where it expects > +0.25), the
  earthquake_coseismic pipeline suite (3) — and **3 are a missing module**: tests/test_sar_debug.py
  runs `python -m sar_debug` with cwd=tools/, but **tools/sar_debug/ is not in the pack** (only
  tools/sar_debug_report.html is), so those three fail with "No module named sar_debug". They say
  nothing about earthquake science. (The fourth test in that file, test_package_never_imports_the_engine,
  passes vacuously because it globs an absent directory.) Pass count differs from the previous
  sandbox's 451 because dependency versions differ here; the failure SET matches the earlier log.
  The pack was therefore NOT fully green or fully verified at baseline.
- [x] `cd app-ui && npm ci && npm run ci` baseline recorded — **green** (route sizes: / 25.5 kB,
  /granules 13.8 kB, /workbench 7.96 kB First Load)
- [x] `cd app-ui && npm run test:e2e` baseline recorded — **46 passed, 1 skipped** (after
  `npx playwright install chromium` — environment, not code)
- [x] Pack integrity: MANIFEST.json not yet regenerated; sha256 spot-check of the two files the
  previous log declared generated (tools/report.html, tools/sar_debug_report.html) — unchanged
  status expected until the final regeneration. (Post-audit: the manifest never hashed
  config/catalog/*.json — see "Post-audit fixes", Problem 6.)

## Phase 1 — engine: acquisition groups + gated /api/background/* endpoints

- [x] §4.2 grouping added to engine/api/granules_bbox.py (pair key = stack+ref+sec; single key =
      stack+start; epoch_merge_minutes clustering; variants[] with differing facts; ungroupable
      granules surfaced ungrouped; multi-frequency bandwidth stays raw) + tests
- [x] §4.5 engine/api/background.py + background_img.py (imaging ideas from the debug hub,
      hub server not imported) + routes_background.py:
      granule / preview / probe / stack-pair — gated, rate-limited, envelope-conformant
- [x] Gating: NISAR_BACKGROUND_TOKEN bearer check (unset → 503 naming the var; wrong → 401) + tests
- [x] Preview: click-only, clip-to-box, max_px cap, all-NaN → clear answer, cache under
      NISAR_DATA_DIR, single concurrent preview, PNG + metadata (or JSON sibling) + tests
- [x] §5 GET /api/background/runs/{id}/preprocessing (+ bundle variant + trace-array/probe
      sub-resources) fed by a recorder run on the chain's observer hook + honest empty state
- [x] §6 GET /api/background/runs/{id}/pipelines/{name} (input/process/output; 404/422 carry the
      engine's text)
- [x] Gate: python -m pytest -q → no new failures vs baseline (tests/test_background_api.py: 27 passed)

## Phase 2 — UI: information architecture

- [x] §1 three top-level tabs in AppNav (Landing · Map · Background); Background sub-nav
- [x] /map = the existing globe app, behaviour unchanged
- [x] /background redirects to /background/retrieval (gated)
- [x] /background/retrieval = the Granule Explorer moved, behaviour unchanged
- [x] /background/preprocessing, /background/pipelines[/name]
- [x] Compatibility: legacy /?lat=&lon=&z=&tile=&bundle=&pipeline=&layer=&scan= → /map same query;
      /granules?… → /background/retrieval same query; /workbench → /background/pipelines; every
      bridge updated (e2e-tested)
- [x] Token gate UI: asks once, sessionStorage (try/catch), never in URL, never logged;
      documented as a stopgap
- [x] Gate: npm run ci green (typecheck + no-science guard + build; route sizes: / 6.4 kB, /map 25.7 kB, /background/retrieval 20.3 kB)

## Phase 3 — Landing (/)

- [x] §2 sections 1–7 (promise + CTA, what this is, four steps, how to read a result reusing the
      globe results panel wording, mini glossary, live status strip from /api/health + verdict
      cache with honest down-state, footer with "not for emergency decisions")
- [x] Mobile-first, AA contrast, no maplibre chunk on / (build output: shared chunk identical, / First Load 113 kB vs /map 137 kB)
- [x] Screenshot gate: 1440×900 + 390×844 captured and critiqued (landing)

## Phase 4 — Background › Data retrieval (the main feature)

- [x] §4.1 GUNW = pair (bar with end dots, true time order, flipped says so); GCOV = single dot;
      GOFF treated as a pair; product glyph+colour+text label; GUNW-not-two-images text +
      before/after backscatter offer via stack-pair
- [x] §4.2 groups rendered (N variants badge, variant picker, never merge/hide, ungrouped fallback)
- [x] §4.3 detail panel tabs: Summary (decoded vs raw table, flags, footprint mini-map) / Layers
      (engine inventory) / Timeline (stack epochs + gaps, selection highlighted) / Data parts
- [x] §4.4 Compare view: two panes, preview images per end, shared zoom/pan (toggle), pixel probe,
      cmap + min/max, acquisition time, swap, layer selector, variant comparison; zoom = new
      sub-box request (never client upscale); all four pane states
- [x] §4.6 states: offline, 503, 429 + retry, truncated/degraded groups, no footprint, one-epoch
      stack, same-epoch variants, multi-frequency raw, flipped pair, two CRIDs/versions, unknown
      id, antimeridian, failing/all-NaN preview, large granule clip-only, missing GCOV end, UTC,
      keyboard + aria-live
- [x] Gate: npm run test:e2e green incl. new specs (86 passed/1 skipped, the final-gate figure — an earlier 85 was a mid-build snapshot); live specs pass with the new base path (7 passed)

## Phase 5 — Background › Preprocessing and › Pipelines

- [x] §5 step-by-step view (name/read/wrote/flags/assumptions/timing/arrays as images with hover
      probe + enlarge), SceneBundle summary, hand-off table; run selector (runs + cached bundles);
      honest empty state
- [x] §6 pipeline list (implemented flag; unimplemented listed as not modelled yet, never faked) +
      detail (Input / Process / Output) reusing VerdictCard / LayerMap pieces
- [x] Screenshots 1440×900 + 390×844 captured and critiqued (21 shots; fixes: VerdictCard contrast, mobile nav wrap)

## Phase 6 — tests, gates, manifest, final report

- [x] Python offline tests for all new endpoints (FakeRetriever seams): gate 401/503, rate limit,
      preview all-NaN/cap/cache, flipped pair, multi-frequency token, grouping cases
- [x] Playwright fixtures regenerated through the real engine code; new specs for every §4.6 state
- [x] Unit tests for new pure logic (grouping display helpers, URL state for new routes)
- [x] Gates with counts: python -m pytest -q = **10 failed / 531 passed / 5 skipped** (the same
      10 as the baseline: 7 earthquake-related + 3 missing tools/sar_debug/; 504 -> 531 passed = +27 new
      tests, no new failures); npm ci && npm run ci **green**; npm run test:e2e **86 passed / 1 skipped**;
      npm run test:e2e:live **7 passed**
- [x] Screenshots at 1440×900 and 390×844 for all ten required states — 21 shots opened and
      critiqued (app-ui/test-results/screens2/); fixes applied: VerdictCard text contrast on the
      dark page, landing mobile nav wrap; remaining known flaws are dev-only and listed in the report
- [x] MANIFEST.json regenerated (tools/make_manifest.py) and verified: 504/504 sha256 match,
      0 missing, 0 extra
- [x] Final report in the §9 exact format (below)

## Commits

(The pack ships WITHOUT a `.git` directory: `zip_repo.py` zips the output of
`git ls-files --cached --others --exclude-standard`, which never lists `.git`, so the zip carries no
history and does not "start" one — run `git init` and commit to begin. During the working session
git commands were landing in the workspace's parent repo, so any phase hashes cited earlier in the
session were not in-pack. The phases below are the session's order only, each gate reported above.)

- baseline | pytest 10F/504P/5S recorded (7 earthquake-related + 3 missing tools/sar_debug/), npm ci + npm run ci green, e2e 46P/1S after chromium install
- phase 1 | engine: §4.2 groups in granules_bbox (pair/single keys, id-decoded times only, truncated flag, ungrouped raw) + engine/api/background{,_img}.py + routes_background.py (gate 401/503, rate limits, preview cache + single-flight, stack-pair, preprocessing traces via the Tracer observer, pipeline detail) + 27 offline tests
- phase 2+3 | UI IA: Landing(/) Map(/map) Background(/background/*), legacy redirects, token gate + bgApi, AppNav Landing·Map·Background, Explorer moved + groups/tabs/Compare, Preprocess + Pipelines views
- phase 4+5 | e2e specs for all of the above; fixture regenerated through the real enrichment with groups/variants/flipped/unknown; live specs moved to the new base paths; 21 screenshots captured and critiqued
- phase 6 | final gates re-run with counts; MANIFEST regenerated (504/504, see Problem 6 for what it skipped); final report (this section); shipped zip

---

# FINAL REPORT (§9 exact format)

## 1. Delivered per phase (with the ledger ticked)

| Phase | Commit | Delivered |
|---|---|---|
| baseline | session start | baseline recorded before any change: pytest **10 failed / 504 passed / 5 skipped** (7 earthquake-related + 3 from the missing tools/sar_debug/ — the pack was not fully green), `npm ci && npm run ci` green, e2e 46P/1S (after the matching Playwright chromium install), baselines recorded in the ledger above |
| 1 — engine (§4.2 + §4.5 + §5 + §6) | phase 1 | §4.2 acquisition groups computed by the engine (`group_acquisitions` in `engine/api/granules_bbox.py`; pair key = stack+ref+sec, single key = stack+start, id-decoded times ONLY, `epoch_merge_minutes` clustering, `variant_facts`, truncated groups flagged "may be incomplete", ungroupable ids ungrouped+raw, response key `groups`); §4.5 gated endpoints (`engine/api/routes_background.py` + `background.py` + `background_img.py` — hub imaging ideas re-implemented, hub server NOT imported): granule (decoded+inventory+group), preview (click-only, clip-to-box, max_px≤512 cap, all-NaN → 422 clear text, PNG + X-Img-Meta + `format=json` sibling, sha-keyed cache under NISAR_DATA_DIR, single-concurrent 409, 429 windows), probe, stack-pair (±merge window, both variants listed, honest empty end); §1 gate (unset NISAR_BACKGROUND_TOKEN → 503 naming the var; wrong → 401); §5 `GET /api/background/runs/{id}/preprocessing` (+`?bundle_id=`, +array PNG/probe sub-resources) fed by the chain Tracer run on the scan's own observer hook (bounded in memory, evicted with the run, honest empty state); §6 `GET /api/background/runs/{id}/pipelines/{name}` (input/process/output; 404s carry the engine's text). +27 offline tests (`tests/test_background_api.py`), all green at the time |
| 2+3 — IA + Landing | phases 2+3 | §1 shared AppNav (Landing · Map · Background + Background sub-nav); /map = the globe app moved unchanged; /background → /background/retrieval; /background/{retrieval,preprocessing,pipelines[/name]}; legacy redirects with query preserved (/?lat|lon|z|tile|bundle|pipeline|layer|scan|panel → /map; /granules → /background/retrieval; /workbench → /background/pipelines); every bridge updated (Explorer→/map, ScanPanel→/background/retrieval, runs/verdicts/cached links→/map); token gate UI (asks once, sessionStorage try/catch, header-only, retry nonce re-triggers gated effects, documented as a stopgap); §2 Landing (promise+CTA, what-this-is, 4 steps, how-to-read reusing the globe wording incl. the not-a-probability note and SYNTHETIC labelling, 5-term glossary, live status strip, footer with data sources + "not for emergency decisions"); no maplibre chunk on / (build: / First Load 113 kB vs /map 137 kB). **What the status strip really shows** (`app/(site)/page.tsx`): while loading, "checking the engine…"; if `/api/health` fails, one red line with the error and **no figures at all**; if the engine is up, three tiles — engine "up", scans enabled yes/no (from `/api/health`), and **cached areas = one number**, the verdict cache's own `pins.total`, or "—" with "(verdict cache not connected)" when the cache is not configured or unreachable. So it carries one count when the engine is up, and no numbers only in the down state |
| 4 — Data retrieval | phase 4 | §4.1 GUNW pair vs GCOV single-date rendering (bar+end dots vs dot, glyph+colour+TEXT label, GOFF as pair, flipped ends labelled by true time order, "a GUNW is not two images" + before/after GCOV offer via stack-pair with the engine's per-end answer); §4.2 one bar per group with ×N badge + variant picker (never merges, never hides, ungrouped fallback); §4.3 tabbed detail (Summary: decoded-vs-raw table + flags + footprint mini-map; Layers: engine layout-index inventory incl. not-readable reasons; Timeline: stack epochs+gaps with the selection highlighted; Data parts: the chain's scan request regex vs what exists, per-layer `requested_by_scan` from the engine); §4.4 Compare view (two panes from the engine's stack-pair, shared zoom/pan + unsync toggle, pixel probe with value+lon/lat, cmap+min/max+NaN fraction+wire bytes, acquisition times, swap, layer selector for BOTH panes, resolution comparison via per-end variant pickers, zoom = new sub-box request); §4.6 states incl. offline/503/429/truncated-degraded/no-footprint/one-epoch/variants/multi-freq-raw/flipped/unknown-id/antimeridian/all-NaN/failing-preview/missing-end/UTC/aria-live; mobile bottom sheet |
| 5 — Preprocessing + Pipelines | phase 5 | §5 step-by-step chain view (name/read/wrote/flags/assumptions/timing/arrays as engine-rendered images with hover probe + enlarge modal), SceneBundle summary, hand-off table (Registry.plan + present/missing chips per declared input), run+bundle selector, honest empty state; §6 list (implemented flag; unimplemented listed as "not modelled yet", never faked) + detail (Input: manifest identity, routing decision+reasons, uses/own_fetch chips; Process: method/own_prep/assumptions/timing/engine numbers/decision rule; Output: reused VerdictCard with tier+tier label and the not-a-probability note); 404/422 carry the engine's text |
| 6 — tests/visual/manifest | phase 6 | fixture regenerated through the REAL enrichment (groups, 2-bandwidth variant epoch, flipped pair, unknown-layout id); new specs: redirects (6), landing (6), background-retrieval (13), preprocessing+pipelines (9), unit additions (5); live specs moved to the new base paths and the workbench flow rewritten to drive POST /api/runs through the browser; screenshots2 suite (21 shots, all opened and critiqued); MANIFEST regenerated + verified |

## 2. Verified by running (commands and counts)

- `python -m pytest -q` (full suite, final, as recorded by the build session): **10 failed / 531
  passed / 5 skipped** — the same 10 failures as the baseline (7 earthquake-related + 3
  missing-module sar_debug failures; see Baseline); 504 → 531 passed (+27 new background tests, 0 new
  failures).
- `cd app-ui && npm ci && npm run ci`: **green** (tsc --noEmit + no-science guard OK + next build;
  route sizes: / 6.4 kB, /map 25.7 kB, /background/retrieval 20.3 kB, /background/preprocessing
  5.3 kB, /background/pipelines 0.2 kB First Load deltas small; maplibre-gl stays in ONE async
  client chunk loaded only by /map and the retrieval footprint map, with ZERO references from /).
- `npm run test:e2e`: **86 passed / 1 skipped** (unit 32 incl. the new grouping/box-math specs,
  smoke incl. the /workbench redirect, globe-cache, explorer 7, redirects 6, landing 6,
  background-retrieval 13, preprocessing+pipelines 9, screenshots2 1, offline screenshots, webgl-off).
- `npm run test:e2e:live` (seeded uvicorn engine + dev server, SYNTHETIC-labelled): **7 passed**
  (health strip, tile-at flow, Data-tab timeline, scan flow, POST /api/runs through the browser,
  2 screenshot suites on the new base paths).
- Pack integrity: MANIFEST.json regenerated → 504/504 sha256 match, 0 missing, 0 extra — **true only
  by construction**: the generator skipped any path part named `catalog`, so the shipped
  config/catalog/GCOV.json and GUNW.json were never hashed (fixed post-audit, Problem 6).
- Token handling: a spec asserts the bearer header rides `Authorization` and the token NEVER
  appears in any URL (granule, preview URLs checked).
- 21 screenshots at 1440×900 and 390×844 captured and each opened and critiqued (§7).

## 3. Not verified, and why

- **Live ASF/CMR behaviour of the new gated endpoints against real data**: the sandbox has Earthdata
  credentials absent (by design), so granule/preview/probe/stack-pair were verified through the
  engine's own retriever seam (FakeSource over the real `search_bbox_sync`/`build_clip` code paths)
  exactly like the pre-existing granule-search tests. The production `NisarSource` path is the same
  code the scanner uses, but no real HDF5 byte was range-read here.
- **The 7 earthquake-related failures are recorded, not investigated** (rule 7: report unchanged).
  They are the same as the baseline set; `test_g0_los_sign.py::test_g0c…` still gets correlation
  −0.36 where it expects > +0.25 (possible sign leak, unproven environmental). The other 3 of the
  10 are the missing `tools/sar_debug/` package (not science).
- **A running scan still blocks the bbox search** (single worker pool) — known, NOT fixed (design); the four local-data background routes were moved off that pool post-audit.
- **Supabase**: not configured in this sandbox; the landing strip shows the honest "—". The cache
  read path is the pre-existing one, unchanged.
- **Real authentication**: the token gate is the stopgap the task specified; a real auth system is
  an owner action (non-goal here).
- The debug-hub UI (tools/debug_hub) was a design reference only; its own suite is untouched and
  passes at baseline (the two earthquake-related failures in it are the recorded ones).

## 4. Deviations with reasons

1. **Sub-resources beyond the §4.5 endpoint list**: trace arrays needed two read-only gated
   sub-routes (`/runs/{id}/arrays/{aid}` PNG + `/probe`) because the preprocessing trace must
   render engine thumbnails without shipping megabytes inline. They follow the same gate/rate/
   envelope rules; the task's endpoint list says "model them on the debug hub", which is exactly
   the /api/img + /api/px split.
2. **Bundle preprocessing route shape**: the §5 selector also lists cached bundles; their records
   are served by the SAME endpoint with `?bundle_id=` (the path keeps its `{id}` for runs). A
   separate bundles route would have duplicated the envelope for no gain.
3. **Trace persistence is in-memory only** (evicted with the run, never written to sqlite): traces
   are debugging observations, the runs store is a bookkeeping contract, and the endpoint says so
   honestly ("runs started before tracing … carry none"). Bundles carry their own step records on
   disk, so cached bundles still show the chain.
4. **Group bars draw the first variant's id**: the timeline draws ONE bar per engine group; the
   bar's selection id is the first variant (deterministic). The picker in the detail panel is the
   variant switcher — no variant is hidden (each is listed with its differing facts).
5. **`id_start` added to the enriched granule record** (additive): grouping keys must come from
   FILENAME-decoded times only, so an unknown-layout id stays ungrouped even when the provider
   states a start time. The provider's own start remains on the granule record untouched.
6. **Live workbench e2e rewritten**: the old page is gone by design (§1: /workbench →
   /background/pipelines), so the "POST /api/runs end to end" live flow now drives the POST from
   the browser and lands on /runs/{id} — same coverage, new home.
7. **The Compare view's before/after GCOV come from `/api/background/stack-pair`** (engine-computed
   per §4.1), not from the pair's own variants; the screenshot fixture labels its ±-merge-window
   answer as a fixture.

## 5. Owner actions

1. Set `NISAR_BACKGROUND_TOKEN` on the engine (any long random string) and share it with Background
   users. **This is a stopgap** — put real authentication in front of the engine before any
   untrusted exposure (the gate message says so on screen too).
2. Keep the existing env set: Earthdata credentials (`EARTHDATA_TOKEN` or
   `EARTHDATA_USERNAME`+`EARTHDATA_PASSWORD`) — the four provider-backed /api/background/*
   routes (granule, preview, probe, stack-pair) answer 503 without them (the four local-data routes no
   longer need them, post-audit); `NISAR_ENABLE_SCANS=1` for scans; `SUPABASE_URL`/`SUPABASE_SERVICE_KEY` for the
   cache; `NEXT_PUBLIC_SUPABASE_URL`/`NEXT_PUBLIC_SUPABASE_ANON_KEY` in app-ui.
3. The preview cache lives under `$NISAR_DATA_DIR/background/previews/` — safe to delete anytime
   (entries are content-addressed and rebuilt on demand); consider a periodic clean or a quota.
4. Preprocessing traces live in memory per engine process and die with the run (by design). If the
   owner wants post-restart traces, that is a persistence follow-up (an owner decision, not done
   here to keep the runs store contract unchanged).
5. CORS: the Background tab calls the engine from the UI origin — the existing `NISAR_UI_ORIGINS`
   list already covers it; no wildcard was added.
6. Owner action 8 from the previous build stands: investigate the 7 earthquake-related failures (the
   −0.36 correlation in test_g0c) BEFORE trusting earthquake verdicts. Also: **restore
   `tools/sar_debug/`** (the 3 test_sar_debug tests depend on it; it is absent from the pack and no
   packaging rule excludes it).
7. asf_search's `dataset="NISAR"` limitation (previous build, owner action 7) is unchanged.

## 6. Known limitations (incl. the rule-7 open issues, unchanged)

- **Rule-7 open issues, reported again unchanged**: (a) 7 earthquake-related pytest failures exist at
  baseline (list in Baseline; correlation −0.36 vs expected > +0.25 in test_g0c — possible sign
  leak, unproven environmental), plus 3 missing-module failures (tools/sar_debug/ absent);
  (b) a running scan blocks the bbox search (single worker pool, 600 s wait) — and by extension the
  four PROVIDER-backed background routes (granule, preview, probe, stack-pair) queue behind it on
  that pool. The four local-data routes (preprocessing, trace arrays, array probe, pipeline detail)
  no longer do (post-audit, Problem 1).
- The token gate protects the routes but not against a token holder's abuse (rate limits are the
  only throttle: 60/min metadata, 12/min previews/probes, 1 concurrent preview).
- Previews render on the layer's own grid (nearest decimation to max_px; no reprojection beyond the
  engine's lon/lat box answer); the granule layer inventory needs one metadata pass per granule
  (cached by the client's layout-index machinery).
- Trace array thumbnails are ≤400 px engine-side decimations (never resampled values); probes read
  the stored thumb, so a probe lands on a thumb pixel, not the native grid.
- Dev-only artifacts visible in some screenshots: the Next.js "N" dev-tools badge (and the dead
  Supabase port's fetch error inside it) — they do not exist in `next start`.
- The compare fixture in screenshots labels a GCOV whose time differs from the pair end (test
  fixture; the live engine only returns granules within ±epoch_merge_minutes).
- Non-goals respected: no new science, no new pipelines, no multi-tile scans, no parameter
  overrides, no automatic scans or prefetching, no place-name search, no changes to
  no-science-guard.mjs (still unmodified, still green), no weakening of existing tests, no real
  authentication system.

## Delivery

The pack's own `tools/final_check.py` was run once more at delivery. Install integrity: PASS.
Environment: PASS. **Credentials: FAILED** — no Earthdata credentials were available in the delivery
sandbox (the recorded offline scope; see §3 of the final report). The real-granule acceptance steps
need those credentials, so they were **not verified**.

## Post-audit fixes

A second pass fixed the seven findings of an audit. Test status: the new tests below were written in
a sandbox with **no network and no fastapi / pytest / node_modules installed**, so
`python -m pytest -q`, `npm ci && npm run ci` and `npm run test:e2e` were **NOT RUN there — NOT
VERIFIED**. What did run: py_compile of every touched Python file; the gate parser, the rate limiter
(40 threads, limit 12 → exactly 12 allowed) and `safe_bundle_dir` against a stubbed `fastapi`;
`tsc` over `bgApi.ts`; the manifest generation and verification script (Problem 6). Run the three
gates before relying on any of this.

1. **Local-data routes no longer gated on Earthdata or queued behind scans.**
   Files: `engine/api/routes_background.py` (new `_run_local`; docstring now lists which routes use the
   shared pool), `engine/api/background.py` (`register_trace` assigns `trace_arrays` under the runner
   lock). Finding: the four routes are light (bounded in-memory thumbnails, a one-pixel probe, JSON +
   read-only mmap + manifest scan), so they run inline in the request's own threadpool thread; no new
   executor. `get_state`/`get_trace`/`get_trace_array` already take `JobRunner._lock`; the one unlocked
   writer was `register_trace`'s `trace_arrays` assignment, now locked. Tests (`tests/test_background_api.py`):
   `test_local_routes_work_without_earthdata_credentials`, `test_provider_routes_still_503_without_earthdata`,
   `test_local_route_not_queued_behind_a_busy_scan_pool`, `test_local_routes_keep_the_gate`,
   `test_local_routes_share_the_background_rate_limit`.
2. **Non-token 503 no longer shows the token prompt.** Files: `app-ui/src/lib/bgApi.ts` (401 → GateError
   "unauthorized"; 503 naming NISAR_BACKGROUND_TOKEN → GateError "unconfigured"; any other 503 →
   `EngineUnavailableError` with the engine's verbatim text; stale comment fixed). Every view already
   rendered non-gate errors in its existing error state, so no view changed. Spec:
   `app-ui/tests-e2e/background-gate-503.spec.ts` (401 / gate-503 / Earthdata-503 on a retrieval,
   a preprocessing and a pipelines route — 9 tests).
3. **Missing `tools/sar_debug/`.** No packaging rule excludes it (`.gitignore`, `zip_repo.py`,
   `dev_scripts/zip_up.py` checked; `git check-ignore` matches nothing under tools/sar_debug/), and the
   pack has no git history, so the source is simply not in the repo. Nothing was written to replace it
   and no test was touched. This log's baseline text was corrected (7 earthquake-related + 3
   missing-module). Owner action: restore `tools/sar_debug/` (3 tests depend on it). The test_g0c
   correlation failure is untouched.
4. **`bundle_id` path validation.** File: `engine/api/routes_background.py` (`safe_bundle_dir`,
   `_preprocessing_worker`), `engine/api/background.py` (`scrub_paths`, also applied to
   `bundle_error` in the pipeline detail). Regex `^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`, no "..",
   resolved parent must equal the resolved bundles dir; 400 otherwise; 404 names the id and exception
   class, never an absolute path. `run_id`/`aid` only index dicts or a SQL parameter, so they are
   unchanged. Tests: `test_bundle_id_unsafe_values_are_400` (10 values incl. `../x`, `..%2Fx`, `/etc`,
   `a/b`, `a\b`, over-long), `test_bundle_id_symlink_escaping_the_store_is_400`,
   `test_bundle_id_valid_cached_bundle_still_loads`,
   `test_bundle_id_missing_bundle_404_names_id_and_class_but_no_paths`,
   `test_pipeline_bundle_error_scrubs_the_bundles_prefix`. Not changed (outside the Background routes):
   `POST /api/runs` → `JobRunner.start_run` and `GET /api/bundles/{bundle_id}` also join a request
   value onto the bundles dir.
5. **Constant-time token compare + rate-limit lock.** File: `engine/api/routes_background.py`
   (`_token_matches` with `hmac.compare_digest` on UTF-8, `Bearer <token>` case-sensitive, one space;
   `_RATE_LOCK` around check-and-append). Tests: `test_gate_malformed_authorization_headers_are_401`
   (9 headers), `test_gate_correct_header_passes_and_uses_compare_digest`,
   `test_rate_limiter_is_atomic_under_concurrency`.
6. **MANIFEST.json skipped shipped config.** Files: `tools/make_manifest.py` (bare `"catalog"` removed;
   `SKIP_PATHS = {("data","catalog")}` + one `is_skipped()`), `tools/final_check.py` (imports
   `files` from `make_manifest`; now also warns about shipped files the manifest does not list),
   `MANIFEST.json` regenerated. Verified by a script (counts in the delivery report).
