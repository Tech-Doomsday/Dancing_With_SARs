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
