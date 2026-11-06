# Merge notes (2026-11-01)

Sources, oldest to newest by file time:
1. `nisar-hazard-engine_14.zip` (Nov 1 03:26) - Colab hub as `make_colab_module.py`
2. `nisar-hazard-engine.zip` (Nov 1 10:43) - **base of this merge.** Corner/stride fix, `lo`/`hi` range, thumbnail queue, map SAR overlay,
   `colab/build_hub.py` (replaces `make_colab_module.py`), `limit` on retrieval list
3. `speckle-fixes_1.zip` (Nov 1 18:45) - earthquake scan-mode, NO_SIGNAL tier, public defaults, cache of partial ASF pages,
   "Speckle" rename, SUPPORTED wording. Its HANDOFF items A-D were already in (2).

Rules used: newest wins per file. `speckle-fixes` files were applied on the base unless the base was newer.
- Kept the base over speckle-fixes: `engine/api/retrievals.py`, `engine/api/routes_retrievals.py` (speckle-fixes carried the older
  `list()` without `limit`; the base's `test_retrieval_list.py` needs `limit`).
- Dropped as superseded: `colab/make_colab_module.py`, `tests/test_colab_module.py`, `tests/test_colab_hub.py` (replaced by build_hub.py and tests/test_hub*.py, test_build_hub.py).
- Ported from the older zip: the two "Retrieval hub" cells at the end of the Colab notebook (now call `colab/build_hub.py`).
- The older zip's `*.pyz` line in `.gitignore` is not needed: `dist/` is already ignored.
- `MANIFEST.json` regenerated from this tree. Not packed: HANDOFF.md from speckle-fixes (its open items are listed below).

## Not verified (sandbox had no fastapi / aiohttp / h5py / pyproj / node_modules / network)
Run: `pytest -q tests/`, then `cd app-ui && npm ci && npm run typecheck && npm run lint && npm run guard:no-science`.
Ran here (--noconftest, stubbed aiohttp): 10 test files incl. test_select_no_signal, test_eq_scan_selection, test_public_defaults,
test_corners_stride, test_preview_scale, test_retrieval_list, test_hub; the failures that remain are identical on the unmerged base
(missing fastapi/h5py). TypeScript was not compiled.
Open items from speckle-fixes HANDOFF: no browser check of the corner-placed globe quad, Before/After and thumbnails; no HTTP test for lo/hi 400 cases;
thumbqueue Playwright spec never run.

# Second merge (2026-11-02): speckle-audit changes (diff + zip, made on the same base as step 2)
The audit agent built its own versions of several things speckle-fixes also built. Overlapping features were NOT blended line by line;
one coherent implementation was kept for each:
- **Kept from audit (replaces speckle-fixes):** NO_SIGNAL ranking + quiet handling (`engine/verdict/select.py`), stack ranking (`scene_bundle.py`: `stack_keys_by_quality`),
  the earthquake config block / `EqParams.defaults()` (`scan_pairs_per_stack`, `analyse_all_stacks`, `edge_touch_max_fraction`), `earthquake_coseismic/{prep,verdict,types,inputs}.py`,
  `config/common.yaml`, the engine-defaults route `GET /api/settings/defaults` + `app-ui/src/lib/defaults.ts` + `RetrievalPanel.tsx`, the search-incomplete flag text in P01,
  the partial-listing cache in `nisar_client.py`/`cache.py`, `main.py`.
- **Removed (speckle-fixes versions of the same features):** `engine/common/defaults.py` and the `defaults` field on `/api/health` and in `types.ts`,
  `tests/test_public_defaults.py`, `tests/test_eq_scan_selection.py`, `tests/test_select_no_signal.py` (their targets no longer exist; audit ships
  `tests/test_verdict_select_quiet.py`, `tests/test_settings_defaults.py`, `earthquake_coseismic/tests/test_scan_inputs.py` for the same ground).
  Not carried over from speckle-fixes: `analysed_window` / `signal_edge_contact` helpers and the "decision.meaning" evidence line.
- **Kept from speckle-fixes (no audit equivalent):** the "Speckle" rename, SUPPORTED wording in the UI, NOT_MODELLED note in VerdictCard, baseline "context only" tag,
  `search_incomplete_levels` + `incomplete_detail` on the granule-listing endpoint and its Explorer banner.
- **Applied from audit as is:** `routes_settings.py`, `VerdictBasis.tsx`, Legend/TimelineCanvas/GlobeApp UI changes, `registry.py` / `chain.py` / `context.py`, `granule_poller.py`, `retrieval/settings.py`,
  `config/retrieval.yaml`, `next.config.ts`, and its four new tests.
Verified here: all Python compiles; the audit's new tests and every test file that can run without fastapi/h5py/aiohttp/pyproj pass (7 passed, 4 skipped).
Not run: full pytest, TypeScript typecheck/lint, any browser.

# UI fixes (2026-11-03)
- Data retrieval page lost the Background section tabs: `ExplorerApp` was `fixed inset-0` with its own AppNav, painted over the Background layout header
  (Overview / Data retrieval / Preprocessing / Pipelines / Health). It is now an ordinary flex child of the layout (no fixed overlay, no second AppNav).
- Timeline bars kept widening: the timeline's measured width fed back into its own container size (canvases have explicit px widths; the flex chain had no
  `min-w-0` / overflow clip). Added `min-w-0` down the chain, `overflow-hidden` on the timeline wrapper, and the ResizeObserver no longer sets state when the width is unchanged.
  NOT verified in a browser (no node_modules / npm reachable in the sandbox): please open Data retrieval, run a search, confirm the tabs show and the timeline holds still.

# Third audit drop (2026-11-03): speckle-audit v2 (diff + zip, same base)
Identical to the first audit drop (37 files already merged) plus five changes, all applied without conflicts:
`earthquake_coseismic/tests/fixtures_eq.py` (synthetic event is now a REVERSE/thrust 60/40/+90, placed 3 days before the window end),
`earthquake_coseismic/tests/test_sign_convention.py` (new), `tests/test_api.py` and `tests/test_g0_los_sign.py` (nodal plane 60/40/+90), `tools/debug_hub/pipelines.py`.
Test files only plus the debug hub; no engine logic changed. The new sign-convention test is skipped here (needs pyproj/h5py); nothing else changed in pass/fail.

# Explore > Events (2026-11-03)
`disasters_after_nisar_launch.csv` (74 events) now fills the Map's Explore panel as a new, default **Events** tab (before Featured / Cached).
- Data: `python tools/build_disasters_json.py` -> `app-ui/src/lib/disasters.json` (CSV stays the source; `--check` verifies sync; `tests/test_disasters_data.py`).
- Ranking: `app-ui/src/lib/events.ts` (pure). Order = location trust first (A exact +30, B approx +12, regional centroid, no point -60), then how much SAR can say
  (category, "PRIORITY" flag, measurable terms, Mw / depth for quakes, "limited/offshore/deep" penalty), then freshness (computed from today's date at render time).
  Each row has "why #N" listing every point added or removed. Tiers: SAR-ready / maybe / limited / no point.
- Click an event -> fly there (zoom by location quality) and open the Pin panel. The heatwave row has no coordinates and cannot be placed.
- Checked here: Python sync tests; the ranking logic via node (same asserts as the new block in tests-e2e/unit.spec.ts); no-science guard. NOT run: tsc / Playwright / browser.
- Not done: event markers on the globe itself (list + fly-to only).

# Fix: "granule ... not found by the provider search" on the SAR layer (2026-11-03)
Cause: `engine/api/background.py::find_granule` searched the WHOLE WORLD for a +/-2 h window around the id's REFERENCE time, 50 results max,
and required the provider to list a pair granule under that date. A GUNW can be listed under its secondary date, or over its whole ref..sec span
(then every pair spanning the date matches and the target falls past the 50 cap). Fix: box-scoped search first (preview/probe pass their box),
then a window around EACH of ref and sec (cap 250), then asf_search by-name lookup (`NisarSource.find_by_id`, NOT exercised against the real ASF);
a miss now lists what was tried and says when a search hit its cap. `NisarClient._ref_from_result` factored out of `search()`.
tests/test_find_granule.py: 6 tests; 4 fail on the old code (same error text as the screenshot), all pass now. Not verified against live ASF.

# Fourth audit drop (2026-11-03): speckle-audit v3 (diff + zip, same base)
Same as v2 plus 13 files: `DataPanel`, `GlobeApp`, `GranuleTimeline`, `VerdictBasis` (updated), `ExplorerDetail`, `ExplorerList`, `ExplorerSummary`, `FootprintMiniMap`, `TimelineCanvas`, `lib/verdictUse.ts`,
`tests/test_retrieval_cuts.py`, `tests/test_ui_consistency.py`, and a `verdictUseFrom` block in `tests-e2e/unit.spec.ts`. 3-way merged on the old base; kept our earlier edits
(timeline width fix, Explore > Events wiring, baseline "context only" tag). `unit.spec.ts` was rebuilt from the audit file + our Events tests (the automatic merge unbalanced its braces).
Ran: test_ui_consistency + all earlier Python tests (no regressions). `test_retrieval_cuts.py` needs pyproj/h5py (errors here, same as other retrieval tests). Not run: tsc / Playwright / browser.

# Granule lookup: two independent fixes compared and combined (2026-11-03)
The other agent's `granule-lookup-fix.zip` (narrow +/-3 min window -> provider lookup by name with and without ".h5" -> wide window) and our earlier fix
(box search, window around BOTH ref and sec time, by-name) fixed the same bug differently. Neither alone covered both causes (crowded/capped list; provider listing a pair
under its secondary date). `find_granule` is now: narrow window around each of ref/sec -> `NisarClient.find_by_id` (their `granule_list=[id, id.h5]` version) -> box (when the caller has one)
-> wide windows around ref/sec (cap 250). Their test file kept as `tests/test_find_granule_busy.py`; ours as `tests/test_find_granule.py` (11 tests pass). By-name still not exercised against live ASF.

# Packaging + visibility fixes (2026-11-03)
- `app-ui/src/data/disasters.json` lived in a folder matched by the repo's `data/` .gitignore rule, so any git-based zip (zip_repo.py) dropped it and Explore > Events failed to build.
  Moved to `app-ui/src/lib/disasters.json`.
- Stale files from older merges removed (still present if you unzip over an old folder): `engine/common/defaults.py`, `tests/test_public_defaults.py`, `tests/test_eq_scan_selection.py`, `tests/test_select_no_signal.py`.
- MapSarControl: the silent states now say what they wait for ("asking the engine for this granule's layer list… Ns", "drawing… Ns"). Before, a pending layer-list request showed NOTHING under the checkbox.

# NASA reads: no time limit, survives tunnels (2026-11-06, on top of the 2026-11-05 repo)
The repo already removed the server's 25 s cap (`fut.result()` with no deadline), retries previews in the browser (`bg.patiently`, `bg.previewBlob({patient})`),
lays the Map out as a left column + bottom timeline dock, and shows exact step inputs/outputs (`Trace.step_io`). All of that is kept untouched. Added on top, because holding one
HTTP request open for minutes does not survive a Colab/Cloudflare tunnel (~100 s), re-sending the read starts it a second time, and `bg.granule` / probe / stack-pair still hit the browser's 90 s cap:
- `engine/api/bg_jobs.py` + `_run_bg`: a read answers in-request within `api.meta_wait_s`, else 202 + job id and keeps running; network failures are retried for ever with
  `api.retry_backoff_s`; definite errors (404/400/422) are final; an unattended read is cancelled after `api.job_idle_s`. Routes `GET|DELETE /api/background/jobs/{id}`.
  Settings: `config/retrieval.yaml` `api:` (env NISAR_API_*).
- `lib/bgApi.ts` `bgFetch` polls a 202 job with no overall limit (tunnel blips retried, abort cancels the job); every background route benefits, `patiently` still handles 409/429.
  `lib/bgWait.ts` + `components/BgWaitNote.tsx` show "still reading from NASA — N s, attempt n: <error>" in the Background header and the Map panel.
- Tests: `tests/test_bg_jobs.py` (9, no fastapi needed). NOT run here: pytest suite, tsc on the app, Playwright, a browser (fastapi/pyproj/h5py/node_modules absent in the sandbox).

# Unified (2026-11-06): the Data-panel rework + the NASA-read job layer
Base = the newest upload (Data as its own RIGHT-hand panel, `data=1` in the URL, Explore/Pin/Results on the left, timeline dock between them) — kept as is.
On top: the job layer from the section above (bg_jobs.py, bgApi polling, BgWaitNote). The two do not overlap: the newest upload does not touch bgApi.ts, routes_background.py,
settings.py or retrieval.yaml, so those files are exactly the previous upload plus the job layer. The wait note sits in the left panel (global, covers reads from either panel).

# UI: Data panel layout + dev badge + timeline lanes (2026-11-06)
Ported from the separately patched tree onto this merge; the backend and every other file of this merge are untouched.
- **Dev "N" badge no longer covers the map panel.** It sat bottom-left on top of the panel's last control (the "show a SAR layer" checkbox).
  `next.config.ts`: `devIndicators` is off by default; `NEXT_DEV_INDICATOR=1 npm run dev` brings it back, parked bottom-right. Dev-only, never in a build.
- **Data panel (right, desktop).** Replaces the previous right panel: it no longer spans the full height and no longer pushes the zoom/compass/scale cluster
  left (the `.data-open` rules in `globe-overrides.css` are gone). It starts below the controls and below the legend (measured), and opens from a "Data" pill
  under the controls (aria-label "Open Data panel"; the old left-header "Data" tab is gone). Selected-acquisition details and the SAR switch come first; the
  search-area form moved below them. The bottom timeline dock is used from 1360 px wide; below that the timeline draws inside the panel. Phone: Data stays the 4th sheet tab.
  URL: `data=1` (this merge's `urlstate`, unchanged); `panel=granules` still opens it. The left tab and the Data panel are both kept in the link.
- **Timeline (`GranuleTimeline.tsx`).** Overlapping GUNW pairs of one stack get their own lanes (touching pairs share a lane); GCOV dots have their own strip
  instead of sitting on the bars; bars are clipped to the plot and never draw over the stack labels; the verdict star is drawn inside its bar; the SVG is `width=100%`.
  Gap labels keep the engine-style start-to-start day count but the dashed connector is drawn only through space empty in every lane.
- Tests: `smoke` and `data-to-retrieval` open the panel with `getByRole("button", { name: "Open Data panel" })`.
- Ran: `tsc --noEmit`, `guard:no-science`, `tests-e2e/unit.spec.ts` (62 passed), and a server-render check of the timeline geometry (overlapping pairs in separate lanes, no dot on a bar).
  NOT run: Playwright browser specs (no browser could be downloaded), `npm run lint`, any visual check.

# 3D map (2026-11-06)
Real 3D terrain on BOTH the globe and the flat map, with the overlays draped over it. Display only; no science, no engine change.
- `GlobeMap.tsx`: a `raster-dem` source (AWS Terrain Tiles, terrarium encoding, no key, attribution added) drives `map.setTerrain` and a faint
  `hillshade` layer. Raster/image layers (the SAR corner-placed quad, result overlays) are draped by maplibre automatically, so they follow the relief.
  The camera tilts to 55 degrees only from zoom 8 up (at world zoom the globe stays upright); right-drag / Ctrl-drag tilts and rotates (max pitch 75 in 3D, 0 otherwise);
  the compass control now shows pitch. Turning 3D off flattens the camera and the terrain.
- Layer order changed on purpose: overlays are inserted BELOW the hillshade, so relief shows through a semi-transparent overlay, and therefore below the tile/footprint/
  detection outlines and the pins (they used to be painted over by an overlay).
- `SearchBar.tsx` / `GlobeApp.tsx`: a "3D" toggle next to Globe/Flat and a "relief x1-5" slider (default 1.5; flat deltas such as Bangladesh need exaggeration to read).
  ON by default; remembered in localStorage (`globe-3d`, `globe-3d-ex`).
- Ran: `tsc --noEmit`, `guard:no-science`, `unit.spec.ts`, and the exact terrain + hillshade + image-overlay style through maplibre's style-spec validator (0 errors).
  NOT run: any browser (none could be downloaded). Terrain tiles come from s3.amazonaws.com at runtime, so the page needs internet access to that host.

# Slimmer layout (2026-11-06)
More map: narrower side panels and a thin timeline stuck to the bottom edge. Layout only.
- Left column and the right Data panel: 400 px -> 340 px (the search pill was already 340). The dock sits between them at `left-[364px]` / `right-[364px]`, `bottom-3`
  (it was `bottom-9`, above the attribution; the attribution now only sits under the Data panel, which still ends at `bottom-9`). The dock is used from 1220 px wide (was 1360).
  Fit-to-footprint padding follows (left 360 / right 380).
- `GranuleTimeline` has a `compact` mode (used only in the dock; the inline/phone timeline is unchanged): lane 12 px (was 18), GCOV strip 9, axis 14, one-line row labels
  ("A069 asc"), one header row that also carries the visible window (the footer line and the px/ms scale are dropped), no "today" label, padding p-1, max height 30vh (was 42vh).
  For two stacks / 5 lanes the timeline svg is 100 px (full: 162 px); with header and padding the dock is roughly 130 px instead of roughly 310 px.
- Ran: `tsc --noEmit`, `guard:no-science`, `unit.spec.ts`, server-render height/overlap check. NOT run: any browser.

# 3D, part 2 (2026-11-06): buildings, sky, camera controls
Builds on the terrain from "3D map". Display only; no engine change.
- **3D buildings** (`GlobeMap.tsx`): a `fill-extrusion` layer over OpenFreeMap's free OpenMapTiles vector tiles (`https://tiles.openfreemap.org/planet`, no key), source-layer
  `building`, from zoom 13, heights from `render_height` / `render_min_height` (OpenStreetMap). Many OSM buildings have no height tag and fall back to 5 m blocks. Opacity 0.72 so a
  draped overlay under a building stays readable. Layer order: overlays < hillshade < buildings < outlines < pins. Attribution added. Switch: "buildings" (default on, `localStorage globe-3d-bld`).
- **Sky + horizon haze** while 3D is on (`map.setSky`, fades out before zoom 7 so the world globe keeps its plain look); `setSky()` resets it when 3D is off.
- **Camera controls** (SearchBar, shown only in 3D): Top-down (tilt 0, north up), Tilt (55), Low (72), rotate -/+ 30 degrees; for trackpads/phones where right-drag is awkward.
  One-shot `viewCmd` prop on `GlobeMap`, re-fired by a new key.
- New spec in `tests-e2e/smoke.spec.ts` (3D controls on by default; toggling hides the camera group).
- Ran: `tsc --noEmit`, `guard:no-science`, `unit.spec.ts`, and the terrain + sky + building-extrusion + DEM style through maplibre's style-spec validator (0 errors).
  NOT run: any browser, the new spec, or a request to tiles.openfreemap.org / s3.amazonaws.com (the sandbox cannot reach them). If OpenFreeMap's layer or field names differ from
  the OpenMapTiles schema the buildings layer simply draws nothing; nothing else depends on it.
