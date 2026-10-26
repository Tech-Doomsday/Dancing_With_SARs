# Data sources and provenance

All availability statements below were verified live on **2026-10-20** from
the development environment. Every dataset surfaced in the application
carries a machine-readable provenance record (see `ProvenanceRecord` in
`src/lib/science/types.ts`) with provider, product ID, acquisition times,
processing software, data class (real / synthetic / derived), source URL,
DOI, access notes and quality flags.

## 1. NISAR L2 GUNW — live catalog (NASA CMR)

| Field | Value |
|---|---|
| Collection | `NISAR_L2_GUNW_PROVISIONAL_V1` (also `NISAR_L2_GUNW_BETA_V1`) |
| Provider | NASA ASF DAAC (provider id `ASF`) |
| Content | Geocoded UNWrapped interferograms (L2) — the displacement product |
| Granules at verification | 90,974 (provisional) + 10,217 (beta) |
| Temporal span | 2025-10-29 → 2026-10-18 (12-day repeat pairs at the recent end) |
| Granule size | ~1–2.3 GB (HDF5) |
| Metadata access | Open, no authentication — CMR Search API |
| Science-file access | Requires free NASA Earthdata Login (302 redirect to urs.earthdata.nasa.gov) |
| Browse imagery | Open, no authentication — ASF Earthdata Cloud |

Endpoints used (both open):

- `https://cmr.earthdata.nasa.gov/search/granules.json?short_name=NISAR_L2_GUNW_PROVISIONAL_V1&bounding_box=...&temporal[]=...`
- `https://cmr.earthdata.nasa.gov/search/granules?short_name=...` (HEAD, for hit counts)

The application uses CMR for the availability panel and the
"acquisition pair brackets the event" detection (`bracketsEvent`:
`timeStart < eventTime < timeEnd`). Live queries are proxied through
`/api/external/cmr` with timeouts and explicit error surfacing.

## 2. NISAR operational GUNW granule — Mojave 2026 (quantitatively analysed)

A genuine product of the operational NISAR mission, downloaded with an
authenticated NASA Earthdata account and analysed end-to-end:

| Field | Value |
|---|---|
| Granule | `NISAR_L2_PR_GUNW_021_013_D_071_022_4000_SH_20260522T024038_20260522T024116_20260603T024038_20260603T024115_P05023_N_F_J_001` |
| Source | `https://nisar.asf.earthdatacloud.nasa.gov/NISAR/NISAR_L2_GUNW_PROVISIONAL_V1/…` (ASF Earthdata cloud; bearer-token download, see §9) |
| Local copy | `data/raw/…h5` (2,575,302,656 bytes; parallel ranged download, ~2 min) |
| Collection | NISAR_L2_GUNW_PROVISIONAL_V1 (operational provisional) · DOI 10.5067/NIL2GUNW-P1 |
| Scene | Southern Sierra Nevada / Owens Valley / Death Valley / Mojave Desert; track 13, frame 71, **descending, left-looking**; centre ≈ 34.64°N 117.70°W |
| Pair | Reference 2026-05-22 02:40:38 UTC → Secondary 2026-06-03 02:40:38 UTC (**12 days**, NISAR repeat cycle) |
| Grid | 4401 × 4338, EPSG:32611 (WGS84 / UTM 11N), 80 m posting (~352 × 347 km) |
| Radar | λ = 0.2419632 m (centre frequency 1.239 GHz), incidence 41.05°, heading 198.5° (derived from the product's own LOS unit vectors), 16×13 = 208 looks |
| Processing | NISAR SDS **v0.25.16** (2026-08-10), product spec **v1.5.0**; SNAPHU unwrapping; topographic + ellipsoidal flattening, range + azimuth ionospheric, hydrostatic + wet tropospheric corrections all applied (flags read from the product) |
| L1 inputs | real NISAR L1 PR RSLC granules `…_021_013_D_071_4005_DHDH_A_20260522…` / `…_022_013_D_071_4005_DHDH_A_20260603…` (recorded in provenance) |
| DEM | Copernicus DEM 30 m (2023_1), NISAR DEM v1.2 |

**What the data shows (measured, not assumed).** This pair was acquired
through a severely disturbed ionosphere:

- the product's own ionospheric phase screens reach **tens of radians**
  (screen std ≈ 36 rad; correction-uncertainty median 3.73 rad ≈ 72 mm LOS);
- **coherence collapsed scene-wide** (median 0.08 uniformly across nine
  regions of the frame; only 12.3% of pixels ≥ 0.1, 0.3% ≥ 0.2);
- the stored phase over non-unwrapped (connected-component 0) areas is a
  noise-filled field (std ≈ 5 rad) — the pipeline's Stage B now gates on
  the unwrapping component labels (see docs/methodology.md);
- the single valid unwrapping component (324,103 px ≈ 2,074 km², 99.7%
  exact-zero phase) is a genuinely stable surface: **max |LOS displacement|
  48 mm < the 72 mm ionospheric systematic floor** → no detection, honestly

**Recorded result (2026-10-21):** because the source granule cannot be re-downloaded without
Earthdata credentials, the measured figures above are served as a clearly-labelled recorded
result — `data/derived/nisar-mojave-2026/recorded_run.json` (summary captured from the
2026-10-20 run stored in the analysis-run database; footprint from live CMR; M4.06 event
record ci41478192 re-verified live against USGS on 2026-10-21). The analysis API still
refuses to re-run this dataset (state `missing`, HTTP 409); the UI offers 'View the recorded
result' instead, and every figure is labelled as recorded.

  reported.

A **M4.06 earthquake** occurred inside the frame between the acquisitions
(18 km WSW of Johannesburg, CA, 2026-06-01 22:34 UTC, 7.6 km depth — USGS
`us7000u18k`-series query, matched live by the attribution layer). Its
expected deformation is mm-to-cm scale, below this pair's measurement
floor; the coseismic hypothesis is correctly reported as *insufficient
evidence*, not as a cause.

**Dataset-suggested starting parameters:** coherence threshold 0.1
(the global 0.5 default would reject the scene at Stage B; the suggestion
is visible and adjustable in the UI, with the reasoning in these notes).

Extraction is the same lossless `tools/extract_gunw.py` path as §3;
derived layers live in `data/derived/nisar-mojave-2026/`.

## 3. NISAR sample GUNW product (quantitatively analysed)

The openly distributed NISAR sample product analysed end-to-end:

| Field | Value |
|---|---|
| Granule | `NISAR_L2_PR_GUNW_001_030_A_019_002_2000_SH_20081012T060911_20081012T060925_20081127T061000_20081127T061014_D00404_N_F_J_001` |
| Source | https://nisar.jpl.nasa.gov/data/sample-data/ (open access) |
| Local copy | `data/nisar-sample/…h5` (264 MB, verified MD5-intact download) |
| Sensor | ALOS PALSAR L-band data, processed through the NISAR SDS **v0.24.0** (processing date 2025-01-29) |
| Scene | Los Angeles region (San Gabriel Mountains / Antelope Valley), frame 019, track 030, ascending |
| Pair | Reference 2008-10-12 06:09 UTC → Secondary 2008-11-27 06:10 UTC (46 days) |
| Grid | 1136 × 1555, EPSG:32611 (WGS84 / UTM 11N), 80 m posting |
| Radar | λ = 0.2360571 m (centre frequency 1,269,999,750.06 Hz), incidence 19.3–28.9°, 17×7 = 119 looks |
| DOI | 10.5067/NIL2GUNW-B1 (recorded in-file) |
| Product spec | v1.2.1; unwrapping: SNAPHU; flattening: with geometry offsets |
| Corrections applied upstream | topographic + ellipsoidal flattening, ionospheric (range+azimuth), hydrostatic + wet tropospheric — all read from product metadata and displayed |
| Ionospheric screen uncertainty | robust median 4.31 rad ≈ 81 mm (spatially correlated systematic → detection floor) |

**Extraction** is lossless and reproducible:
`python3 tools/extract_gunw.py <product.h5> <outdir> --slug …` writes
`phase.f32`, `coherence.f32`, `components.u16` plus a complete `meta.json`
(grid, radar geometry derived from the product's own LOS unit vectors,
identification, processing flags, statistics). The derived dataset lives in
`data/derived/nisar-la-2008/`. All scientific interpretation happens in the
TypeScript pipeline; the extractor performs no science.

## 4. Panama M7.7 earthquake evidence (2026)

| Field | Value |
|---|---|
| Event | USGS `us6000u18k`, Mww **M 7.7** |
| Time | 2026-10-09T17:56:06 UTC |
| Location | 7.5868°N, 80.769°W (12 km WSW of Pitaloza Arriba, Panama), depth 12.6 km |
| Moment tensor | strike 25.21°, dip 74.77°, rake −155.3° (dextral-oblique), M0 = 3.77×10²⁰ N·m — `us/us_6000u18k_mww` |
| NISAR coverage | 14 GUNW granules whose acquisition pairs bracket the event (CMR spatial+temporal query, verified 2026-10-20) |
| Primary granule | `NISAR_L2_PR_GUNW_031_141_D_085_032_4000_SH_20260927T235335_…_20261009T235335_…` — 2026-09-27 → 2026-10-09 (second pass ~6 h after the mainshock), footprint contains the epicentre |
| Browse imagery | 3 official SDS LATLON renderings downloaded (open access) to `data/nisar-browse/`, served via `/api/external/browse` with strict filename validation |

Cached records: `data/derived/panama-2026/usgs_event.json` (event + moment
tensor, from the live USGS detail endpoint) and `granules.json` (CMR
records). **Honest limitation, displayed in the UI:** the full HDF5
products require NASA Earthdata credentials, which this deployment does not
hold — so MOTUS presents the real availability, official imagery and a
labelled Okada forward model, and explicitly does not present a measured
displacement field for this event.

## 5. USGS ComCat (FDSN) earthquake catalog

| Field | Value |
|---|---|
| Service | https://earthquake.usgs.gov/fdsnws/event/1/ (open, no auth) |
| Use | Independent seismic evidence for attribution; moment-tensor nodal planes for forward modelling |
| Observed latency | 15–25 s per time-range query under load; intermittent 502s (measured 2026-10-20) |
| Mitigation | 35 s timeout, one retry, 15-minute in-memory response cache; honest degradation message on failure |

The client parses each event's `products["moment-tensor"]` for nodal-plane-1
strike/dip/rake and the event magnitude is taken from the `mww`-derived
property. Events are attributed to an analysis only when their origin time
falls strictly inside the acquisition window (reference → secondary) and
within 400 km of the dataset centre.

## 6. Synthetic validation fixtures

Generated in-process by `src/lib/science/fixtures.ts` from the Okada85
forward model with an explicit observation-simulation chain (LOS projection
→ phase conversion → coherence-consistent Gaussian noise → smooth
atmospheric ramp → decorrelation patches). Deterministic (mulberry32
seeds). Three fixtures: dextral strike-slip (seed 20261009), thrust
(seed 88001), null (seed 424242). Every fixture is labelled `dataClass:
synthetic` in provenance, badged in the UI, and excluded from any claim
about the real Earth.

## 7. Map tiles

Base map tiles: Esri World_Topo via the ArcGIS Online tile service
(keyless, clean at time of verification). CARTO basemaps were rejected
during development because they now serve "API KEY REQUIRED" watermarked
tiles without a key; this was caught by visual inspection of the rendered
map, not assumed.

## 8. Provenance model

Every dataset loaded by the registry produces a `ProvenanceRecord`:

```
provider, productId, productType, platform,
referenceTime, secondaryTime, temporalBaselineDays,
processingTime?, processingSoftware?,
dataClass ('real' | 'synthetic' | 'derived'),
sourceUrl, doi?, accessNotes?, qualityNotes[]
```

These fields are surfaced in the UI (dataset panel + expandable full
provenance block), returned by `GET /api/datasets/[id]`, and the quality
flags are copied verbatim from the product metadata (corrections applied,
no-data fractions, multilooking). Run-level provenance (parameters,
summaries, durations) is persisted in the `AnalysisRun` table for audit.

## 9. Access policy and credentials

The granule of §2 was downloaded with a real Earthdata bearer token
(uid `ujayer`) on 2026-10-20. Verified auth flow against the live service:

1. `GET …/<granule>.h5` with `Authorization: Bearer <token>` → **303** to a
   short-lived (~3 h) presigned CloudFront/S3 URL (an `asf-urs` session
   cookie is also set);
2. the presigned URL must be fetched **without** the Authorization header
   (S3 rejects dual auth mechanisms);
3. observed: the bearer token succeeded for exactly one presign exchange —
   subsequent attempts returned `401 invalid_token`; the presigned URL and
   session cookie remained valid. Treat the token as single-use for
   presigning and keep the URL/cookie for the actual transfer;
4. the 2.58 GB file was downloaded as 12 parallel HTTP-range parts
   (`tools/parallel_fetch.sh`, ~19 MB/s, ~2 min 16 s) — a single stream
   throttles to ~2 MB/s.

Browse PNGs (`<granule>_LATLON.png`) were anonymously accessible earlier
on 2026-10-20 but now redirect to Earthdata Login — treat browse access as
authenticated. No credentials exist in this repository: the token lives in
`.env` (git-ignored) and `.env.example` documents the variable.
`tools/fetch_nisar_granule.py` implements the documented flow.
