# earthquake_coseismic port — overlay (work in progress, NOT yet fully tested)

Copy this tree over the repo root (it only adds/overwrites the listed files), then
`pip install -r requirements-retrieval.txt -r requirements-engine.txt`.

## Verified (final state of this overlay)
* Pipeline suite `engine/pipelines/earthquake_coseismic/tests`: 99 tests. A full run gave 98 passed / 1 failed;
  the one failure (`test_eq_params_override_via_triggers`, expectation made obsolete by NO_SIGNAL) was fixed
  and re-run together with the other fast pipeline tests (7 passed). The ~40 s optimizer tests were not
  re-run after that one-line assertion change (they do not depend on it).
* Engine suites (independence, bundle contract, common truth/chain/foundation, debug hub): 186 passed.
* Okada vectorised-grid bug fixed (`_ub_vec` used an undefined `q2` on exactly-vertical faults);
  regression test covers dips 90, 90-1e-12, 89.999, 60, 5 for all three dislocation types.
* Referencing: default `tileMedian` (robust median over all valid tile pixels; no reference-scatter
  inflation of sigma). With the plan's 12 % far-field margin the margin held the source's own field
  (scatter 22.6 mm -> threshold 68 mm) and blinded detection of a 47 mm signal; the end-to-end
  uncatalogued test now guards this. `farField` / `fullFrameMedian` stay selectable.
* `raw_p` = Pearson r of the better nodal plane (clipped 0..1; None when no model screened) — decision 4.
  An uncalibrated rank score, never a probability.
* Statuses: `NO_SIGNAL` (no event + no cluster; added to manifest), `NOT_MODELLED` (event qualifies but
  cannot be modelled). Metamorphic LOS-sign test passes through the real chain (toward -> SUPPORTED,
  away -> not SUPPORTED, away correlation < 0.25).
* Uncatalogued fit: synthetic vertical strike-slip truths, 7 of 8 strikes recovered within 500 m / 8 deg
  (+ the unit-test scene at 96 m). One miss (strike 98 deg, 1.85 km). The residual ratio does NOT separate
  right from wrong basins, so a "converged" candidate's parameters are a best fit, not unique; depth, width
  and slip are weakly constrained. ~40 s per fit on one core.

## Not done
* `bundle.references` / central reference retrieval (decision 6, confirmed) — the pipeline still calls its
  own `fetch.py` seam for R1/R2. This needs a SceneBundle contract change and is the next structural step.
* Docs: plan section 8.6 still describes the original fit; deviations (fitted strike, stress-drop cap,
  analytic slip, location bounded around the centroid, strike scan + least-squares refinement) and the
  referencing change are not yet written into docs/IMPLEMENTATION_PLAN.md.
* docs/common_vs_eq_comparison.md (needs the real LA sample, not provided).
* M3-M5 (live adapter tests, API integration tests, UI parity/Playwright).
* Engine hygiene found while checking the second plan: engine.common._raster/_runner/state fail to import
  (`PreprocConfig` missing); pyproject.toml dependencies = [].
