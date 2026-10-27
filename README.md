# earthquake_coseismic port — skeleton deliverable

This tree is the **M0 slice** of `docs/IMPLEMENTATION_PLAN.md` (see that
document for the full engineering plan): the TypeScript science core of the
"Dancing with SARs" app, ported to Python inside the nisar-hazard-engine's
pipeline contract, plus the first serving slice (FastAPI) and a fresh minimal
UI that consumes it. Everything here is real: no mocks, no demo switches.

## What is in here

```
engine/
├── lib/okada.py                          # Okada85 DC3D — benchmark-exact (24 rows, rel < 1e-8)
├── pipelines/earthquake_coseismic/
│   ├── manifest.yaml                     # updated pipeline data sheet
│   ├── verdict.py                        # run(bundle) — the registry entry point
│   ├── prep.py detection.py model.py     # own_prep / detection kernel / forward screen + decision
│   ├── fetch.py types.py lib_local.py    # provider seam / dataclasses / ported statistics kernels
│   ├── README.md
│   └── tests/                            # 68 tests: okada benchmarks, kernels, chain end-to-end
├── retrieval/clients/adapters/
│   ├── r1_comcat.py                      # REAL USGS ComCat FDSN adapter (no auth, disk cache)
│   └── r2_shakemap.py                    # REAL ShakeMap extent adapter (evidence-only)
└── api/                                  # FastAPI serving layer + runs store (sqlite) + job runner
app-ui/                                   # fresh minimal Next.js UI — consumes the API, zero science
requirements-engine.txt                   # fastapi + uvicorn (everything else already pinned)
docs/IMPLEMENTATION_PLAN.md               # copy of the engineering plan
```

## How to apply

1. Copy the tree over the **repo root** of `nisar-hazard-engine` (it only adds
   or overwrites the files listed above).
2. `pip install -r requirements-retrieval.txt -r requirements-engine.txt`
3. Run the tests:

   ```
   python -m pytest engine/pipelines/earthquake_coseismic/tests -q
   python -m pytest tests/test_pipeline_independence.py tests/test_scene_bundle_contract.py -q
   ```

   The kernel tests (okada, lib_local, detection, model) run offline. The
   chain-level tests (`test_pipeline.py`) use the engine's own
   `tests/common_fixtures.py` synthetic truth — no network, no HDF5.

## What runs today (verified)

* Okada85 forward model — bit-level benchmark vs the official DC3D reference.
* The full pipeline path: bundle → pair selection → mask → far-field
  referencing → cycle-jump screen → detection (threshold + clusters + null
  screen) → event fetch (injected or real R1) → Okada LOS screen (both nodal
  planes, prediction referenced with the measurement's rule) → status decision
  → evidence layers. Synthetic truth: EQ scene ⇒ SUPPORTED, null scene ⇒
  NOT_SUPPORTED, provider failure ⇒ INSUFFICIENT_EVIDENCE, low coherence ⇒
  INSUFFICIENT_EVIDENCE, empty catalog ⇒ honest UNCATALOGUED/NOT_SUPPORTED.
* Registry integration: `Registry.run_tile(...)` routes and gates 3.01 and
  reports unimplemented siblings as NOT_MODELLED.
* FastAPI: `/api/health`, `/api/pipelines`, `/api/bundles[/{id}]`,
  `/api/events`, `POST /api/runs` (single-flight), `GET /api/runs[/{id}]`,
  `GET /api/runs/{id}/layers/{name}`.
* UI: overview page (health, bundle picker, launcher, runs) and run page
  (polling, verdict card, evidence, warnings, layer map).

## How to see it live

```
# 1. ingest a bundle (engine tools / debug hub) so data/bundles/<id>/bundle.json exists
# 2. serve the engine
python -m uvicorn engine.api.main:app --port 8900
# 3. serve the UI
cd app-ui && npm install && npm run dev        # http://localhost:3000
```

## Honest gaps (by milestone, see the plan)

* UNCATALOGUED optimizer with v1 simplifications lands in M2 (`detail.uncatalogued` is wired, the fit is M2 scope).
* R3 GNSS + S5 faults: deferred (M6 / ancillary wave); the pipeline degrades honestly.
* UI params form activates with the bundle-rebuild run path (M5); today params are audited but a pre-built frozen bundle's baked triggers win.
* Live-adapter tests are marked `@pytest.mark.live` (rate-limit friendly, not in default CI).
