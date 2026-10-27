# Instructions — running, testing and demonstrating this repo

Working name: **NISAR hazard engine** — a modular Python engine that turns NISAR SAR data
(SceneBundles built by the P01–P13 chain) into per-hazard verdicts through independent pipelines.
The two science pipelines implemented so far are `earthquake_coseismic` (3.01) and
`tsunami_ocean_evidence` (3.17, heavy lane); `tropical_cyclone` (3.13, "hurricane") is scaffolded
but not implemented yet — the registry answers `NOT_MODELLED` for it, honestly.

This revision also carries the final-review fixes: catalogued-but-unscreenable events answer
`INSUFFICIENT_EVIDENCE` (`events_unscreenable`), quiet tiles answer `NO_SIGNAL`, the uncatalogued
source fit lives in `engine/pipelines/earthquake_coseismic/uncatalogued.py` (strike scan +
least-squares refinement; a known vertical strike-slip source is recovered within 500 m / 8°),
run parameters are APPLIED (not just recorded), and every run writes replay artifacts.

Everything below is runnable from the repo root. There is no compiled component and no external
service to configure except (optionally) NASA Earthdata credentials for live data.

---

## 1. Setup (once)

```bash
cd nisar-hazard-engine
python3 -m venv .venv && source .venv/bin/activate
pip install -e .                 # or: pip install -r requirements-engine.txt -r requirements-retrieval.txt
```

* Live data additionally needs Earthdata credentials in a `.env` at the repo root
  (`EARTHDATA_TOKEN` or `EARTHDATA_USERNAME`/`EARTHDATA_PASSWORD`). Without them the engine
  still runs, searches still work, and the UI shows exactly where it stops — never a fake result.
* No `.env` is required for the test suite or the SELF-TEST demo (both are offline and hermetic).

---

## 2. The UI (most important) — the debug hub

```bash
python tools/hub.py               # http://127.0.0.1:8765   (loopback only)
python tools/hub.py --open        # same, opens the browser
python tools/hub.py --allow-selftest   # also offers the synthetic SELF-TEST source (offline demo)
python tools/hub.py --port 9000
```

Type a latitude/longitude (or paste `34.05, -118.25` into either box), press **RUN**, and watch the
real engine path stream live. Four tabs:

| Tab | Shows |
|---|---|
| **DATA RETRIEVAL** | the request; live transfer counters (requests, wire vs useful bytes, retries, cache tiers); every retriever call with timing; granules found + footprints; every clip as raw HDF5 layers (image, shape, dtype, stats, hover pixel value + lon/lat, click to enlarge) |
| **HEALTH** | environment, credentials (presence only), dependencies, module import test, storage/ancillary files, stub-vs-implemented per stage, pipeline file state, independence scan, data-needs matrix; on demand: network reachability, pytest |
| **DATA PROCESSING** | input = the retrieval output; P01..P13 one by one (reads, writes, flags, assumptions, arrays); the SceneBundle; the hand-off table for all 16 pipelines |
| **PIPELINES** (new) | **input and output of the featured pipelines — earthquake (3.01 `earthquake_coseismic`) and hurricane (3.13 `tropical_cyclone`)** — run through the real registry path on exactly the bundle this run produced |

### The PIPELINES tab — what you get and how to use it

* Two cards are always listed: **3.01 earthquake_coseismic** and **3.13 tropical_cyclone**.
  The tab badge shows each one's status at a glance (e.g. `SUPPORTED · NOT_APPLICABLE`).
* **Click a card to expand it.** The expanded view has two halves:
  * **INPUT — what the pipeline is and what it sees**: manifest identity (code, sensor, min_epochs,
    whether `run()` is implemented), the P12 routing decision with its reasons, the declared bundle
    inputs (`uses`) and own-fetch adapters as green/red chips (hover for the reason), the measurement
    pair the pipeline selected (ref → sec, granule ids, valid pixels, reference offset and σ), and
    the actual bundle arrays it reads (interferograms, geometry, stable mask / backscatter) as
    image cards taken unchanged from DATA PROCESSING.
  * **OUTPUT — the pipeline's own `PipelineResult`**: status badge + headline summary, the narrative
    (what was found and how reliable it is), `raw_p` (labelled as a within-tier **rank score, not a
    probability**), the decoded **evidence layers as images** (displacement, threshold, detected,
    residual — hover to probe values, click to enlarge, just like everywhere else), every evidence
    item (level, kind, claim, support numbers), and expandable details: pairs analysed, events
    considered/excluded, Okada forward screens (predicted vs measured peaks, amplitude ratio,
    pattern correlation), the decision record, the uncatalogued-source fit, pipeline params,
    provenance, data used, references and warnings.
* **Honesty rules** (enforced, not decorative): routing-declined pipelines are shown with the
  registry's own answer and reasons (`NOT_APPLICABLE`, `NOT_RUN`, `INSUFFICIENT_EVIDENCE`,
  `PENDING`) — never hidden. A pipeline without `run()` shows `NOT_MODELLED`. A crash shows
  `ERROR` with the trimmed traceback, and the rest of the job is unaffected.
* In SELF-TEST mode the earthquake pipeline's event fetch is pointed at the repo's calibrated
  fixture provider (no network); the tab stamps a `SELFTEST INJECTION` note saying exactly that.
  In live mode `providers=None` and the real R1 ComCat adapter (and R2 ShakeMap corroboration) run.

---

## 3. What can I show — ready-made demos

1. **The flagship demo (offline, no credentials, ~10 s):** start `python tools/hub.py --allow-selftest`,
   choose source `selftest`, keep the default coordinates (Los Angeles, `34.05, -118.25`), press RUN.
   * DATA RETRIEVAL: synthetic granules in real NISAR layer-key layout, live transfer counters.
   * DATA PROCESSING: P01–P13 with arrays, flags and assumptions; a red SELF-TEST banner is shown
     the whole time.
   * PIPELINES → click **earthquake**: the synthetic scene is the same truth bowl the pipeline's own
     acceptance tests use (a +50 mm LOS deformation over the newest pair), so the pipeline detects
     it, ranks the fixture event, passes the Okada forward screen and answers **SUPPORTED** with
     layer images, evidence and the full decision trail. Then expand **tropical_cyclone**: the
     registry's honest `NOT_APPLICABLE` with the routing reason ("no active/recent storm or
     high-wind trigger in the request") — a good talking point about how the engine refuses to
     invent results.
2. **Live demo (needs `.env` credentials + network):** source `live`, a tile you know has NISAR
   coverage. Retrieval hits ASF for real; the PIPELINES tab runs the earthquake pipeline against
   the real USGS ComCat catalog. Any missing dependency/credential shows the engine's own error.
3. **Robustness demo:** press RUN on an ocean tile, or with no credentials — the UI shows the
   engine's own messages (routing decisions, credential warnings), never a mock.
4. **Retrieval-layer dashboards (separate, dependency-free):**
   `python tools/debug_view.py --demo` — poller/tile-queue dashboard over fabricated sample data;
   `python tools/debug_view.py` — the same dashboard over your real retrieval state (read-only).
5. **Performance claim:** `python tools/bench_synthetic.py --latency-ms 80 --tiles 12` — the old
   prototype approach vs the engine on a synthetic granule with injected latency.
6. **Full acceptance sweep of the retrieval layer (real network, 5–10 min):** `python tools/final_check.py`.
7. **Replay an audited run offline (plan 13):** after any API run, `python tools/replay_run.py <run_id>`
   re-runs the recorded pipelines from `data/runs/<run_id>/params.json` + the stored bundle and
   prints each verdict next to what the run originally reported — what the run did must be
   reproducible from what it wrote (`--json` for machine-readable output).
8. **Parameter-override demo:** in the web UI, set *Detection floor* = 0.2 before RUN — the
   verdict comes back `NOT_SUPPORTED` / `NO_SIGNAL` with `detail.params.detection_floor_m = 0.2`,
   proving overrides reach the science. Out-of-range values are rejected with 400 before a run starts.

---

## 4. Commands to run each test

Run everything from the repo root with the venv active.

```bash
python -m pytest -q              # the FULL suite (all sections below; ~5–6 min, offline)
python -m pytest -q tests/       # engine-level suites only
EQ_LIVE=1 python -m pytest -m live -q tests/test_adapters_r1_r2.py   # live USGS (opt-in)
```

Per suite (each file is independently runnable):

| Suite | Command | What it pins |
|---|---|---|
| Retrieval client | `python -m pytest -q tests/test_retrieval.py` | NisarClient: search/fetch/clips, cache tiers, catalog laziness, concurrency |
| Regression pack | `python -m pytest -q tests/test_regressions.py` | documented regression fixes: resolve() retry, h5py-GC deadlock, auth redaction, replace_dir, search truncation, hermetic catalogs |
| Poller | `python -m pytest -q tests/test_poller.py` | ASF polling → granules → tile queue state machine |
| MGRS tiling | `python -m pytest -q tests/test_mgrs.py` | tile ids, EPSG, bboxes, margins |
| Common foundation | `python -m pytest -q tests/test_common_foundation.py` | config, grids, frozen arrays |
| Common chain | `python -m pytest -q tests/test_common_chain.py` | P01–P13 order, reads/writes, bundle assembly |
| Bundle contract | `python -m pytest -q tests/test_scene_bundle_contract.py` | `check_bundle` over persisted bundles |
| Common truth | `python -m pytest -q tests/test_common_truth.py` | synthetic truth fixtures themselves |
| Common debug | `python -m pytest -q tests/test_common_debug.py` | the Tracer used by the hub |
| G0 sign gate | `python -m pytest -q tests/test_g0_los_sign.py` | THE LOS convention pin (engine vs app sign flip) + metamorphic bundle-flip test |
| Verdict stages | `python -m pytest -q tests/test_verdict_stages.py` | 4.1 collect, 4.2 calibrate, 4.3 link, 4.4 select, 4.5 persist |
| Jobs lane | `python -m pytest -q tests/test_jobs.py` | heavy-lane runner: cache, progress, deadline/cancel |
| API | `python -m pytest -q tests/test_api.py` | /runs, /tiles, /jobs routes over a persisted bundle; FLAT run-state contract; params are APPLIED (0.99 reaches detail.params) + 400 on out-of-range/unknown params; 409 single-flight; /api/events offline + 502; layer endpoint with bounds_lonlat; replay artifacts on disk |
| R1/R2 adapters | `python -m pytest -q tests/test_adapters_r1_r2.py` | ComCat parse + cache + failure drill; the (payload, sha) tuple regression; R2 normalised result + tile-coverage check; network-guarded units + `EQ_LIVE=1 pytest -m live` |
| Robustness | `python -m pytest -q tests/test_robustness.py` | malformed inputs, degraded modes |
| Pipeline independence | `python -m pytest -q tests/test_pipeline_independence.py` | pipelines never import each other or the chain |
| Debug hub | `python -m pytest -q tests/test_debug_hub.py` | hub observes without changing the bundle; PIPELINES tab (earthquake SUPPORTED through the registry path, hurricane honest refusal, failure containment, JSON-safe payloads, served images) |
| Earthquake pipeline | `python -m pytest -q engine/pipelines/earthquake_coseismic/tests/` | kernels (robust stats, Okada incl. the dip-90 vertical-fault regression, detection), prep, model + decision table (events_unscreenable / NO_SIGNAL / fit-converged-only), end-to-end via the registry |
| Uncatalogued fit | `python -m pytest -q engine/pipelines/earthquake_coseismic/tests/test_uncatalogued.py` | quadtree + strike-scan fit: recovers a known vertical strike-slip source (500 m / 8°), honest refusal on non-representable fields (~40 s) |
| Tsunami lib | `python -m pytest -q engine/lib/tsunami/tests/` | source/Kajiura/SWE/eikonal/gauges acceptance (lake-at-rest, dam-break mass, Ritter front, Kajiura ≤2%, eikonal ≤4%) |
| Tropical cyclone (stub) | `python -m pytest -q engine/pipelines/tropical_cyclone/tests/` | result-contract tests for the not-yet-implemented pipeline |

Handy filters:

```bash
python -m pytest -q tests/test_debug_hub.py -k pipelines      # only the PIPELINES-tab tests
python -m pytest -q engine/pipelines/earthquake_coseismic/tests/test_pipeline.py -k supported
python -m pytest -q engine/pipelines/earthquake_coseismic/tests/test_model.py -k decide
```

The hub can also run pytest itself: HEALTH tab → *run test suite (pytest)*.

UI (`app-ui/`) — zero science in the browser, enforced by CI:

```bash
cd app-ui
npm install                      # once
npm run ci                       # typecheck + no-science guard + next build (must pass)
npm run test:e2e                 # Playwright smoke (2 specs, ~12 s; auto-installs chromium)
```

The no-science guard (`app-ui/scripts/no-science-guard.mjs`) fails the build if any science
identifier (okada, nodal planes, LOS math, thresholds …) or a non-transport import appears in
`app-ui/src` — the UI is a thin client of engine/api and stays that way.

---

## 5. The debug layer — how it works (for maintainers)

Everything lives in `tools/debug_hub/` (stdlib server + one static HTML page):

| File | Role |
|---|---|
| `__main__.py` / `tools/hub.py` | launcher (port, `--open`, `--allow-selftest`) |
| `server.py` | localhost HTTP + SSE; anti-DNS-rebinding Host check, per-process POST token, one job at a time; serves `/api/job?part=pipelines` |
| `runner.py` | one tile through the REAL path: source → recorded retriever → `build_scene_bundle` (observed through its own hook) → hand-off → **featured pipelines** |
| `pipelines.py` (new) | the PIPELINES tab backend: runs `earthquake_coseismic` + `tropical_cyclone` through the registry's own `plan → run_tile(workers=0) → coerce_result`; decodes `detail.layers` (base64 grids) into the job ArrayStore and strips them from the JSON; injects the calibrated fixture event provider in SELF-TEST mode only |
| `recorder.py` | wrapping proxy around the retriever (calls, timing, network deltas) |
| `events.py` | per-job append-only event log → SSE |
| `store.py` / `imaging.py` | ArrayStore + own PNG encoder (no numpy deps beyond numpy itself); pixel probes read stored arrays |
| `handoff.py` | asks `Registry.plan()` what would launch per pipeline and checks every declared input |
| `health.py` | measured environment/dependency/independence/needs-matrix snapshots |
| `ui/index.html` | the whole UI (no build step): four tabs, image gallery, pixel probe, modal viewer, live log |
| `engine/api/jobs.py` | the SERVING path (web UI's API): applies run params to the bundle request, writes replay artifacts `data/runs/<id>/` (`params.json`, `result.json`, `layers/*.npy`), persists calibrated verdicts |
| `tools/replay_run.py` | offline replay of a stored run (audit path, plan 13) |

Design rules:

* **Observe only.** The retriever is wrapped, the chain is watched through its own observer hook;
  `tests/test_debug_hub.py` asserts a hub run and a plain `build_scene_bundle` produce the same
  content-addressed bundle id.
* **No mocks in live mode.** The only synthetic thing is the SELF-TEST source (off by default,
  red banner on every screen), and — inside SELF-TEST only — the earthquake event provider.
* **One execution path.** The PIPELINES tab does not re-implement routing, gating or result
  normalisation; it calls the registry (`Registry.plan`, `run_tile(workers=0)`, `coerce_result`),
  so what you see is exactly what a production tile run would produce.
* **Failure containment.** A featured pipeline that raises becomes an `ERROR` result with its
  traceback on the tab; the job itself still completes with retrieval, processing and hand-off intact.

To feature another pipeline later: add one line to `FEATURED` in `tools/debug_hub/pipelines.py`
(name, display label, bundle array groups to show as input) — routing, gating, execution,
normalisation and the UI adapt automatically.

---

## 6. Troubleshooting

* **"no Earthdata credentials"** — expected without `.env`; search still works, file opening fails.
  Use the SELF-TEST source for offline demos.
* **Port already in use** — `python tools/hub.py --port 9000`.
* **A job seems stuck** — only one job runs at a time (the real client owns threads and an event
  loop); the footer bar shows the live stage. ComCat/ASF latency lands in the PIPELINES tab's
  runtime, not in a silent hang: per-pipeline timeouts come from each manifest (`timeout_s`).
* **`tropical_cyclone` shows NOT_MODELLED / NOT_APPLICABLE** — correct: the pipeline is scaffolded
  (manifest + routing) but has no `run()` yet, and routing correctly declines land tiles.
* **`NO_SIGNAL` on a quiet tile** — correct and distinct: no catalog event in the window AND no
  coherent cluster. "Nothing happened here" is a different sentence from "a quake was tested and
  rejected" (`NOT_SUPPORTED`).
* **`INSUFFICIENT_EVIDENCE` with `events_unscreenable`** — correct: catalogued event(s) exist in
  the window but publish no moment tensor, so the Okada screen cannot run. A data gap, never
  evidence about the hypothesis.
* **Tests fail on imports** — activate the venv from step 1; the suite needs `numpy`, `scipy`,
  `pyproj`, `yaml`, `pytest` (all declared in `pyproject.toml`).
