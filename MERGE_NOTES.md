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
