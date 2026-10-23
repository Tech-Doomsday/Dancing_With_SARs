# nisar-hazard-engine

Global SAR (NISAR primary, Sentinel-1 fallback) hazard detection engine: retrieval → common preprocessing → 16 independent, parallel hazard pipelines → verdict → serving/UI, with validation and cross-cutting support.

Full design rationale, the current data-reality check, and the pipeline-by-pipeline open-source mapping live in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). The diagram below is the same shape as `SARs_full_workflow.html`.

## Workflow

```mermaid
flowchart TD
    subgraph RET["1 · DATA RETRIEVAL — engine/retrieval/"]
        direction TB
        SCHED["1.2 Scheduler & Triggers<br/>granule_poller · event_pollers · trigger rules"]
        CLIENTS["1.3 Clients<br/>nisar_client · adapters/ · cache policy"]
        SOURCES["1.1 Sources<br/>M1–M7 measurements · S1–S13 ancillary · R1–R14 reference"]
        STORAGE["1.4 Storage<br/>clips/ · ancillary/ · reference/ · bundles/ · verdicts.db"]
        SCHED -->|decides what / when to fetch| CLIENTS
        CLIENTS <-->|pulls / polled| SOURCES
        CLIENTS -->|writes| STORAGE
    end

    RET -->|clips → bundles| PRE["2 · COMMON PREPROCESSING<br/>engine/common/ — P01…P13, sequential"]
    PRE --> BUNDLE[("SceneBundle<br/>read-only, shared by all pipelines")]

    BUNDLE --> PIPES
    REG["7 · Cross-cutting registry<br/>runs applicable pipelines in a process pool"] -.-> PIPES

    subgraph PIPES["3 · ALL PIPELINES — engine/pipelines/&lt;name&gt;/ (16, independent, parallel)"]
        direction LR
        p01["3.01 earthquake_coseismic"]
        p02["3.02 land_subsidence"]
        p03["3.03 volcanic_deformation"]
        p04["3.04 landslide"]
        p05["3.05 glacier_ice_flow"]
        p06["3.06 permafrost_thaw"]
        p07["3.07 flood_inundation"]
        p08["3.08 deforestation"]
        p09["3.09 wildfire_burn_scar"]
        p10["3.10 river_erosion"]
        p11["3.11 coastal_erosion"]
        p12["3.12 urban_damage"]
        p13["3.13 tropical_cyclone"]
        p14["3.14 tsunami_inundation"]
        p15["3.15 oil_spill"]
        p16["3.16 iceberg_calving_drift"]
    end

    PIPES -->|pipeline, tile, epoch, raw P, status, evidence| VERDICT["4 · VERDICT<br/>engine/verdict/<br/>4.1 collect → 4.2 calibrate → 4.3 link → 4.4 select → 4.5 persist"]
    VERDICT --> SERVE["5 · SERVING & UI<br/>engine/api/ + app/<br/>/tiles · /events · /scanner/status"]
    VALID["6 · VALIDATION & CALIBRATION<br/>engine/validation/<br/>labels, metrics, fixtures"] -.labels fit calibration.-> VERDICT
```

**Rules the diagram doesn't show but the folder structure enforces:** no pipeline ever reads another pipeline's verdict — shared logic only goes through `engine/lib/` (see `tests/test_pipeline_independence.py`). "Not applicable" is a distinct, valid result from "no event found."

### Common preprocessing chain (P01 → P13)

```mermaid
flowchart LR
    P01["P01<br/>granule<br/>resolution"] --> P02["P02<br/>clip"] --> P03["P03<br/>decode<br/>& gate"] --> P04["P04<br/>grid<br/>harmonization"] --> P05["P05<br/>geometry"] --> P06["P06<br/>phase<br/>to LOS"] --> P07["P07<br/>correction<br/>audit"] --> P08["P08<br/>backscatter<br/>prep"] --> P09["P09<br/>ancillary<br/>align"] --> P10["P10<br/>temporal<br/>stack"] --> P11["P11<br/>stable<br/>mask"] --> P12["P12<br/>tile quality<br/>+ routing"] --> P13["P13<br/>provenance"] --> SB[("SceneBundle")]
```

## Repo layout

Full tree with every file's purpose: [`docs/ARCHITECTURE.md` §3](docs/ARCHITECTURE.md#3-repository-layout). Top level:

```
engine/retrieval/   engine/common/   engine/lib/   engine/pipelines/*
engine/verdict/      engine/validation/   engine/api/   engine/registry.py
app/   data/   config/   docs/ARCHITECTURE.md
```
