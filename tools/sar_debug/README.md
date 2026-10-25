# sar_debug

Lives in `tools/sar_debug/`; run every command below from `tools/` (`cd tools && python -m sar_debug --demo ...`).
It is the file / retrieval-level counterpart of `engine/common/debug/` (which reports on a built SceneBundle).

A standalone debug layer for NISAR retrieval and analysis. It produces **one self-contained HTML report**
that shows everything that was retrieved, how it was retrieved, and whether it looks sane.

It is **independent of the hazard engine**: no module imports `engine.*` (enforced by `--selftest`), and the
optional engine view only *reads files* from a `Dancing_With_SARs` checkout (it parses Python with `ast`, never imports or runs it).

## Use

```bash
# no network, no login: synthetic data, shows every section
python -m sar_debug --demo --out demo_report.html

# live: search ASF, open the newest granule, read only your box
python -m sar_debug --bbox -118.5 33.9 -118.0 34.2 --level GUNW --out report.html

# reuse a search you already saved with nisar_region.py / nisar_fast.py
python -m sar_debug --csv nisar_results.csv --bbox -118.5 33.9 -118.0 34.2 --open 2

# one file (local or remote), with a link-speed probe for remote
python -m sar_debug --file granule.h5 --bbox ...
python -m sar_debug --url https://.../granule.h5 --bbox ... --probe

# clips from nisar_region.py / nisar_fast.py
python -m sar_debug --npz ./nisar_clips/*.npz

# what the engine has produced on disk (layout, stubs, independence, data/, verdicts.db)
python -m sar_debug --engine ./Dancing_With_SARs

python -m sar_debug --selftest
```

Add `--show` to open the report in a browser. Credentials come from `EARTHDATA_USERNAME` / `EARTHDATA_PASSWORD`
(environment or a `.env`), same as `nisar_region.py`. Searching needs no login; opening remote files does.

Install what you need: `pip install numpy h5py pyproj asf_search earthaccess fsspec aiohttp python-dotenv pyyaml`.
Only `numpy` is required; every other library degrades to a clear message in the Health section.

## What the report contains

| Section | What it shows |
|---|---|
| Health | every check, worst first (fail / warn / ok / info) |
| Retrieval trace | waterfall of every search, login, file open and byte-range request with timing and MB; optional link probe (1/2/4 parallel connections) |
| Search results | timeline of products per track/frame/direction, footprints, baseline histogram, pair-network connectivity, stack-depth readiness against the rules in `docs/ARCHITECTURE.md` |
| File anatomy | grids (size, spacing, EPSG, lon/lat extent), gridded layers (shape, dtype, chunks, compression), key parameters, full tree |
| Layers | one card per raster: map with hover readout (pixel value and lon/lat), colour scale, histogram, statistics, integrity warnings |
| Cross-layer checks | component gate vs phase, wrapped vs unwrapped consistency, grid agreement |
| Engine view | folder layout, pipeline files and manifests, P01..P13 chain, verdict steps, pipeline-independence violations, data folders, clip contents, `verdicts.db` tables |

## Honesty notes

* Warnings marked **heuristic** (phase jumps > pi, valid fraction, plane fit, "stub?") are debug aids, not acceptance criteria.
* Ionosphere, troposphere and tide layers are reported as *present in the file*; the report does not claim they were applied.
* Stack-depth readiness only encodes rules actually written in `docs/ARCHITECTURE.md`; it invents none for the other pipelines.
* The remote HDF5 path (`--url`, live search hits) and the real-`h5py` path are written against the access pattern used in
  `nisar-data_analysis` but were **not run against live ASF servers or real NISAR files**. The demo and self-test exercise the same
  code through an in-memory stand-in. Check the first real run.

## Layout

```
sar_debug/
  __main__.py     CLI + orchestration + --selftest
  retrieve.py     flight recorder, ASF search/CSV, local/remote HDF5 open, window math, layer reads, npz clips, link probe
  anatomy.py      HDF5 walk: tree, parameters, rasters, grids, derived facts
  analyze.py      search analysis, readiness rules, layer statistics, cross-layer checks
  engine_view.py  read-only look at a Dancing_With_SARs checkout
  report.py       HTML sections          render.py  PNG/SVG/colormaps/page shell
  geo.py          lon/lat <-> projected (pyproj, or built-in UTM)
  synthetic.py    demo data, in-memory h5py stand-in, fake engine checkout
```

To add a kind of layer, add a row to `anatomy.CATEGORIES`. To add a readiness rule, add a row to `analyze.READINESS_RULES`.
