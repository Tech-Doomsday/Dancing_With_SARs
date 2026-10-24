# Retrieval layer (step 1): fast NISAR streaming + tiered cache

Drop-in for `engine/retrieval/` in Dancing_With_SARs. Implements ARCHITECTURE §8
`fetch(bbox, t0, t1, product, layers) -> clips`, built on what `nisar-data_analysis` proved
(range-read clipping, version de-dup, EPSG lookup, 1-px margin, atomic writes) but without its
per-clip costs.

```
engine/retrieval/
  settings.py              config/retrieval.yaml + .env + NISAR_* overrides; data dir from NISAR_DATA_DIR
  auth/secrets.py          Earthdata token or user/password, one place
  clients/
    range_io.py            one async loop, pooled keep-alive, redirect resolved ONCE, coalesced+concurrent ranges, retry
    h5_index.py            per-granule layout index: chunk byte offsets, filters, axes, EPSG (built once, ~ms to load)
    decode.py              deflate/shuffle/fletcher32 without libhdf5 (threads scale, no global lock)
    cache.py               RAM LRU -> disk chunk cache -> Zarr clip store; TTL, stale-while-error; search cache
    nisar_client.py        search -> index -> chunk-exact fetch -> assemble; singleflight; prefetch; h5py fallback
config/retrieval.yaml      every knob (concurrency, gap, cache sizes, TTLs) - no absolute paths
tests/                     15 tests against a local ASF-like server (redirect, auth stripping, 503, expiry, offline)
tools/bench_synthetic.py   old approach vs new, reproducible
tools/probe_granule.py     RUN THIS ON A REAL GRANULE: layout, supported-filter check, cold/warm timings
```

## Install
`pip install -r requirements-retrieval.txt` (the client checks at startup and names anything missing).

## Use
```python
from engine.retrieval.clients.nisar_client import NisarClient
c = NisarClient()                                   # reads .env + config/retrieval.yaml
r = c.fetch((-118.5, 33.9, -118.0, 34.2), "2026-07-01", "2026-10-20", product="GUNW",
            layers=r"unwrappedPhase|coherenceMagnitude|connectedComponents")
for clip in r.clips:                                # r.errors / r.skipped / r.degraded are never silent
    for grid in clip.grids.values():
        grid.layers["science__LSAR__GUNW__grids__frequencyA__unwrappedInterferogram__HH__unwrappedPhase"]
c.prefetch(list_of_tile_bboxes, ...)                # warm caches ahead of the scanner
```
Layer keys are the prototype's flat `__` names, so downstream code that read the old `.npz` clips keeps working.

## Design decisions worth knowing
- **Cache key**: granule-level (`granule_id + bbox + layers`) instead of the query-level key in §8, so two
  queries that share a granule share work, and processing-versioned granules never expire. The query-level
  part (t0/t1/product) lives in the TTL'd search cache (6 h = poller cadence).
- **Stale-while-error** covers search results and expired clips; failures land in `FetchResult.errors`.
- **Process pool friendly**: all disk writes are atomic (tmp + rename); each worker builds its own client.
- Boxes crossing +-180 deg are not supported (same as the prototype).

## Not verified against live ASF (cannot reach it from my sandbox)
Redirect/presigned-URL behaviour, real chunk sizes and filters, real latency. `probe_granule.py` answers all
three in one run. Files whose layers use other HDF5 filters, or contiguous layers, take the h5py fallback
(still block-cached) and are reported by the probe.
