# Limitations

Each limitation is listed with its severity and what would resolve it.
Nothing in this list is hidden in the interface — the UI surfaces the
relevant items where they matter.

## Scientific

0. **Measurements are apparent relative LOS range change — atmospheric and
   ionospheric contributions are NOT removed from the consumed phase.**
   Per the official ASF NISAR GUNW guide (retrieved 2026-10-21), the
   ionospheric phase screen is supplied but not applied, and tropospheric
   corrections are supplied as lookup tables and not removed from the
   interferometric data. MOTUS quantifies the unresolved ionospheric
   systematic as a detection floor (screen variation vs. screen uncertainty,
   whichever is larger) and labels every measurement accordingly — but it
   does not (and, without applying the supplied corrections itself, cannot)
   produce atmosphere-free displacement. *Resolve:* apply the supplied
   screens/LUTs with verified units and sign conventions, or ingest
   externally corrected products.

1. **Single interferogram per analysis.** No time series, no stacking, no
   averaging of atmospheric noise across pairs. *Consequence:* slow
   processes cannot be separated from single events, and the reliability
   narrative says so. *Resolve:* ingest and chain multiple GUNW pairs per
   frame (the catalog supports it).

2. **LOS-only measurement.** One viewing geometry cannot decompose
   vertical from horizontal motion. *Resolve:* pair an ascending and a
   descending frame (available in the catalog) and solve the 2-geometry
   decomposition.

3. **Attribution is a rule-based screen, not a calibrated classifier.**
   No precision/recall or probability calibration is claimed anywhere; the
   screening values (amplitude-ratio factor, correlation floor, atmospheric
   plausibility value) are exploratory conventions attached to every result.
   *Resolve:* a labelled validation corpus (events with independently
   modelled deformation) would be required before any statistical claim.

4. **Forward-model amplitude precision is order-of-magnitude.** The
   Wells & Coppersmith (1994) area scaling scatters by ~0.25 decades and a
   2:1 aspect is assumed. *Resolve:* published finite-fault models where they
   exist.

5. **Coherence-based σ underestimates below γ ≈ 0.3.** Documented in the
   uncertainty module; masked pixels keep usage inside the valid range.

6. **Far-field referencing assumption.** If deformation covers the entire
   frame, the reference region absorbs part of the signal. Warned in-app
   when the reference region is small.

7. **No independent geodetic cross-validation ingested.** The LA 2008
   result has not been compared with GNSS or published InSAR studies.
   Documented as the top validation gap.

## Data access

8. **Panama quantitative processing blocked by credentials.** NISAR HDF5
   downloads require a free NASA Earthdata Login; this deployment holds no
   credentials. Evidence mode (real metadata, official browse imagery,
   labelled forward model) is the honest fallback, and the documented
   authenticated path (`EARTHDATA_BEARER_TOKEN`) is unexercised.
   *Resolve:* configure credentials and download the ~1–2 GB granules.

9. **Official challenge page unreachable from the development
   environment** (Cloudflare block). The interpretation of "Dancing with
   the SARs" is based on the provided brief plus official NISAR/ASF
   documentation, and this limitation is recorded rather than papered
   over.

10. **Map tiles are third-party.** Esri World_Topo was selected after
    CARTO began serving watermarked tiles without API keys; availability
    is outside our control. The map remains functional (overlays,
    markers, footprints) even if tiles fail.

## Engineering

11. **Sandbox constraints.** Development-mode only (`next dev`); no
    production build is exercised. TypeScript build errors are suppressed
    by the scaffold's `ignoreBuildErrors` (lint and the test suite are the
    enforced gates).

12. **No automated UI tests.** The interface was verified interactively
    (both analysis modes, layer switching, live CMR, responsiveness,
    footer behaviour) but those checks are not repeatable artifacts.

13. **Geodesy tolerances.** UTM (Snyder series) accuracy is ~metre level —
    far below the 80 m product posting, but not geodetic-grade.

14. **Single-process in-memory caches** (results, USGS responses) —
    per-instance, not shared; a restart recomputes (deterministically).

15. **Ionospheric floor on the 2008 sample.** The analysed product's
    81 mm systematic floor bounds small-signal detection on that pair; the
    recent operational 12-day products carry smaller screens, but none was
    downloadable without credentials at verification time.
