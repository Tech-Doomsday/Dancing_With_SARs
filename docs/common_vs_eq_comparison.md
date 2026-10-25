# engine/common vs. real data: what `dancing-with-sars-eq` and `nisar-data_analysis` showed

Checked 2026-10-25 against `Dancing_With_SARs@7432468`, `dancing-with-sars-eq` and `nisar-data_analysis` (read-only clones).
Nothing in those repos was changed.

## What each repo is, for this purpose

| Repo | Useful for | Not useful for |
|---|---|---|
| `dancing-with-sars-eq` | One real extracted NISAR-format GUNW (`data/derived/nisar-la-2008/`: phase, coherence, connected components, `meta.json`), the product spec and ATBD as text (`data/nisar-sample/gunspect.txt`, `atbd.txt`), verified conventions in `docs/science.md` | The `.h5` itself: `data/nisar-sample/*.h5` is a 134-byte Git-LFS pointer (264 MB real file), so the HDF5 reading path cannot be exercised offline |
| `nisar-data_analysis` | The *look* of a data browser: one canvas card per layer, colormap, p2/p98 stretch, valid %, fill/units, remote-read log | Data: it needs Earthdata login and live ASF access; `lunar-workspace.zip` is an unrelated game project |
| `Dancing_With_SARs/tools/debug_view.py` (yours) | Retrieval state: granules seen, MGRS tile queue, poll history, cache size | It does not show array contents; it is the retrieval side of the same story |

## The debug layer (`engine/common/debug/`)

```
python -m engine.common.debug --sample ../dancing-with-sars-eq/data/derived/nisar-la-2008 --out bundle_debug.html
python -m engine.common.debug --list-tiles          # every usable 10 km tile and its valid-phase fraction
python -m engine.common.debug --mode best|mixed|worst   |   --lonlat LON LAT
```

* `eq_sample.py` serves the real rasters through `search()` / `fetch_tile()`, the only two calls `engine/common` makes on a retriever.
  It cannot invent what the extraction lacks: no iono/tropo screens, no offsets, flat radarGrid from scalar metadata.
* `trace.py` records, after each of P01..P13, which arrays appeared or changed. It works through an optional `observer`
  argument on `run_steps` / `build_scene_bundle` (default `None`; the bundle id is identical with and without it, tested).
* `compare.py` puts each engine assumption next to the real product's value.
* `report.py` + `report.html` write one self-contained file, no server and no network.

The 10 km tile is small (125 px at 80 m), so the report also draws the whole product with the tile outlined. The default
`mixed` tile straddles the edge of the footprint: about 38 % of its raw pixels are fill (mostly beyond the footprint edge), 5 % are in
component 0, and the rest are kept.

## Findings

| Topic | Engine | Real product | Verdict |
|---|---|---|---|
| wavelength | 0.2384 m | 0.2360571 m (ALOS-1, 1.27 GHz) | defaults differ by 1 %; the clip carries no frequency |
| effective looks | 100 | 119 (17 × 7) | sigma ×1.09 too small with the default |
| LOS vector | ground→sensor | spec: "from target to sensor"; stored (−0.4005, −0.0837) vs computed (−0.4008, −0.0838) | confirmed |
| phase sign | toward-sensor = −λφ/4π | ATBD φ = 4π/λ·(R_sec − R_ref) | consistent, not tested against known motion |
| connectedComponents | fill 65535, keep ≥ 1 | `_FillValue 65535`, plus a real class 0 | confirmed |
| float fill | NaN | `_FillValue nan` | confirmed |
| corrections in phase | none applied | screens supplied, not applied | consistent; the `*CorrectionApplied: True` metadata flags are geocoding flags and must not be read as phase corrections |
| coherence | test scenes 0.8 | mean 0.41; about a quarter of valid pixels reach 0.5 | `stable.coherence_min = 0.5` was never stressed by the tests |
| P01 id parsing | regex written from the naming convention | first real id parsed; dates match `identification` | fine on one id |

### A bug in `engine/common` this exposed (fixed)

P05's `consistency_deg` only checks the vector's length, so it could not detect a wrong `radar.los_vector_points`. P05 now
derives the look side from the LOS and along-track vectors and, when `radar.look_side` is configured, flags a mismatch. Tested on
the real product (correct setting silent, flipped setting → `los_convention_suspect`) and on synthetic scenes.
The along-track direction is a second unverified sign ("along ground track" in the spec), so flipping both at once is invisible.

### A likely sign problem in `dancing-with-sars-eq` (not changed; please check)

`src/lib/science/los-projection.ts` describes `losUnitVector()` as the ground→satellite vector, using look azimuth
`heading + 90°` for a right-looking radar, and says the product stores the satellite→ground direction. Numerically, for this
product (heading −11.8°, incidence 24.2°, right-looking):

* azimuth `heading − 90°` gives (−0.4008, −0.0838), the stored values, and is the ground→sensor vector (the sensor is west of the target);
* azimuth `heading + 90°` gives (+0.4008, +0.0838), which is what `losUnitVector()` returns, and is sensor→ground.

The official spec text says the stored vector is "from target to sensor". `projectDisplacementToLOS()` then treats the returned vector
as ground→sensor (`-(u · los)`), so for the East and North components its sign looks inverted relative to its own stated convention
(positive = away from the satellite); the Up component is unaffected. The unit test compares against the *negated* stored vector, so
it passes either way. I read the code and did not run it, so treat this as a strong lead, not a verdict.

## What was not possible

* The HDF5 layout / `NisarClient` path on a real file (Git-LFS pointer only).
* Any check of the phase sign against a known ground displacement (the sample has no known deformation).
* The ionosphere, troposphere, pixelOffsets and mask layers (not in the extracted rasters).
