# References

Citation policy: a source link must support the associated claim. No
citation in this project is decorative; each entry below lists exactly what
it supports in MOTUS. This registry is machine-readable at
`src/lib/data/references.ts` and served at `GET /api/references`. All links
verified reachable on **2026-10-20**.

## Foundational methods

### Okada 1985 — the elastic dislocation model
Y. Okada, *Surface deformation due to shear and tensile faults in a
half-space*, Bulletin of the Seismological Society of America 75(4):1135–1154,
1985. [doi:10.1785/BSSA0750041135](https://doi.org/10.1785/BSSA0750041135)

Supports: the analytic elastic half-space dislocation model used for the
synthetic validation fixtures and the earthquake forward-model consistency
screen.

### Okada 1992 — the DC3D reference code
Y. Okada, *Internal deformation due to shear and tensile faults in a
half-space*, Bulletin of the Seismological Society of America 82(2):1018–1040,
1992. [doi:10.1785/BSSA0820021018](https://doi.org/10.1785/BSSA0820021018)

Supports: the DC3D algorithm whose surface-displacement terms this project
ports; our benchmark suite validates the port against it.

### dc3d.py — reference implementation
satoshi-pes, *dc3d.py — Python port of the dislocation model DC3D.f by
Okada (1992)*, GitHub. https://github.com/satoshi-pes/dc3d.py
(vendored at `tools/dc3d_reference.py`)

Supports: generation of the frozen Okada benchmark values. Cross-validation
result: worst relative difference 0.0 over 72 scalar comparisons (8
significant digits).

### Rodríguez & Martín 1992 — interferometric phase noise
E. Rodriguez, J. M. Martin, *Theory and design of interferometric synthetic
aperture radars*, IEE Proceedings F 139(2):147–159, 1992.
[doi:10.1049/ip-f-2.1992.0018](https://doi.org/10.1049/ip-f-2.1992.0018)

Supports: σ_φ = sqrt((1−γ²)/(2Lγ²)), the coherence-based phase standard
deviation used in the uncertainty layer.

### Hanssen 2001 — the InSAR error taxonomy
R. F. Hanssen, *Radar Interferometry: Data Interpretation and Error
Analysis*, Kluwer Academic Publishers, 2001.
[doi:10.1007/0-306-47633-9](https://doi.org/10.1007/0-306-47633-9)

Supports: the classification of atmospheric, ionospheric, unwrapping and
decorrelation error sources that structures the uncertainty and attribution
layers.

### Hanks & Kanamori 1979 — moment magnitude
T. C. Hanks, H. Kanamori, *A moment magnitude scale*, Journal of
Geophysical Research 84(B5):2348–2350, 1979.
[doi:10.1029/JB084iB05p02348](https://doi.org/10.1029/JB084iB05p02348)

Supports: M0 = 10^(1.5·Mw + 9.1) N·m used to scale the forward model.

### Wells & Coppersmith 1994 — empirical rupture scaling
D. L. Wells, K. J. Coppersmith, *New empirical relationships among
magnitude, rupture length, rupture width, rupture area, and surface
displacement*, Bulletin of the Seismological Society of America
84(4):974–1002, 1994. [doi:10.1785/BSSA0840040974](https://doi.org/10.1785/BSSA0840040974)

Supports: log10(A/km²) = 0.91·Mw − 3.49 (all slip types) for fault
dimensions in the forward-model screen. Used with its stated scatter
(~0.25 decades) as an order-of-magnitude relation only.

### Aki & Richards 2002 — rake convention
K. Aki, P. G. Richards, *Quantitative Seismology*, 2nd edition, University
Science Books, 2002.

Supports: the rake (slip-vector) convention used to map USGS moment-tensor
solutions onto Okada dislocation components.

### Huber 1981 — robust statistics
P. J. Huber, *Robust Statistics*, John Wiley & Sons, 1981.
[doi:10.1002/0471725250](https://doi.org/10.1002/0471725250)

Supports: median/MAD estimators (50% breakdown point) used for referencing
and reference-region scatter.

### Snyder 1987 — map projections
J. P. Snyder, *Map Projections — A Working Manual*, U.S. Geological Survey
Professional Paper 1395, 1987. [doi:10.3133/pp1395](https://doi.org/10.3133/pp1395)

Supports: the transverse Mercator (UTM) forward/inverse series used for map
alignment of product grids, in both the science core and the browser
renderer.

## NISAR mission documentation

### NISAR L1/L2 ATBD — the phase convention
NASA Jet Propulsion Laboratory / NISAR Project, *NISAR NASA SDS Algorithm
Theoretical Basis Document*, JPL D-95677 Rev A, 12 Nov 2023.
[PDF (ASF cloud, open)](https://nisar.asf.earthdatacloud.nasa.gov/NISAR-SAMPLE-DATA/DOCS/NISAR_D-95677_NASA_L1_L2_ATBD_20231112_R3.4_w-sigs.pdf)

Supports: interferogram formation as reference × conj(secondary) (§5.7);
the phase decomposition Δφ = (4π/λ)(Δr_d + Δr_g + Δr_t + Δr_i + Δr_n)
(§5.11.1) that fixes the sign of `d = λφ/(4π)`; the split-spectrum
ionospheric correction description.

### NISAR L2 GUNW Product Specification
NASA Jet Propulsion Laboratory / NISAR Project, *NISAR L2 GUNW Product
Specification*, JPL D-102272 Rev E, 8 Nov 2024.
[PDF (ASF cloud, open)](https://nisar.asf.earthdatacloud.nasa.gov/NISAR-SAMPLE-DATA/DOCS/NISAR_D-102272_RevE_NASA_SDS_Product_Specification_L2_GUNW_Nov8_2024_w-sigs.pdf)

Supports: the HDF5 layout (unwrappedPhase, coherenceMagnitude,
connectedComponents, ionosphere screens, radarGrid metadata) consumed by
the extractor.

### NISAR sample data portal
NASA / ASF DAAC, *NISAR Sample Data*. https://nisar.jpl.nasa.gov/data/sample-data/
(open access; hosted on the ASF Earthdata Cloud)

Supports: the analysed sample product (ALOS PALSAR 2008 Los Angeles pair
processed through NISAR SDS v0.24.0; local copy under `data/nisar-sample/`).
Product DOI recorded in-file: 10.5067/NIL2GUNW-B1.

## Services and datasets

### NASA CMR Search API
NASA Earthdata, *Common Metadata Repository Search*.
https://cmr.earthdata.nasa.gov/search/

Supports: live discovery of NISAR L2 GUNW granule availability
(collections `NISAR_L2_GUNW_PROVISIONAL_V1`, 90,974 granules, and
`NISAR_L2_GUNW_BETA_V1`, 10,217 — verified 2026-10-20) and the
acquisition-pair/event bracketing logic.

### USGS ComCat FDSN API
U.S. Geological Survey, *Earthquake Catalog API*.
https://earthquake.usgs.gov/fdsnws/event/1/

Supports: independent seismic evidence for the attribution layer;
moment-tensor nodal planes (e.g. `us6000u18k`, the 2026 Panama M7.7:
strike 25.21°, dip 74.77°, rake −155.3°, M0 3.77×10²⁰ N·m) for the forward
model.

---

*Checked 2026-10-20. The registry carries `checkedAt` per entry; re-verify
links when forking this project.*
