# UI build 3 — retrieval / job control / UI clarity ledger

One entry per change: files · why · how verified. "NOT RUN" = could not be run in the build sandbox (no network, no credentials).

Baseline before any change (sandbox, Python 3.13): `pytest -q` → **10 failed, 528 passed, 6 skipped** (failures are
pre-existing, same list after the change: test_api::test_run_equake_then_tile_verdicts, test_debug_hub ×2,
test_g0_los_sign, test_sar_debug ×3, earthquake_coseismic ×3). Playwright baseline on the original UI: 4 specs fail
here (background-gate-503 ×2, background-retrieval compare, screenshots2) — identical before and after.

## Backend
1. **/api/health `busy`** — `engine/api/main.py`, `engine/api/jobs.py` (`JobRunner.busy`, `started_at` on run state).
   `{kind, run_id, step, started_at} | null`, read without the worker pool or a long lock.
   Verified: tests/test_retrieval_stages.py::test_bbox_search_and_health_are_not_queued_behind_a_blocking_retrieval (health < 200 ms during a blocked retrieval).
2. **Metadata lane** — `jobs.py` (`_meta_pool`, 4 threads), `routes_background.py` (`_run_bg` uses it).
   Safe-by-construction check (read, not assumed): every metadata job builds its OWN `NisarClient` (own `HttpIO`, event
   loop, threads — `NisarClient.__init__`) and closes it; the only shared state is on-disk caches. Found and fixed one
   real cross-thread hazard: temp names used only the PID (`ClipStore.put`, `SearchCache.put`, catalog save,
   `GranuleIndex.save`) → two threads writing the same key would share a temp path. Now pid + thread id.
   Verified: bbox search answered in < 3 s while a retrieval blocked the worker (same test).
3. **No request open > 25 s** — `jobs.py` (`_meta_submit`, `META_WAIT_S=20`, `meta_job`), `routes_scanner.py`
   (`202 {pending, job_id}`, `GET /api/granules/jobs/{id}`), `routes_background.py` (25 s → 504 with a "ask again" text).
   Fast searches are still plain 200s (contract unchanged). Verified: test_slow_bbox_search_answers_202_then_polls.
4. **search_incomplete surfaced** — `granules_bbox.py` adds `search_incomplete`, `search_warnings` to the bbox response.
5. **Bounded granule concurrency, per-granule state, cancel, plan** — `engine/retrieval/clients/nisar_client.py`,
   `settings.py`, `config/retrieval.yaml` (`net.granule_concurrency: 4`). `_fetch_many` now uses a semaphore instead of
   `gather`-all; `granule_states` (queued → indexing → fetching → stored|skipped|failed|cancelled, bytes/chunks done
   vs planned, seconds, error); `cancel_event` checked between chunk batches (in-flight work finishes, nothing partial
   is stored as a clip); `plan_tile` computes exact compressed bytes per granule/layer from `L.sizes` before any data
   chunk is requested. Each granule is written to the clip store the moment it finishes (this already held per
   granule; with bounded concurrency granules now finish incrementally, which is what makes it visible).
   Verified: test_granule_concurrency_is_bounded_and_granules_finish_incrementally, test_real_client_cancel_stops_queued_granules.
6. **Retrievals (Resolve → Plan → Retrieve → Inspect → Preprocess)** — new `engine/api/retrievals.py`,
   `engine/api/routes_retrievals.py`, `main.py`. `POST /api/retrievals` (mode plan|retrieve), `GET`, `/{id}/cancel`,
   `/{id}/preprocess`, token-gated `/{id}/clips`, `/clips/image|stats|probe`, `/export.npz`, `/download.zip`.
   Manifest JSON per retrieval (tile, bbox, window, caps, layers regex, granules with clip key/state/bytes/timings,
   plan, flags). Runs on the retrieval worker; refused with 409 naming the active job if one is running.
   Resume = start again: stored granules are clip-cache hits. Verified: test_plan_mode_downloads_nothing…,
   test_retrieve_stores_then_resume_makes_zero_network_calls, test_cancel_leaves_stored_granules_usable.
7. **Preprocess from a stored retrieval** — `StoredRetriever` (no HTTP client in it), `JobRunner.start_scan(...,
   require_credentials=False, retrieval_id=)`, `p02_clip.py` records the mode as an assumption. Old path untouched.
   Verified: test_preprocess_from_manifest_makes_zero_network_calls (socket.connect patched to raise),
   test_api_preprocess_route_and_inspect_routes (route works with credentials removed).
8. `tools/measure_plan.py` — per-granule/per-layer byte plan + coalesce-gap comparison. **NOT RUN** (no network / credentials here).
9. Colab: `build_colab_zip.py` no longer packs `.env` by default (`--include-env` opt-in); optional Drive-save notebook cell; README note.

## UI (app-ui)
10. **Globe navigation** — `ExplorerApp.tsx` (router.push + saveQueryContext), `GlobeApp.tsx`/`GlobeMap.tsx` (map created at
    the requested/last camera, never world view → fly), new `lib/camera.ts` (sessionStorage camera per session).
    Verified: `tests-e2e/globe-navigation.spec.ts` — no document reload (window marker survives, 1 navigation entry),
    one live map, camera within 0.01° of target at zoom ≥ 7, no world-view camera ever stored. Map *creations*: 1 in
    the production build (`playwright.nav-prod.config.ts`), 2 under `next dev` (React StrictMode double-mounts effects).
11. **Honest errors** — new `lib/engineFetch.ts` (`engineFetch`, `engineJson`, pure `classifyEngineError`, `EngineError`
    with kind unreachable|timeout|busy|http + hint); `lib/api.ts`, `lib/bgApi.ts` route through it. Unit-tested in
    `tests-e2e/unit.spec.ts`.
12. **Search 202 polling + incomplete banner with Retry** — `lib/api.ts` (`resolvePending`), `ExplorerApp.tsx`; the
    "enter a box and press Search" empty state no longer shows after a failure.
13. **RetrievalPanel** (`components/background/RetrievalPanel.tsx`, mounted in Data retrieval) — stage bar, caps, plan
    table, per-granule table, "what is slow" line from measured MB/s, Cancel/Resume, stored-clip browser (decimated image
    labelled with its stride, histogram, stats, .npz/zip download), "Preprocess from this retrieval". Typecheck + build +
    no-science guard pass. **Not covered by a browser test** (no fixture-backed e2e written for the panel).
14. `ScanPanel.tsx` — plain step names; the "cannot be cancelled" line now points to the cancellable path (the old scan
    route itself is unchanged and still not cancellable).

## Merged from the second agent's build (updated_3.zip) — what was taken, what was not
Taken (each re-verified in this tree):
15. **Root cause of the failing Background search** — `granules_bbox.py::_hashable`: ASF's `mainBandPolarization` is a LIST for
    dual-pol granules; `group_acquisitions` put it in a set → `TypeError: unhashable type: 'list'`, failing the whole search.
    Test: tests/test_error_cors.py::test_dual_pol_granule_with_list_polarization_groups_without_crashing.
16. **Unexpected errors keep CORS** — `main.py` catch-all middleware registered inside CORSMiddleware: any unhandled exception
    becomes `500 {detail:{ok:false,error:"engine error: <Type>: <msg>"}}` WITH CORS headers (was a bare 500 → browser "Failed to fetch").
    Test: tests/test_error_cors.py::test_unexpected_search_error_is_json_with_cors.
17. **Lat / Lon / Range km fix the box** (Data retrieval) and **Range km on the Map panel** — `lib/boxFromCenter.ts`, `SearchBar.tsx`,
    `GlobeApp.tsx` (`rangeInput`/`customBox`/`shownPolygon`, query-context sync), `ExplorerApp.tsx`. Unit spec: tests-e2e/box-from-center.spec.ts.
18. **Footprint button brings the footprint into view** — `GlobeMap.tsx` (fill + brighter outline, `fitFootprintKey` → `fitBounds`),
    `GranuleDetail.tsx`/`DataPanel.tsx` (`onFootprintOnMap(ring, fit)`). Their root-cause note (swath off-screen at pin zoom) is a HYPOTHESIS, not measured in a browser.
19. `Health.busy` type in `lib/types.ts`.
Not taken (mine is a superset, or incompatible API): their `engineError.ts` (mine: `engineFetch.ts`, also asks /api/health for `busy`),
their `camera.ts` and map-create hook (mine: `camera.ts` with saved projection + e2e proof), their `retrievals.py`/`RetrievalPanel.tsx`
(no byte plan, no resume test, `bytes_*` null), `test_granule_concurrency.py` / `test_retrievals_api.py` (written against their
`cancel_requested` API; my tests cover the same properties on `cancel_event`), `test_meta_lane.py` (my test covers lane isolation).
