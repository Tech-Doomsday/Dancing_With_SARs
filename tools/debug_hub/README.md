# debug_hub: one local debug layer, four tabs

```bash
python tools/hub.py --open            # http://127.0.0.1:8765  (loopback only)
python tools/hub.py --allow-selftest  # also offers the synthetic SELF-TEST source (tests the hub itself)
```

Type a latitude / longitude (or paste `34.05, -118.25` into either box). The tile (MGRS id, EPSG, bbox) updates as you type.
**RUN** drives the *real* engine path and streams what happens:

`engine.retrieval NisarClient -> engine.common P01..P13 -> SceneBundle -> Registry.plan -> featured pipelines`

| Tab | Shows |
|---|---|
| **DATA RETRIEVAL** | the request; live transfer counters (requests, wire vs useful bytes, retries, cache tiers); every retriever call with timing; granules found + footprints; every clip as raw HDF5 layers: image, shape, dtype, stats, hover pixel value + lon/lat, click to enlarge |
| **HEALTH** | environment, credentials (presence only), dependencies found by scanning imports, module import test, storage / ancillary files, stub-vs-implemented per stage, pipeline file state, independence scan, the data-needs matrix; on demand: network reachability, pytest |
| **DATA PROCESSING** | input = the retrieval output; P01..P13 one by one (reads, writes, flags, assumptions, arrays); the SceneBundle; the hand-off table: for each of the 16 pipelines, what `Registry.plan` would do and which declared inputs are present |
| **PIPELINES** | input + output of the featured pipelines — **earthquake (3.01)** and **hurricane (3.13 tropical_cyclone)** — click a card to expand. INPUT: manifest identity, P12 routing decision + reasons, declared `uses`/`own_fetch` as state chips, the selected measurement pair, the bundle arrays the pipeline reads. OUTPUT: the registry-normalised `PipelineResult` — status, summary, narrative, raw_p (a rank score, never a probability), evidence items with support numbers, the decoded evidence layers as images (hover probe, click to enlarge), pairs/events/forward-screens/decision/uncatalogued details, provenance, warnings. |

## The PIPELINES tab, precisely

* `pipelines.py` runs the featured pipelines the way the registry would: `Registry.plan` decides
  routing + gating; launches go through `run_tile(workers=0)` (the production call path, inline),
  and every return is normalised by `coerce_result` against the manifest's `status_values`.
  Nothing is re-implemented and no result is invented.
* A pipeline routing declined is shown with the registry's own synthetic result and reasons
  (`NOT_APPLICABLE` / `NOT_RUN` / `INSUFFICIENT_EVIDENCE` / `PENDING`); a folder without `run()`
  shows `NOT_MODELLED`; a raise shows `ERROR` + trimmed traceback — and the job still completes.
* `detail.layers` (base64 grids inside the result) are decoded into the job's ArrayStore so the UI
  renders them like every other array; the base64 never crosses the wire (`/api/job?part=pipelines`).
* **SELF-TEST only:** the earthquake pipeline's event fetch is injected with the repo's calibrated
  fixture event (the T-8 truth-bowl recipe) so the synthetic source stays hermetic — no network call
  is mixed into a selftest run. The tab stamps a `SELFTEST INJECTION` note. In live mode
  `providers=None`: the real R1 ComCat adapter (and R2 ShakeMap corroboration) runs.

## Authenticity rules

* **Observe only.** The retriever is wrapped by a recording proxy; the chain is observed through its own `observer` hook.
  `tests/test_debug_hub.py` asserts a hub run and a plain `build_scene_bundle` give the same bundle id.
* **No mocks in live mode.** Missing dependency, no credentials, ASF down: the page shows the engine's own error text.
* The only synthetic source is `--allow-selftest`; it is off by default and stamps a red banner on every screen.
  Its scene is `common_fixtures.selftest_scene()` (the earthquake acceptance truth bowl) and, for the earthquake
  pipeline only, the injected fixture event provider — both clearly labelled in the UI.
* In HEALTH, counts and states are measured (AST parsing, `importlib`, `os.stat`, a real socket). `MEASUREMENT_NAMES` and `S_NAMES` in `health.py` are
  human-readable labels written by hand. `S_CHANNEL` is a hand-written S-code -> bundle-channel mapping that is verified at run time against
  `engine/common/ancillary.py` (`s_channel_check`); a channel not named there is shown as missing.
* Per-granule byte progress is not exposed by `NisarClient` (one blocking call per product), so progress is per call,
  from the client's own counters. Exposing a progress callback in the client would give per-granule bars.

## Security

Binds `127.0.0.1` only, rejects foreign `Host` headers, requires a per-process token on every POST, one job at a time.
