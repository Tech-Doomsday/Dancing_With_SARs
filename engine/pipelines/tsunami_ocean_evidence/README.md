# 3.17 tsunami_ocean_evidence — event-scoped ocean evidence (NEW pipeline)

The app's DART + shallow-water work, ported as one event-scoped pipeline ("did this earthquake
move the ocean?"). 3.14 `tsunami_inundation` stays the SAR coastal-strip pipeline ("did the sea
flood this coastal strip?") — a different question at tile scale; both share
`engine/lib/tsunami/` and `engine/lib/okada.py` and never read each other's verdicts.

## The five files

| file | job |
|---|---|
| `manifest.yaml` | code 3.17, `execution: heavy` (timeout 3600 s), `needs_sar: false`, fetch codes R1/R11/R15/R16 |
| `fetch.py` | providers seam (`run(bundle, providers=None)`) → R1 event (+ FFM.geojson), R15 DART, R16 ETOPO1, R11 catalog; failures are explicit result states |
| `prep.py` | pick the triggering event, build the (capped) ETOPO1 domain, pick DART stations within radius |
| `model.py` | screening → detection (k·σ on de-tided records, eikonal-extended seismic mask) → source (FFM or MT plane + WC94 + Tanioka-Seno + Kajiura) → NSWE simulation → comparisons → 5-level classification |
| `verdict.py` | wires the above into one typed `PipelineResult` (evidence items, provenance per external record, layers, diagnostics) |

## Statuses

`SUPPORTED` (consistent: trigger(s) AND simulation agree within ±15 min / ×3) · `POSSIBLE`
(screening met and/or trigger(s), no sim comparison) · `NOT_SUPPORTED` (coverage shows no
anomalous wave) · `INSUFFICIENT_EVIDENCE` (no usable record / event unknown). `raw_p` is an
uncalibrated within-tier rank score, never a probability.

## Honesty contracts (inherited from the app, enforced here)

* A trigger is a DETECTION of an anomalous wave, not proof of a tsunami.
* Missing evidence is never evidence of absence; the null result is limited to the sensors,
  cadence and noise actually available.
* Buoy agreement is CORRELATED evidence (all stations sample the same wave field).
* "confirmed-official" is a citation the operator attaches — the software never asserts it.
* Excursions within the seismic-phase mask (8 min, extended to ~85 % of the eikonal ray time)
  are caveats, never the arrival.

## Heavy lane

The registry defers this pipeline (`PENDING`, `detail.lane = "heavy"`); `engine/jobs` executes it
with the manifest's own deadline, a per-job progress file (`data/jobs/<job>.json`), cooperative
cancel and a content-addressed result cache (same bundle + params + code ⇒ cached verdict).

## Tests (offline)

`engine/pipelines/tsunami_ocean_evidence/tests/` — heavy-lane deferral, end-to-end with injected
providers (SUPPORTED/POSSIBLE), parameters via triggers, null sensors → NOT_SUPPORTED, no stations
→ INSUFFICIENT_EVIDENCE, degraded event fetch → INSUFFICIENT_EVIDENCE. Solver acceptance tests
live in `engine/lib/tsunami/tests/`.
