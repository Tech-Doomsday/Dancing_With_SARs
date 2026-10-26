# debug_hub: one local debug layer, three tabs

```bash
python tools/hub.py --open            # http://127.0.0.1:8765  (loopback only)
python tools/hub.py --allow-selftest  # also offers the synthetic SELF-TEST source (tests the hub itself)
```

Type a latitude / longitude (or paste `34.05, -118.25` into either box). The tile (MGRS id, EPSG, bbox) updates as you type.
**RUN** drives the *real* engine path and streams what happens:

`engine.retrieval NisarClient -> engine.common P01..P13 -> SceneBundle -> Registry.plan`

| Tab | Shows |
|---|---|
| **DATA RETRIEVAL** | the request; live transfer counters (requests, wire vs useful bytes, retries, cache tiers); every retriever call with timing; granules found + footprints; every clip as raw HDF5 layers: image, shape, dtype, stats, hover pixel value + lon/lat, click to enlarge |
| **HEALTH** | environment, credentials (presence only), dependencies found by scanning imports, module import test, storage / ancillary files, stub-vs-implemented per stage, pipeline file state, independence scan, the data-needs matrix; on demand: network reachability, pytest |
| **DATA PROCESSING** | input = the retrieval output; P01..P13 one by one (reads, writes, flags, assumptions, arrays); the SceneBundle; the hand-off table: for each of the 16 pipelines, what `Registry.plan` would do and which declared inputs are present |

## Authenticity rules

* **Observe only.** The retriever is wrapped by a recording proxy; the chain is observed through its own `observer` hook.
  `tests/test_debug_hub.py` asserts a hub run and a plain `build_scene_bundle` give the same bundle id.
* **No mocks in live mode.** Missing dependency, no credentials, ASF down: the page shows the engine's own error text.
* The only synthetic source is `--allow-selftest`; it is off by default and stamps a red banner on every screen.
* In HEALTH, counts and states are measured (AST parsing, `importlib`, `os.stat`, a real socket). `MEASUREMENT_NAMES` and `S_NAMES` in `health.py` are
  human-readable labels written by hand. `S_CHANNEL` is a hand-written S-code -> bundle-channel mapping that is verified at run time against
  `engine/common/ancillary.py` (`s_channel_check`); a channel not named there is shown as missing.
* Per-granule byte progress is not exposed by `NisarClient` (one blocking call per product), so progress is per call,
  from the client's own counters. Exposing a progress callback in the client would give per-granule bars.

## Security

Binds `127.0.0.1` only, rejects foreign `Host` headers, requires a per-process token on every POST, one job at a time.
