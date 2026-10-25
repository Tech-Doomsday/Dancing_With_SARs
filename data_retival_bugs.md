# Data retrieval — bugs and gaps found while building `engine/common/` (task 2)

**Nothing under `engine/retrieval/` was modified by `engine/common/`.** (`git status` shows no change there.)
Last re-verified on commit `7432468` (2026-10-25), Linux, Python 3.12, h5py 3.16, in a sandbox without a Windows host. Everything below is something
`engine/common/` had to work *around* or *assume*, so the people who own retrieval can decide what to do.
Items are ordered by how much they can hurt the pipelines. "Workaround" says what `engine/common` does today.

Legend: **BUG** = behaves differently from what its own test/docstring says. **GAP** = missing information that downstream
code needs. **RISK** = works today, easy to break.

---

## 1. BUG — `tests/test_retrieval.py::test_chunk_tables_load_lazily_per_layer` fails on a clean checkout

```
>       assert c2.counters["index_built"] == 0 and c2.counters["index_tables_layers"] == 0
E       assert (0 == 0 and 2 == 0)
```

This test fails before any `engine/common` code exists (`pytest tests/test_retrieval.py -k chunk_tables_load_lazily`).
All other retrieval tests pass.

What the test expects: a second client process re-uses the *persisted partial index* (written after fetching two layers)
and therefore loads **zero** chunk tables.

What happens: the second client reloads the 2 layer tables again. Diagnosis from reading `nisar_client.py`:

* `_catalog(g)` (line ~288) looks in `cfg.catalog_dirs` and **the shipped catalog `config/catalog/GUNW.json` wins** over
  the catalog the first client just learned from the synthetic granule (`_save_catalog`, line ~301).
* With a catalog present, `catalog_missing(ix, cat, pat)` (line ~259) is true for the persisted partial index, so
  `_extend_index` (line ~341) runs, and it rebuilds the index from the catalog **without keeping the chunk tables that the
  persisted index already had** → `index_tables_layers` goes 0 → 2.

So either the test's expectation is wrong for the shipped-catalog case, or `_extend_index` should keep already-loaded
tables. Impact: every cold process re-reads chunk tables it already paid for (extra range requests per granule), nothing
incorrect in the returned data.

Workaround: none needed (data is correct). I did not touch the test or the client.

**Re-verified on `7432468`: still fails** (1 failed, 37 passed, 1 skipped when `tests/test_regressions.py` is excluded; see items 9-10).

## 2. GAP — clips carry no per-layer fill value or scale/offset

`h5_index.LayerLayout.fill` knows each dataset's HDF5 `fillvalue`, but `GridClip` (`clients/types.py`) exposes only
`grid, epsg, x, y, extras, layers{key: raw array}`. The `_FillValue` / `valid_min` attributes, `scale_factor`,
`add_offset` and `units` are not delivered. Integer layers (`connectedComponents`, `mask`, GCOV `mask`) therefore arrive
with their fill as a bare dtype maximum and the consumer has to guess.

Workaround (`p03_decode_gate.py`, `layers.integer_fill`): float fill = NaN; unsigned integer fill = dtype max (255 / 65535);
signed = dtype min. If a real product uses another sentinel (e.g. `connectedComponents` fill 65535 *and* 0 for "no
component", or a float `-9999`) it is silently treated as data. `decode.float_sentinels` in `config/common.yaml` lets
an operator add float sentinels, but nothing detects the problem.

**Checked against a real product (2026-10-25, ALOS-1 pair processed by the NISAR SDS, spec 1.2.1):** the workaround is right for
this product. The spec gives `connectedComponents` `_FillValue 65535`, `mask` `_FillValue 255` and float layers `_FillValue nan`.
The real data also holds a genuine component `0` ("no component", 69,103 px in the whole scene, not fill) next to 720,851 fill pixels;
P03 drops it because `decode.cc_min = 1`. So the gap is still real (a different sentinel elsewhere would pass silently), but nothing
is wrong today.

Suggested fix: return `{layer_key: {"fill": ..., "units": ..., "scale": ..., "offset": ...}}` next to `layers`.

## 3. GAP — no radar / processing metadata in the clip

The clip contains only the requested datasets. It does not carry `centerFrequency` / `wavelength`,
`slantRangeSpacing`, `numberOfLooks` for GUNW, the unwrapping method, which corrections the product generator already
applied to `unwrappedPhase` (troposphere, ionosphere, solid-earth tide), `lookDirection`, `orbitPassDirection`,
`referenceZeroDopplerStartTime` / `secondaryZeroDopplerStartTime`, or product version/processing level.

Impact: these are *physics inputs* of P06/P07/P05, not niceties.

Workaround — each is a config default **and is written into every bundle's `provenance.assumptions`**, never silent:

| Assumed value | Config key | Risk if wrong |
|---|---|---|
| wavelength 0.2384 m | `radar.wavelength_m` | all LOS displacements scale by the ratio (≈1 % per 2.4 mm of λ). **Real product: 0.23606 m (1 % lower)**, because the sample is ALOS-1 L-band; the right value cannot come from the clip |
| `toward-sensor = -λ/4π · phase` (ref earlier than sec) | `radar.phase_sign` | **sign of every displacement flips**. Consistent with ATBD JPL D-95677 (φ = 4π/λ·(R_sec−R_ref)); not tested against a known ground displacement |
| LOS vector points ground→sensor | `radar.los_vector_points` | wrong E/N of the LOS. **Confirmed**: spec says "from target to sensor" and the stored (−0.4005, −0.0837) matches the geometry (−0.4008, −0.0838) |
| 100 effective looks per 80 m pixel | `radar.gunw_effective_looks` | sigma is off by √(L/100). **Real product: 119** (17 az × 7 rg), so the default understates sigma by ×1.09 |
| no correction already applied | `radar.corrections_applied_by_product` | P07's "residual" would double-subtract. Consistent with the ASF guide (screens supplied, not applied) |
| pair dates parsed from the granule id | — | see item 6 |
| look side (new, optional) | `radar.look_side` | see the correction below |

**Correction to an earlier version of this file.** It said P05 raises `los_convention_suspect` when the incidence layer disagrees
with the LOS vector. That check compares `incidence` with `arccos(Up)` where `Up = +sqrt(1-x²-y²)`; flipping the sign of (x, y)
leaves it unchanged, so it could not detect a wrong `los_vector_points`. P05 now also derives the look side from the LOS vector and
the along-track vector (always reported as `look_side_implied`) and, when `radar.look_side` is set, raises
`los_convention_suspect` on a mismatch. On the real product the correct setting is silent and the flipped one is caught.
**`lookDirection` is in `identification/` but not in the clip, so in a live run nobody can set `radar.look_side` automatically.**
This makes `lookDirection` the most useful single attribute to add to the clip.

Suggested fix: add an `attrs` dict per grid / per granule to `GranuleClip` (identification group + the few scalar
datasets above).

## 4. GAP — `engine/retrieval/sources/` and `clients/adapters/` are empty, so there is no ancillary data

`data/ancillary/` is never written by anything, therefore DEM, ESA WorldCover, JRC water occurrence, tree cover,
built-up, permafrost, glaciers, river network, shoreline distance and volcano list (S1…S13) do not exist in practice.
Without them only P01–P08, P10 and a few P12 rules can say anything; the rest of the routing is `unknown`.

Workaround: `engine/common/ancillary.py` defines a 2-method `AncillaryProvider` protocol and a reference
`DirectoryAncillary` (`<root>/<name>/*.npz` or GeoTIFF + `facts.json`). Nothing is fabricated: a missing layer is reported
in `bundle.ancillary.status` and as an `ancillary_missing` flag. The measurement-code meanings (M1…M7) were inferred from
the pipeline manifests because `sources/__init__.py` is a one-line comment.

## 5. RISK — stale/odd listings are only visible through a boolean

`NisarClient.search()` returns `(refs, degraded)`. `degraded=True` is surfaced (`search_degraded`, bundle `partial`), but a
listing that is merely *incomplete* (ASF paging cut-off, `max_results`) is indistinguishable from a complete one.
`engine/common` cannot tell "no pair exists" from "ASF returned the first 100".

## 6. RISK — pair dates exist only inside the granule id

`GranuleRef.start/stop` describe one acquisition; for GUNW the reference and secondary dates have to be parsed out of the
id (4 timestamps). `p01_granule_resolution.parse_granule_id` does this with a regex that was only checked against ids
written from the NISAR naming convention in the shipped catalogs; real ids were not available offline. (Update: it parsed one real id correctly on 2026-10-25, and the dates match the product's
`identification` group and `temporalBaselineDays`; one id is not a survey.) If the pattern
differs the pair is kept with `times_resolved=False` and a `unresolved_pair_dates` flag (it can still be used for
coverage but not for network / temporal logic).

## 7. RISK — `fetch_tile` call signature drift would be silent for a duck-typed retriever

`engine/common` calls `search(bbox, t0, t1, product=...)` and `fetch_tile(tile, t0, t1, product=, layers=, granules=,
margin_m=)` and nothing else. The tests drive it with a fake that mirrors the signature, plus one test against the real
`NisarClient`. A rename in retrieval would show up as `fetch_failed` flags, not an import error.

## 8. Minor

* `tests/conftest.py` leaves aiohttp sessions unclosed at interpreter exit ("Unclosed client session" printed after the
  run). Harmless.
* `engine/pipelines/_template/manifest.yaml` is not valid YAML (`method:[]`, no space). Not retrieval, but the registry
  skips `_`-prefixed folders for exactly that reason; it should be fixed by whoever owns the template.

## 9. BUG — `range_io.resolve()` has no retry; two regression tests describe retry logic that is not in the code

`tests/test_regressions.py::test_transient_503_on_the_resolve_probe_is_retried` and
`::test_resolve_gives_up_after_the_retry_budget_with_a_clear_error` fail deterministically on `7432468` and on `4bed88d`:

```
RangeError: HTTP 503 resolving http://127.0.0.1:<port>/data/NISAR_...h5      (test expects the call to be retried)
assert ... "gave up resolving" in r.errors[...]                              (test expects this wording)
```

`RangeClient.resolve()` (`engine/retrieval/clients/range_io.py`, ~line 171) issues the probe once and raises
`RangeError(f"HTTP {r.status} resolving ...")` for anything but 200/206/3xx/401/403, so one 429/5xx fails the granule. Only
`_get()` (the range reads) retries. The string `gave up resolving` appears in no commit of the code, only in the tests that
arrived with `4bed88d`: the tests are ahead of the implementation. Not platform-dependent.

## 10. BUG or ENVIRONMENT — two more regression tests fail or hang in my environment

* `::test_index_build_survives_h5py_objects_being_finalised_on_the_loop_thread` runs for its full 90 s join and fails
  ("fetch deadlocked").
* `::test_close_releases_loops_and_fallback_handles` did not finish within 100 s. This is the test for the `LoopThread.close()` /
  `NisarClient.close()` change in `7432468`.

I could not tell whether this is the code or the platform (Linux, Python 3.12, h5py 3.16 here; the removed `sys.platform != "win32"`
branch suggests the author develops on Windows). Practical effect: a plain `pytest` never completes here, so CI would hang on
these two tests. Please run them on your machine and in CI before trusting the close/GC fixes.

