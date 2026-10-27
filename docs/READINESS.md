# Pipeline readiness audit

Source of every statement below: `python tools/hub.py` (HEALTH + DATA PROCESSING tabs) run on the checkout dated 2026-10-25,
plus reading the code. Re-run the hub to refresh; this file is a snapshot, the hub is the truth.

**Verdict: the measurement path (retrieval -> P01..P13 -> SceneBundle) is built and contract-clean; the pipelines are not
yet implementable end to end, because of five gaps that sit upstream of the pipelines, not inside them.**

## What works

* P01..P13 + `SceneBundle` + `check_bundle(deep=True)`: all 13 steps run and the contract is clean on a full synthetic scene.
* `Registry.plan` routes and gates correctly, and says *why* (`NOT_APPLICABLE` vs `BLOCKED` vs `INSUFFICIENT_EVIDENCE`).
* Pipeline independence (import rules of `tests/test_pipeline_independence.py`, relative imports resolved; the hub does not mirror that test's dynamic-import and adapters-only rules): 136 files scanned, no violations.
* M1 (GUNW), M2 (GCOV), M3 (offsets), geometry, corrections audit (P07) have a real producer.

## Gaps, in the order they block work

**1. Pipeline-owned data: the adapters it relies on do not exist.**
Design (confirmed): the bundle carries the common preprocessed products plus raw data; reference and event data is pulled by the one
pipeline that needs it, from its own `fetch.py` (manifest `own_fetch`), so nothing is fetched for a tile that no pipeline will use.
The repo's independence test allows this: pipelines may not import another pipeline or the P01-P13 chain, and
`test_pipeline_uses_retrieval_only_through_reference_adapters` lets them import only `engine.retrieval.clients.adapters.*` from the retrieval
layer (so a `fetch.py` has to go through an adapter, never `NisarClient` directly). What is missing is the adapters: all 14 `adapters/r*_*.py` are comment-only stubs, and no S-source downloader exists.
Points to settle when writing the first `fetch.py`:
* **R1 (ComCat) is declared by five pipelines, not one.** The manifest `own_fetch` text for R1, as read from each manifest:
  earthquake_coseismic: "R1 (bbox+0.5 deg, M>=4, window +-1 day, within 400 km, nodal planes)";
  volcanic_deformation: "R1 low-magnitude seismicity (M>=2 within 30 km)";
  landslide: "R1 shaking trigger" (no query parameters stated);
  tsunami_inundation: "R1 trigger (shallow, offshore/near-coast, M>=7.0 or tsunami flag, nodal plane)";
  urban_damage: "R1 / R10 / R9 trigger context" (R1 grouped with R10 and R9; no query parameters stated).
  Only three manifests state query parameters; for landslide and urban_damage the query is not specified anywhere read. If they do not
  really need R1, trim their manifests. Either way each pipeline fetches for itself; duplicate calls are absorbed by a request-keyed cache
  in the adapter, never by sharing results between pipelines.
* **Testability:** `run(bundle)` has a fixed signature, so each `fetch.py` needs an injectable provider (module-level seam) so tests run offline.
* **Failure must be a result, not an exception:** a failed or empty fetch should map to `INSUFFICIENT_EVIDENCE` with the reason, never to "no event".
* **Not everything pipeline-specific can be deferred:** P12 decides *before* any pipeline runs whether it applies, using bundle-level ancillary
  (land cover, S12 permafrost, S8 glacier, S4 tree cover, S9 built-up, S11 river buffer, S7 volcano distance). Those stay in the bundle.

**2. Static ancillary layers are readable but never produced.**
`DirectoryAncillary` reads `data/ancillary/<name>/*.npz|tif`; nothing in the repo writes them. Effect, measured on a complete
synthetic NISAR scene (3 GUNW pairs, 6 GCOV epochs) with no ancillary: **8 of 16 pipelines are `BLOCKED`** (routing `unknown`, not launched):
volcanic_deformation, landslide, glacier_ice_flow, permafrost_thaw, deforestation, wildfire_burn_scar, river_erosion, urban_damage.
DEM (needed for slope/aspect/HAND and the P09 terrain products), WorldCover, JRC water occurrence, tree cover, built-up, permafrost,
glacier, river buffer, shoreline distance and volcano distance all have a bundle channel. **S5 (faults), S6 (basins), S13 (platforms)
have no channel at all.**

**3. Four measurement codes have no producer.**
M4 soil moisture (landslide, permafrost_thaw, flood_inundation, wildfire_burn_scar), M5 GSLC (urban_damage), M7 OPERA cross-check
(flood_inundation, urban_damage): not defined anywhere in retrieval. M6 Sentinel-1 (tropical_cyclone, oil_spill, iceberg_calving_drift):
`sentinel1_client.py` is a stub. Decide per code: build, or remove from `uses_codes`/mark optional in the manifest.

**4. The 16 pipelines are comment-only.**
0 of 16 define `run()`; the registry's entry point is `engine/pipelines/<name>/verdict.py: run(bundle)`. That is by design for now, but nothing
exercises the contract yet. First implementation target should be a pipeline whose inputs are already supplied: of the synthetic run,
only `earthquake_coseismic` was `READY` (needs M1 only), `flood_inundation` launched with gaps (M4, S3 absent).

**5. Everything after the pipelines is stub.** `engine/verdict/*` 0/5, `engine/api/*` 0/6, `engine/validation/*` 0/2,
`engine/lib/*` 0/5 (`okada`, `flood_detect`, ...), event pollers and `triggers.py` stubs (scheduler 1/7 implemented: only `granule_poller`).

## Registry contract gap (read by the hub with `ast`; HEALTH tab, "Registry contract")

`engine/registry.py` sets `ENTRY_MODULE = "verdict"` and `ENTRY_FUNC = "run"`, and the worker imports `engine.pipelines.<name>.verdict` and calls
`run(bundle)`. Nothing in the registry refers to a `fetch` module, so **no code calls a pipeline's `fetch.py`**. The registry docstring says
"A pipeline sees ONLY the bundle: no retriever", which contradicts the `own_fetch` design (manifests and `_template`: `fetch.py` pulls reference
data on demand). One of the two must change: either the registry/`verdict.run` flow is defined to call the pipeline's own `fetch.py`
(and the docstring is corrected), or the `own_fetch` design is dropped. This was not changed here (registry.py is out of scope).

## Hand-kept labels in the hub

`MEASUREMENT_NAMES` and `S_NAMES` in `tools/debug_hub/health.py` are human-readable labels written by hand. `S_CHANNEL` (S-code -> bundle
channel) is a hand-written mapping, verified at run time against `engine/common/ancillary.py` (`s_channel_check`, shown in the HEALTH payload;
a channel not named there is reported `missing`). Counts and states in HEALTH are measured.

## Hygiene findings

* **Dead modules:** `engine/common/runner.py`, `state.py`, `_raster.py` import `PreprocConfig`, which no longer exists. They fail to import and
  nothing in the live path imports them. Delete, or the import test in HEALTH stays red.
* **Undeclared dependencies:** `scipy` (top-level import in `_terrain.py`, `grids.py`) and `rasterio` (lazy) were not in any requirements file;
  `pyproject.toml` lists no dependencies. `scipy` is now in `requirements-retrieval.txt`; the file name is also misleading, since `engine.common` needs `pyproj`/`shapely` too.
* **Credentials:** the archive you sent contained `.env` with a real Earthdata username and password. `.gitignore` protects git, not a zip.
  Rotate the password if that archive went anywhere, and prefer `EARTHDATA_TOKEN`.
* **Repository drift:** GitHub `main` (3 commits) has `config/ docs/ engine/ tests/` only; `tools/` (every debug tool) is not pushed.
* `MANIFEST.json` covers only the retrieval files and lists a `README_retrieval.md` that is not in the tree.

## Not verified (could not be, from where this was written)

* **No live run.** The sandbox had no network, `aiohttp`, `h5py` or `zarr`, so `NisarClient` was never run against ASF. The first live RUN in the hub is
  that test; `sar_debug`'s README says the same about its own remote path.
* The full `tests/` suite has not been run. `tests/test_debug_hub.py`, `tests/test_pipeline_independence.py` and `tests/test_retrieval.py` were run in a later session: the first two pass; `test_retrieval.py::test_chunk_tables_load_lazily_per_layer` fails (pre-existing, cause not investigated), and `tests/test_cli_cloudflare_e2e.py` cannot be collected (no module `devflock`).
* `pyproj` was stood in for by the repo's own UTM code during verification, so tile coordinates were checked against that, not against `pyproj`.

---

## Addendum — 2026-10-27 (earthquake + tsunami port, Phase 0 + first two pipelines)

Status changes since the audit above (this audit's snapshots are historical; re-run the hub for the live view):

* **Gap 1 (adapters) resolved for the hazard path**: R1 ComCat (with provenance: url, retrieved_at,
  sha256), R2 ShakeMap, R3 NGL GNSS (holdings + tenv3; optional evidence), R11 tsunami catalog
  (local curated copy or remote, degrades to a note), R15 DART (realtime + historical archive,
  live-verified), R16 ETOPO1 (OPeNDAP ascii, live-verified, disk cache). Failures are degraded
  results, never exceptions.
* **Gap 4 (pipelines comment-only) resolved for two pipelines**: 3.01 `earthquake_coseismic`
  (full science + UNCATALOGUED fit) and 3.17 `tsunami_ocean_evidence` (NEW; heavy lane). The other
  14 remain scaffolds by design.
* **Gap 5 partially resolved**: `engine/lib/{okada,los,geo,detect}.py` and the whole
  `engine/lib/tsunami/` package are real and acceptance-tested (lake-at-rest exact well-balancing,
  dam-break mass drift < 1e-12, Ritter front speed, SSP-RK2 √(gh) speed, Kajiura transparency,
  eikonal ≤ 4 %). `engine/verdict/` 4.1–4.5 and `engine/api/` are implemented (FastAPI + runs store
  + verdicts.db). `engine/jobs/` (heavy lane) is new.
* **Fetch seam defined** (was the registry-contract gap): `run(bundle, providers=None)`;
  providers injected per call, module-level seam for tests, real adapters in production; a failed
  fetch maps to INSUFFICIENT_EVIDENCE with the verbatim reason. The registry docstring now states
  this (the old "a pipeline sees ONLY the bundle" wording was wrong).
* **Registry contract extended**: manifests may declare `execution: normal|heavy`, `timeout_s`,
  `needs_sar`; new registry status PENDING; per-manifest deadlines enforced in the pool.
* **Hygiene fixed (was: broken imports, missing deps, stray tests)**:
  `engine/common/{runner,state,_raster}.py` (dead `PreprocConfig` design) and 20 stray devflock
  test files + `tools/devflock_probe.py` quarantined under `attic/` (nothing imported them);
  `pyproject.toml` now declares its dependencies; the failing retrieval test
  (`test_chunk_tables_load_lazily_per_layer`) root-caused and fixed (a stale catalog forced the
  KeyError → full-walk fallback which DISCARDED already-loaded chunk tables; the runtime-learned
  catalog is now read first and the fallback carries layout-compatible loaded tables); the
  documented-but-missing regression fixes implemented (resolve-probe transient retry, h5py-GC
  deadlock broken with a private inline HTTP loop for the h5py-serving path, credential-safe
  `EarthdataAuth.__repr__`, `replace_dir(keep_existing=...)` with meta-first invalidation, search
  truncation flag, hermetic catalogs under pytest).
* **Tests**: `pytest tests/ engine/pipelines/earthquake_coseismic/tests
  engine/pipelines/tsunami_ocean_evidence/tests engine/lib/tsunami/tests tests/test_jobs.py
  tests/test_verdict_stages.py tests/test_api.py tests/test_g0_los_sign.py -q` is the new full
  suite (live network tests remain excluded by default).
