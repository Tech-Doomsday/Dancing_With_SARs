# UI build log 3 — retrieval / job control / navigation

Ledger: one entry per change (files, why, how verified). Status of the TASK is at the bottom.

## 1. Metadata lane (Phase 2)
- Files: `engine/api/jobs.py` (`_meta_pool`, `busy_info`, `started_at`), `engine/api/routes_background.py`
  (`_run_bg` on the meta lane, `http_max_s`), `engine/api/routes_scanner.py` (TimeoutError -> JSON 504).
- Why: VERIFIED `jobs.py` had one `ThreadPoolExecutor(max_workers=1)` for scans, runs, bbox search and
  background previews, and `_run_bg` waited up to 600 s. A search issued during a scan queued behind it.
- Safety proof for two lanes: every job builds its OWN `NisarClient` via `_default_retriever_factory`
  (the client owns its threads and event loop; nothing is shared). On-disk caches: `DiskChunkCache`
  already used pid+thread temp names; `ClipStore.put` and `SearchCache.put` used pid only (two threads
  in one process could collide, and ClipStore rmtree'd the other's temp dir) -> thread id added in
  `engine/retrieval/clients/cache.py`.
- Cap: no request waits longer than `NISAR_HTTP_MAX_S` (default 25 s); the wait ends in a JSON 504 with
  the engine's own words. An uncaught exception would be a bare 500 with no CORS headers (browser: "Failed to fetch").
- Verified: `tests/test_meta_lane.py` 4 passed. Negative control: with `_meta_pool = _pool` the bbox test FAILS (504).
- NOT converted: bbox search is still synchronous up to 25 s (not 202 + poll).

## 2. /api/health `busy`
- Files: `engine/api/main.py`, `engine/api/jobs.py::busy_info`. Lock-free snapshot of live run states; never touches a pool.
- Verified: health < 200 ms during a blocked run and names run id (test above); `busy` is null when idle.

## 3. Honest errors (UI)
- Files: `app-ui/src/lib/engineError.ts` (new), `api.ts` (get/post go through it), `types.ts` (Health.busy).
- Kinds: unreachable / busy (names run + step) / cut / timeout / http (engine's own text). After a network failure it asks /api/health why.
- Verified: `tests-e2e/engine-error.spec.ts` 6 passed (node only, fetch stubbed — fixtures).
- Not covered: the one raw `fetch` in `GlobeApp.tsx` (tile-at) and `engineUrl.ts` health probe keep their own handling.

## 4. Globe navigation (UI)
- Files: `lib/camera.ts` (new), `GlobeMap.tsx` (creates at `initialCamera`, saves camera on moveend, `window.__nisarMapCreates` test hook),
  `GlobeApp.tsx` (initial camera from link > saved > world), `explorer/ExplorerApp.tsx` (both `window.location.href` -> `router.push`).
- Verified: `tsc --noEmit` clean; `npm run ci` (typecheck + no-science guard + build) OK.
- NOT VERIFIED: `tests-e2e/globe-navigation.spec.ts` was written but NOT RUN — no browser binary could be downloaded in this sandbox.
- Not checked: other remount paths (EngineLink reloads at lines 75/97).

## Status vs the TASK
Done: Phase 1 (navigation, honest errors, health.busy), Phase 2 (meta lane, 25 s cap, tests).
NOT done: search_incomplete banner + Retry in Background tab; Phase 3 stages 2–5 (plan, bounded-concurrency retrieve job,
manifest, cancel/resume, stored-clips inspector, preprocess-from-retrieval); Phase 4 UI; tools/measure_plan.py;
all HYPOTHESIS measurements (chunk shapes, coalesce gap, tunnel ~100 s cut, 53-granule need) — they need real credentials/network.

## 5. Bounded granule concurrency + cancel flag (Phase 3, Stage 3 — partial)
- File: `engine/retrieval/clients/nisar_client.py::_fetch_many`. VERIFIED before: `asyncio.gather` over every granule.
  Now an `asyncio.Semaphore(NISAR_GRANULE_CONCURRENCY, default 4)` gates the granules; "active" is registered only
  once a granule really starts. `client.cancel_requested = True` makes not-yet-started granules skip
  ("cancelled before it started"); in-flight ones finish and their clips stay stored (the clip store is written per granule).
- Verified: `tests/test_granule_concurrency.py` 2 passed (peak active == 3 with limit 3; mid-run done>0; cancel keeps 2 finished, skips 6).
- NOT done: no `/api/retrievals` routes, manifest, per-granule byte states, or resume-from-manifest; nothing sets
  `cancel_requested` from the API yet; the config key is an env var, not yet in `config/common.yaml`.

## 6. Retrievals API (Phase 3, Stage 3 — engine side)
- Files: `engine/api/retrievals.py` (RetrievalManager), `engine/api/routes_retrievals.py`, `engine/api/main.py` (router),
  `engine/api/jobs.py` (`busy_info` also reports a retrieval), `engine/retrieval/clients/nisar_client.py` (`granule_hook` via `_notify`).
- Routes: `POST /api/retrievals` (202 + id; lon/lat or bbox, products, lookback_days, layers regex, max_granules = newest N per product),
  `GET /api/retrievals/{id}` (state + counts + manifest), `POST /api/retrievals/{id}/cancel`. One retrieval at a time (409 names it).
- Per-granule state: queued -> fetching -> stored | skipped | failed | cancelled, with seconds, error text, clip key.
  Manifest JSON at `<data_dir>/retrievals/<id>/manifest.json`, rewritten atomically on every granule change; readable after an engine restart.
- Honest nulls: `bytes_done` / `bytes_planned` are null — NOT yet measured (needs the layout-index plan; Stage 2). No MB figures are invented.
- Verified: `tests/test_retrievals_api.py` 5 passed (done + failed granule + manifest on disk; newest-N cap flagged; cancel keeps the
  in-flight granule and cancels the rest; health shows `kind: retrieval`; 409 while running; 400s). Fake client = labelled fixture.
- NOT verified: against the real `NisarClient` / real ASF (no credentials here). Resume (clip-cache reuse with zero network) is a property of the
  existing clip cache and is NOT tested here. Still no UI for any of this, no Stage 2 plan, no Stage 4 inspector, no Stage 5 load-from-retrieval.

## 7. Retrieval panel in Background > Data retrieval (UI)
- Files: `app-ui/src/components/globe/explorer/RetrievalPanel.tsx` (new), `ExplorerApp.tsx` (mounts it under the timeline,
  box + levels from the existing inputs), `lib/api.ts` (`retrievals.start/get/cancel` + types).
- Shows: the six plain stages (Find granules -> Plan download -> Download -> Inspect -> Preprocess -> Pipelines) with the current one marked;
  Download / Cancel; `n/total granules finished · k downloading now`; flags as sentences; a per-granule table (state, seconds, engine note/error);
  the plain statement that data is stored on the engine disk (lost with the Colab session). Engine errors go through the classifier (§3).
- Honest gaps shown on screen: "Bytes per granule: not measured yet." No MB/s, ETA or byte progress is displayed (none is measured per granule yet).
- Verified: `tsc --noEmit` clean; `npm run ci` OK (typecheck, no-science guard, build).
- NOT VERIFIED: rendering / click behaviour — no browser here, no Playwright test written for the panel. Stage 2 "Plan download" is only a label (no plan table).

## 8. Lat / Lon / Range km fix the box (Data retrieval tab)
- Files: `app-ui/src/lib/boxFromCenter.ts` (new), `ExplorerApp.tsx` (three inputs beside West/South/East/North).
- Behaviour: Lat, Lon and Range (side length of a square box, 1–200 km) recompute West/South/East/North on every edit. A box that arrives
  from a tile, link or the map fills Lat/Lon/Range back (centre + N-S height). Unusable values show a message and leave the box untouched.
  Typing West/South/East/North by hand still works. Geometry only (111.32 km per degree of latitude, longitude scaled by cos(lat)); the engine still tiles/searches.
- Verified: `tests-e2e/box-from-center.spec.ts` 4 passed (the Dhaka box is reproduced within 0.002°; square in km at 60°N; round trip; invalid -> null).
  `tsc` clean, `npm run ci` OK. NOT VERIFIED in a browser (none available).

## 9. Range km on the MAP panel (beside Latitude / Longitude)
- Why: entry 8 added Range only to Background > Data retrieval; the user works on the Map tab and (screenshot) found no Range there.
- Files: `SearchBar.tsx` (Range km input + one-line note), `GlobeApp.tsx` (`rangeInput`, `customBox`, memoised `shownPolygon`, context sync).
- Behaviour: empty = the engine's own tile outline (as before). A value draws a square of that side (km) around the pin and writes it to the
  query context, so Data retrieval opens on that exact box. Invalid -> a visible message, box unchanged.
- Limit, stated on screen: a SCAN still covers the engine's tile (scans are one-tile); Range changes the drawn box and the Data retrieval box.
- Verified: `tsc` clean, `npm run ci` OK. NOT VERIFIED in a browser (none available); no e2e test for this panel.

## 10. Readable engine errors with CORS; footprint button brings the footprint into view
- Symptom (screenshots): Background > Data retrieval search ends in "connection was cut while the engine was still answering (the engine is reachable)";
  Map > "draw this pair's footprint on the globe" shows nothing.
- A. VERIFIED by reproduction (`tests/test_error_cors.py`, failed before the fix): the bbox route only caught ScanError/TimeoutError. Any other exception from the
  search (ASF/requests/etc.) was unhandled -> bare 500 from the outermost middleware, no JSON and no CORS header -> the browser reports only a network error,
  which the UI classified as "cut". Fix: `engine/api/main.py` catch-all middleware registered BEFORE CORS (so inside it) returns
  `500 {"detail": {"ok": false, "error": "engine error: <Type>: <message>"}}` with CORS headers and logs the traceback (logger `nisar.api`).
  The REAL cause of the user's failing search is still UNKNOWN: it needs one more Search press (the UI now shows the engine's text) or the engine log line `unhandled error on GET /api/granules`.
- B. VERIFIED by reading: the button and data path were wired (`onFootprintOnMap` -> `pairFootprint` -> `pair-footprint` source). Cause (HYPOTHESIS, consistent with
  the code and screenshots, not measured in a browser): the footprint is a swath hundreds of km wide and the map sits at the pin's zoom, so it is off-screen.
  Fix: `GlobeMap.tsx` outline now has a fill + brighter colour; the explicit button bumps `fitFootprintKey` -> `fitBounds` (maxZoom 9). Selecting a bar does not move the camera.
- Verified: new test passes; API regression 51 passed / 1 failed (the pre-existing earthquake test); `npm run ci` OK. NOT VERIFIED in a browser.
