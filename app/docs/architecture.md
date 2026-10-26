# Architecture

## Module map and data flow

```
                          ┌──────────────────────────────────────────────────┐
                          │  Real data on disk (immutable inputs)            │
                          │  data/nisar-sample/…h5      (open NISAR sample)  │
                          │  data/nisar-browse/*.png    (official browses)   │
                          │  data/derived/nisar-la-2008/  (extracted layers) │
                          │  data/derived/panama-2026/   (cached records)    │
                          └───────────────┬──────────────────────────────────┘
                                          │ tools/extract_gunw.py (offline, lossless)
                                          ▼
  ┌─────────────────────┐        ┌────────────────────┐
  │ Live services       │        │ src/lib/data       │
  │ NASA CMR (search)   │◄──────►│ cmr.ts usgs.ts     │
  │ USGS FDSN           │        │ datasets.ts (registry, lazy cache)
  │ Esri tiles (maps)   │        │ references.ts (15-entry registry)
  └─────────────────────┘        └─────────┬──────────┘
                                           ▼
                                  ┌────────────────────┐
                                  │ src/app/api (REST) │
                                  │ /api               │  health/capabilities
                                  │ /api/datasets[/:id]│  registry + provenance
                                  │ /api/analyses[/:id]│  run pipeline / evidence
                                  │ /api/external/*    │  cmr · usgs · browse
                                  │ /api/references    │  registry (+DB mirror)
                                  └─────────┬──────────┘
                                            │ zod-validated params,
                                            │ in-memory result cache,
                                            │ Prisma AnalysisRun audit
                                            ▼
                            ┌─────────────────────────────────┐
                            │ src/lib/science (pure TS core)  │
                            │ validation  (Stage A)           │
                            │ pipeline    (B referencing,     │
                            │              C measurement,     │
                            │              D uncertainty,      │
                            │              E attribution)     │
                            │ phase okada uncertainty         │
                            │ detection statistics fixtures   │
                            │ utm los-projection constants    │
                            └─────────┬───────────────────────┘
                                      │ AnalysisResult / TransportGrid (base64)
                                      ▼
                     ┌────────────────────────────────────┐
                     │ src/app/page.tsx + components/app  │
                     │ decode.ts      b64 → Float32 grids │
                     │ raster-render  Mercator-exact warp │
                     │ map-panel      Leaflet + overlays   │
                     │ results-panel  narrative + badges   │
                     │ evidence-viewer browses · CMR · MT  │
                     │ methodology    drawer + references  │
                     └────────────────────────────────────┘
```

The science core is deliberately free of I/O: USGS events are passed *into*
`runPipeline` by the API layer, so every stage is deterministic and
unit-testable. All heavy rasters live on the filesystem; the database
stores only run metadata.

## Modes

- **quantitative** — the full stages A–E pipeline over phase/coherence
  rasters (real LA sample + synthetic fixtures).
- **evidence** (Panama) — real granule metadata, official browse imagery,
  live CMR/USGS queries and a labelled Okada forward model; no measured
  displacement is claimed because the HDF5 download is auth-gated. The mode
  is explicit in the dataset record, the UI and the narratives.

## Persistence (Prisma + SQLite)

- `AnalysisRun` — one row per run: dataset, status, parameters (JSON),
  summary (JSON), error, duration. Full grids are not persisted: they are
  deterministic functions of dataset + parameters and are recomputed (or
  served from the in-memory cache) on demand.
- `Reference` — mirror of the filesystem registry, seeded on first request
  for audit; `src/lib/data/references.ts` remains authoritative.

## External service handling

- **CMR:** 30 s timeout, open search endpoints, explicit 502 error
  surfacing. `bracketsEvent` is computed server-side.
- **USGS:** measured 15–25 s latencies and intermittent 502s → 35 s
  timeout, one retry, 15-minute response cache, honest degraded messages
  ("cached event record shown").
- **Browse imagery:** served from disk through a path-traversal-guarded
  endpoint with source attribution headers.

## Frontend georeferencing

The transport grids are UTM-projected. The browser renders them into a
canvas whose pixel grid is defined in Web Mercator: each canvas pixel is
inverse-Mercator'd to lat/lon, converted to UTM with the same Snyder series
as the science core, and sampled from the science grid (nearest
neighbour). The rendered image is then placed as an `L.imageOverlay`
rectangle — exact by construction; grid-zone edges are masked, not
distorted. Displacement is coloured with a symmetric terracotta↔teal
divergent scale; a legend, hover value readout, opacity control and layer
switcher (displacement / coherence / uncertainty / coverage — the coverage
layer shows the valid-measurement mask as a flat tone, which is the honest
view for scenes whose displacement is ~zero) complete the map.

## Performance (measured on the dev machine, 4 GB RAM)

| Operation | Time |
|---|---|
| Synthetic fixture pipeline run | 65–90 ms |
| Real LA sample (1.77 M pixels, full stages A–E) | ~360 ms |
| Real Mojave 2026 operational granule (19.1 M pixels, stages A–E) | ~1.5 s + USGS |
| 2.58 GB granule download (12 parallel range parts) | 2 min 16 s |
| Transport grid + base64 (~6.8 MB JSON for the Mojave frame) | included above |
| Okada forward model, 400×400 grid | < 100 ms |
| Okada forward-model consistency screen (strided ≤400k subgrid) | 1–3 s |
| Test suite (42 tests) | ~1.2 s |

USGS live queries dominate wall-clock time for real-dataset analyses
(15–25 s); the UI shows an elapsed timer and explains the wait.

**Memory discipline on a 4 GB host** (all learned from real OOM kills):

- The raw 2.58 GB granule and all derived products live in `data/`
  (git-ignored). Tailwind 4's automatic content scanner respects
  `.gitignore` — before `/data/` was ignored, the scanner tried to read the
  2.58 GB HDF5 during the CSS build and the OOM killer took down the dev
  server (postcss worker died mid-IPC → Turbopack panic). `globals.css`
  additionally pins `@source not "../data"` / `@source not "../tools"` as
  belt-and-braces.
- The analysis result cache is bounded (10 entries; each holds ~10 MB of
  transport rasters) with oldest-first eviction.
- Dataset rasters (~190 MB for the Mojave frame + smaller sets) are loaded
  lazily per request and cached per server process; the page compiles
  before any dataset is loaded in normal usage.

## Reproducibility

- Deterministic fixtures (seeded PRNG) and a pure pipeline: same inputs →
  same outputs, cacheable and re-runnable.
- `bun test tests/` re-executes the entire validation suite in ~1.2 s.
- The extractor (`tools/extract_gunw.py`) re-derives `data/derived/*` from
  the original HDF5 in one command.
