# Pipeline readiness audit

Source of every statement below: `python tools/hub.py` (HEALTH + DATA PROCESSING tabs) run on the checkout dated 2026-10-25,
plus reading the code. Re-run the hub to refresh; this file is a snapshot, the hub is the truth.

**Verdict: the measurement path (retrieval -> P01..P13 -> SceneBundle) is built and contract-clean; the pipelines are not
yet implementable end to end, because of five gaps that sit upstream of the pipelines, not inside them.**

## What works

* P01..P13 + `SceneBundle` + `check_bundle(deep=True)`: all 13 steps run and the contract is clean on a full synthetic scene.
* `Registry.plan` routes and gates correctly, and says *why* (`NOT_APPLICABLE` vs `BLOCKED` vs `INSUFFICIENT_EVIDENCE`).
* Pipeline independence: 136 files scanned, no pipeline imports another pipeline, the chain, or anything from retrieval (no adapter imports yet, because every pipeline file is a stub).
* M1 (GUNW), M2 (GCOV), M3 (offsets), geometry, corrections audit (P07) have a real producer.

## Gaps, in the order they block work

**1. Reference data (R\*): decided, but the contract and the adapters are not there yet.**
Decision (yours): a pipeline retrieves its own context data (ComCat, ERA5, FIRMS, ...) itself; the bundle carries measurements,
common preprocessed products, raw layers and static ancillary, not R\* data. The manifests' `own_fetch` and each folder's `fetch.py`
("reference adapters / ancillary this pipeline pulls on demand") say the same. What is missing, read from the code by the HEALTH tab:
* **No caller.** `engine/registry.py` imports `engine.pipelines.<name>.verdict` and calls `run(bundle)`; nothing imports or calls `fetch.py`.
  So an own_fetch can only happen inside `run(bundle)` or something it imports. The registry docstring still says "a pipeline sees ONLY the bundle: no retriever".
  One of the two has to change (docstring, or a defined `fetch` step).
* **No adapters.** All 14 `engine/retrieval/clients/adapters/r*_*.py` are comment-only stubs. They live in the shared retrieval layer, so a pipeline can reach them
  only if the independence rule allows it. HEALTH now applies this rule and prints it: own package, `engine.lib`, bundle types from `engine.common`,
  and from retrieval ONLY `engine.retrieval.clients.adapters.*` (never `NisarClient`, scheduler or the chain). `tests/test_pipeline_independence.py` enforces it.
  The adapter allowance is my reading of your decision plus the manifests, not a sentence in ARCHITECTURE.md: please confirm it.
* **Who declares what (manifests, not opinion).** R1 (ComCat) is declared by **five** pipelines, not one: earthquake_coseismic, landslide ("R1 shaking trigger"),
  tsunami_inundation ("R1 trigger"), urban_damage ("R1 / R10 / R9 trigger context") and volcanic_deformation ("R1 low-magnitude seismicity"). If only the earthquake
  pipeline should use ComCat, the other four manifests need editing; the HEALTH matrix follows the manifests automatically.
  R5 (ERA5) is declared by six, R4 (IMERG) by four, R10 by four.
* **Parallel duplicates.** Pipelines run in separate processes, so two pipelines that declare the same R-source fetch it twice unless the adapters share
  `retrieval/clients/cache.py` (TTL disk cache). Adapters should use it.
* **Scheduler path is separate.** ComCat is also meant to be polled by `scheduler/event_pollers/comcat.py` to raise the `triggers={"quake": ...}` that P12 routes on
  (`RequestInfo.triggers`). That poller is a stub too. Routing on a trigger and a pipeline's own R1 fetch are two different uses.

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

## Hygiene findings

* **Dead modules:** `engine/common/runner.py`, `state.py`, `_raster.py` import `PreprocConfig`, which no longer exists. They fail to import and
  nothing in the live path imports them. Delete, or the import test in HEALTH stays red.
* **Hand-kept labels:** the hub's `S_CHANNEL` / `S_NAMES` / `MEASUREMENT_NAMES` are labels written by hand; `S_CHANNEL` entries are now checked for presence in `engine/common/ancillary.py` (HEALTH shows the result).
* **Undeclared dependencies:** `scipy` (top-level import in `_terrain.py`, `grids.py`) and `rasterio` (lazy) were not in any requirements file;
  `pyproject.toml` lists no dependencies. `scipy` is now in `requirements-retrieval.txt`; the file name is also misleading, since `engine.common` needs `pyproj`/`shapely` too.
* **Credentials:** the archive you sent contained `.env` with a real Earthdata username and password. `.gitignore` protects git, not a zip.
  Rotate the password if that archive went anywhere, and prefer `EARTHDATA_TOKEN`.
* **Repository drift:** GitHub `main` (3 commits) has `config/ docs/ engine/ tests/` only; `tools/` (every debug tool) is not pushed.
* `MANIFEST.json` covers only the retrieval files and lists a `README_retrieval.md` that is not in the tree.

## Not verified (could not be, from where this was written)

* **No live run.** The sandbox had no network, `aiohttp`, `h5py` or `zarr`, so `NisarClient` was never run against ASF. The first live RUN in the hub is
  that test; `sar_debug`'s README says the same about its own remote path.
* The full `tests/` suite was not run (no `pytest`). The hub's own tests pass under a minimal runner; run `pytest tests/test_debug_hub.py` and the HEALTH tab's "run test suite".
* `pyproj` was stood in for by the repo's own UTM code during verification, so tile coordinates were checked against that, not against `pyproj`.
