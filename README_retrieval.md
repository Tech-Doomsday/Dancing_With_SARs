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
engine/tiling/mgrs.py      MGRS tiles (id <-> bbox/polygon, tiles covering a footprint); config/tiling.yaml sets the cell size
engine/retrieval/state.py  SQLite: seen granules, version supersession, dirty-tile work queue (safe for many workers)
engine/retrieval/scheduler/granule_poller.py   6-hourly ASF diff -> tiles queued; tools/poll_granules.py is the CLI
config/retrieval.yaml      every knob (concurrency, gap, cache sizes, TTLs) - no absolute paths
tests/                     15 tests against a local ASF-like server (redirect, auth stripping, 503, expiry, offline)
tools/bench_synthetic.py   old approach vs new, reproducible
tools/probe_granule.py     RUN THIS ON A REAL GRANULE: layout, supported-filter check, cold/warm timings
```

## Verify everything on your machine (one command)
`python tools/final_check.py` - unit tests, ASF polling, real GUNW + GCOV tile fetch compared value-by-value with h5py,
cache tiers, restart, neighbour tile, 4 concurrent processes, then copies learned catalogs into `config/catalog/`.
Report: `<data dir>/final_check_report.txt`.

## Install / update (PowerShell)
Download `nisar_retrieval_final.zip`, then run the block from the chat message (it backs up engine/tools/tests/config,
copies ALL files over the repo, installs requirements and runs `python tools/final_check.py`, whose first step verifies
every file against MANIFEST.json).

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

## Tiles and polling
```python
from engine.tiling.mgrs import tile_at, tile_from_id, tiles_for_bbox
t = tile_at(-118.95, 34.65)                      # 10 km cell, e.g. 11SLU.. (size from config/tiling.yaml)
res = client.fetch_tile(t, product="GCOV", layers=r"grids/frequencyA/(HHHH|HVHV|mask)")

poller = GranulePoller(client)                   # python tools/poll_granules.py [--loop] [--region W S E N]
poller.poll()                                    # new granules -> tiles -> dirty_tiles queue
for item in poller.state.claim(20, worker="w1"): # pipeline workers
    client.fetch_tile(item["tile"], product=item["product"], layers=...); poller.state.complete(item["tile"], item["granule_id"])
```
Tile cells at zone boundaries exist in both zones (as with Sentinel-2). UTM latitudes only (-80..84).
