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
