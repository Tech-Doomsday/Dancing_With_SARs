#!/usr/bin/env python3
"""
Extract analysis-ready layers from a NISAR L2 GUNW (HDF5) product into the
MOTUS derived-product format.

This is a data-access tool, not scientific processing: it performs a lossless
extraction of the product's own science layers (unwrapped phase, coherence,
connected components, ionospheric phase screen statistics) plus the metadata
needed for interpretation. All scientific analysis (referencing,
phase->displacement, uncertainty, detection, attribution) is performed by the
TypeScript pipeline in src/lib/science/.

SUPPORTED SCOPE (validated, not assumed):
  - NISAR L2 GUNW products following the layout of product specification
    1.x: science/LSAR/GUNW/grids/frequencyA/unwrappedInterferogram/<co-pol>.
  - Only frequency A is supported. Per the official ASF NISAR GUNW guide
    (https://nisar-docs.asf.alaska.edu/gunw/, retrieved 2026-10-21), GUNW
    products "are only generated for frequency A" — matching this requirement
    is a validation gate, not an assumption.
  - Co-polarization (HH or VV) layers; the first available co-pol is used and
    recorded. Other layouts are rejected with a clear error.

CORRECTION-STATUS SEMANTICS (verified from the official ASF guide):
  - The ionospheric phase screen is INCLUDED in the product but NOT APPLIED
    to the data layers ("The estimated ionospheric phase screen is included
    in the product but not applied to the data layers within the GUNW product
    by default").
  - Tropospheric / solid-Earth-tide corrections are provided as geocoded
    lookup tables and are NOT removed from the interferometric data.
  - The metadata flags parameters/geocoding/*IonosphericCorrectionApplied
    etc. record corrections applied during the GEOCODING (geolocation) step,
    not phase corrections applied to the interferogram.
  This tool therefore extracts the ionospheric screen's own statistics
  (spatial variation) in addition to its uncertainty, and records the
  correction status explicitly for downstream honest labelling.

Usage:
    python3 tools/extract_gunw.py <product.h5> <output_dir> [--slug name]
        [--title T] [--blurb B]

Exit codes: 0 success, 2 validation failure (message on stderr).

References:
  - ASF NISAR GUNW product guide (nisar-docs.asf.alaska.edu/gunw/), 2026-10-21
  - NISAR L2 GUNW Product Specification (JPL D-102272)
  - NISAR NASA SDS L1/L2 ATBD (JPL D-95677 RevA, Nov 2023)
"""
import argparse
import json
import math
import os
import re
import sys

import h5py
import numpy as np

GUNW_BASE = "science/LSAR/GUNW"
CO_POLS = ("HH", "VV")
SUPPORTED_SPEC_PREFIXES = ("1.",)  # product specification 1.x layouts


class ValidationError(Exception):
    pass


def wkt_to_coords(wkt: str):
    m = re.search(r"\(\(([^)]+)\)", wkt)
    if not m:
        return []
    pts = []
    for tok in m.group(1).split(","):
        parts = tok.strip().split()
        if len(parts) >= 2:
            pts.append([float(parts[0]), float(parts[1])])
    return pts


def s(node):
    v = node[()]
    return v.decode() if isinstance(v, bytes) else v


def require(f, path, what):
    if path not in f:
        raise ValidationError(f"required {what} is missing: {path}")
    return f[path]


def robust_mad(a: np.ndarray) -> float:
    """Consistent (normal-equivalent) sigma from the MAD, NaNs ignored."""
    finite = a[np.isfinite(a)]
    if finite.size == 0:
        return float("nan")
    med = np.median(finite)
    return float(1.4826 * np.median(np.abs(finite - med)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("product")
    ap.add_argument("outdir")
    ap.add_argument("--slug", default="gunw")
    ap.add_argument("--title", default="")
    ap.add_argument("--blurb", default="")
    args = ap.parse_args()

    if not os.path.isfile(args.product):
        print(f"extract_gunw: input product not found: {args.product}", file=sys.stderr)
        return 2
    if not args.product.endswith(".h5"):
        print(f"extract_gunw: input does not look like an HDF5 product (.h5): {args.product}", file=sys.stderr)
        return 2

    try:
        with h5py.File(args.product, "r") as f:
            return extract(f, args)
    except ValidationError as e:
        print(f"extract_gunw: validation failed: {e}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"extract_gunw: cannot open HDF5 product: {e}", file=sys.stderr)
        return 2


def extract(f: h5py.File, args) -> int:
    # ---- identification --------------------------------------------------
    ident = require(f, "science/LSAR/identification", "identification group")
    for key in ("granuleId", "missionId", "lookDirection", "orbitPassDirection",
                "productSpecificationVersion", "productDoi",
                "referenceZeroDopplerStartTime", "secondaryZeroDopplerStartTime"):
        require(ident, key, f"science/LSAR/identification/{key}")

    spec_version = s(ident["productSpecificationVersion"])
    if not any(spec_version.startswith(p) for p in SUPPORTED_SPEC_PREFIXES):
        raise ValidationError(
            f"product specification version '{spec_version}' is outside the "
            f"supported layouts {SUPPORTED_SPEC_PREFIXES}; this extractor has "
            "only been validated against spec 1.x GUNW layouts"
        )

    # ---- frequency + polarization ----------------------------------------
    grids = require(f, f"{GUNW_BASE}/grids", "grids group")
    if "frequencyA" not in grids:
        raise ValidationError(
            "frequencyA group is missing; GUNW products are only generated for "
            "frequency A (ASF guide), so this file is not a standard GUNW product"
        )
    grid = grids["frequencyA"]

    ui_group = require(grid, "unwrappedInterferogram", "unwrappedInterferogram group")
    available = [p for p in CO_POLS if p in ui_group]
    if not available:
        raise ValidationError(
            f"no co-polarized unwrappedInterferogram layer ({'/'.join(CO_POLS)}) found; "
            "only co-pol GUNW products are supported"
        )
    pol = available[0]
    ui = ui_group[pol]

    # ---- science layers with structural validation ------------------------
    for key in ("unwrappedPhase", "coherenceMagnitude", "connectedComponents",
                "xCoordinates", "yCoordinates", "xCoordinateSpacing",
                "yCoordinateSpacing", "projection"):
        require(ui, key, f"unwrappedInterferogram/{pol}/{key}")

    phase = ui["unwrappedPhase"][()]
    coherence = ui["coherenceMagnitude"][()]
    components = ui["connectedComponents"][()]
    if phase.ndim != 2 or coherence.ndim != 2 or components.ndim != 2:
        raise ValidationError("science layers must be 2-D arrays")
    if not (phase.shape == coherence.shape == components.shape):
        raise ValidationError(
            f"layer shapes disagree: phase {phase.shape}, coherence {coherence.shape}, "
            f"components {components.shape}"
        )
    height, width = phase.shape
    if width < 2 or height < 2:
        raise ValidationError(f"degenerate grid {width}x{height}")

    xs = ui["xCoordinates"][()]
    ys = ui["yCoordinates"][()]
    if xs.shape[0] != width or ys.shape[0] != height:
        raise ValidationError(
            f"coordinate vectors do not match the rasters: x {xs.shape[0]} vs width {width}, "
            f"y {ys.shape[0]} vs height {height}"
        )
    dx = float(ui["xCoordinateSpacing"][()])
    dy = float(ui["yCoordinateSpacing"][()])
    if not (math.isfinite(dx) and dx > 0):
        raise ValidationError(f"xCoordinateSpacing must be positive and finite (got {dx})")
    if not (math.isfinite(dy) and dy < 0):
        raise ValidationError(
            f"yCoordinateSpacing must be negative (rows advance southward, NISAR "
            f"convention); got {dy}. A non-negative value means the grid is not the "
            "standard NISAR layout"
        )
    if dx != float(np.median(np.diff(xs))) or abs(dy) != abs(float(np.median(np.diff(ys)))):
        raise ValidationError("coordinate vectors are not evenly spaced as declared")

    epsg = int(ui["projection"][()])
    if not (32601 <= epsg <= 32760 or epsg in (3413, 3031)):
        raise ValidationError(
            f"projection EPSG {epsg} is neither WGS84 UTM (326xx/327xx) nor the "
            "polar stereographic codes (3413/3031) used by GUNW products"
        )

    # ---- radar geometry ---------------------------------------------------
    meta = require(f, f"{GUNW_BASE}/metadata", "metadata group")
    rg = require(meta, "radarGrid", "radarGrid group")
    for key in ("incidenceAngle", "losUnitVectorX", "losUnitVectorY"):
        require(rg, key, f"radarGrid/{key}")
    inc = float(np.nanmean(rg["incidenceAngle"][()]))
    lx = float(np.nanmean(rg["losUnitVectorX"][()]))
    ly = float(np.nanmean(rg["losUnitVectorY"][()]))
    if not all(map(math.isfinite, (inc, lx, ly))):
        raise ValidationError("radarGrid geometry means are not finite")

    # Heading derived from the product LOS unit vector (satellite->ground,
    # E/N components). ground->satellite azimuth = sat->ground azimuth + 180,
    # and for a right-looking radar heading = groundToSatAzimuth - 90.
    sat2g_az = math.degrees(math.atan2(lx, ly))
    g2s_az = sat2g_az + 180.0
    look = s(ident["lookDirection"]).lower()
    if look not in ("right", "left"):
        raise ValidationError(f"unexpected lookDirection '{look}'")
    heading = g2s_az - 90.0 if look == "right" else g2s_az + 90.0
    pass_dir = s(ident["orbitPassDirection"]).lower()

    # ---- wavelength from the product's own centre frequency ---------------
    require(grid, "centerFrequency", "grids/frequencyA/centerFrequency")
    freq = float(grid["centerFrequency"][()])
    if not (math.isfinite(freq) and 1e8 < freq < 3e9):
        raise ValidationError(f"centerFrequency implausible: {freq} Hz")
    wavelength = 299792458.0 / freq

    # ---- acquisition-time ordering ----------------------------------------
    ref_start = s(ident["referenceZeroDopplerStartTime"])
    ref_end = s(ident["referenceZeroDopplerEndTime"])
    sec_start = s(ident["secondaryZeroDopplerStartTime"])
    sec_end = s(ident["secondaryZeroDopplerEndTime"])
    from datetime import datetime
    def parse(ts):
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    try:
        if not (parse(ref_start) < parse(ref_end) < parse(sec_start) < parse(sec_end)):
            raise ValidationError(
                f"acquisition times are not ordered ref<refEnd<sec<secEnd: {ref_start} .. {sec_end}"
            )
    except ValueError as e:
        raise ValidationError(f"acquisition timestamps not ISO-8601: {e}")

    # ---- ionospheric phase screen statistics -------------------------------
    # SEMANTICS (ASF guide, retrieved 2026-10-21): the screen is INCLUDED in
    # the product but NOT APPLIED to the data layers. Two quantities matter:
    #   - the screen's own spatial variation -> the uncorrected ionospheric
    #     signal present in the measured phase (after constant referencing);
    #   - the screen's uncertainty layer -> the error of the screen estimate,
    #     i.e. how little is known about the true ionosphere.
    # Both are extracted; the pipeline raises the detection floor to at least
    # the larger of the two (see src/lib/science/pipeline.ts Stage D).
    iono_variation_mad = None
    iono_variation_std = None
    iono_uncertainty_median = None
    if "ionospherePhaseScreen" in ui:
        iono = ui["ionospherePhaseScreen"][()]
        if iono.shape != phase.shape:
            raise ValidationError(
                f"ionospherePhaseScreen shape {iono.shape} does not match the "
                f"science layers {phase.shape}"
            )
        finite = iono[np.isfinite(iono)]
        if finite.size:
            iono_variation_mad = robust_mad(iono)
            iono_variation_std = float(finite.std())
        if "ionospherePhaseScreenUncertainty" in ui:
            unc = ui["ionospherePhaseScreenUncertainty"][()]
            if unc.shape != phase.shape:
                raise ValidationError(
                    f"ionospherePhaseScreenUncertainty shape {unc.shape} does not "
                    f"match the science layers {phase.shape}"
                )
            ufinite = np.abs(unc[np.isfinite(unc)])
            if ufinite.size:
                iono_uncertainty_median = float(np.median(ufinite))
    # A missing screen is legal (not all products carry one); it is recorded
    # as null and the pipeline then has NO ionospheric floor information.

    # ---- processing information --------------------------------------------
    pr = require(meta, "processingInformation", "processingInformation group")
    for key in ("algorithms/softwareVersion",):
        require(pr, key, f"processingInformation/{key}")
    p_ig = f"parameters/unwrappedInterferogram/frequencyA"
    for key in ("numberOfAzimuthLooks", "numberOfRangeLooks",
                "topographicFlatteningApplied", "ellipsoidalFlatteningApplied"):
        require(pr, f"{p_ig}/{key}", f"processingInformation/{p_ig}/{key}")
    looks_az = int(pr[f"{p_ig}/numberOfAzimuthLooks"][()])
    looks_rg = int(pr[f"{p_ig}/numberOfRangeLooks"][()])
    if looks_az < 1 or looks_rg < 1:
        raise ValidationError(f"look counts must be >= 1 (got {looks_az} az x {looks_rg} rg)")

    def opt(path, default):
        try:
            return s(pr[path])
        except KeyError:
            return default

    # Correction flags: NOTE these are GEOCODING-scoped flags (they record
    # corrections applied to the geolocation, not to the interferometric
    # phase). They are preserved verbatim for provenance, and the derived
    # meta explicitly records that phase corrections are NOT applied to the
    # consumed layers (per the authoritative ASF guide).
    geocode_flags = {
        "rangeIonosphericCorrectionApplied": opt("parameters/geocoding/rangeIonosphericCorrectionApplied", "unknown"),
        "azimuthIonosphericCorrectionApplied": opt("parameters/geocoding/azimuthIonosphericCorrectionApplied", "unknown"),
        "hydrostaticTroposphericCorrectionApplied": opt("parameters/geocoding/hydrostaticTroposphericCorrectionApplied", "unknown"),
        "wetTroposphericCorrectionApplied": opt("parameters/geocoding/wetTroposphericCorrectionApplied", "unknown"),
    }

    require(ident, "boundingPolygon", "identification/boundingPolygon")
    poly = wkt_to_coords(s(ident["boundingPolygon"]))
    if len(poly) < 3:
        raise ValidationError("boundingPolygon did not parse into >= 3 points")

    # ---- write derived layers (lossless) -----------------------------------
    os.makedirs(args.outdir, exist_ok=True)
    phase.astype("<f4").tofile(os.path.join(args.outdir, "phase.f32"))
    coherence.astype("<f4").tofile(os.path.join(args.outdir, "coherence.f32"))
    components.astype("<u2").tofile(os.path.join(args.outdir, "components.u16"))

    meta_out = {
        "slug": args.slug,
        "title": args.title or s(ident["granuleId"]),
        "blurb": args.blurb,
        "extractor": {
            "tool": "tools/extract_gunw.py",
            "policy": "lossless extraction of product layers; no scientific processing",
            "validatedAgainst": "ASF NISAR GUNW guide (nisar-docs.asf.alaska.edu/gunw/, retrieved 2026-10-21); NISAR L2 GUNW product spec 1.x layouts",
        },
        "grid": {
            "width": int(width),
            "height": int(height),
            "xStart": float(xs[0]),
            "xSpacing": dx,
            "yStart": float(ys[0]),
            "ySpacing": dy,
            "epsg": epsg,
        },
        "radar": {
            "wavelength": wavelength,
            "centerFrequencyHz": freq,
            "polarization": pol,
            "frequency": "A",
            "incidenceAngleDeg": inc,
            "headingDeg": heading,
            "passDirection": "ascending" if "asc" in pass_dir else "descending",
            "lookDirection": look,
            "losUnitVectorProductX": lx,
            "losUnitVectorProductY": ly,
            "numberOfLooks": looks_az * looks_rg,
            "ionoScreenVariationMadRad": iono_variation_mad,
            "ionoScreenVariationStdRad": iono_variation_std,
            "ionoScreenUncertaintyMedianRad": iono_uncertainty_median,
        },
        "identification": {
            "missionId": s(ident["missionId"]),
            "granuleId": s(ident["granuleId"]),
            "frameNumber": int(ident["frameNumber"][()]),
            "trackNumber": int(ident["trackNumber"][()]),
            "productDoi": s(ident["productDoi"]),
            "productSpecificationVersion": spec_version,
            "processingDateTime": s(ident["processingDateTime"]),
            "referenceZeroDopplerStartTime": ref_start,
            "referenceZeroDopplerEndTime": ref_end,
            "secondaryZeroDopplerStartTime": sec_start,
            "secondaryZeroDopplerEndTime": sec_end,
            "temporalBaselineDays": int(meta["orbit/temporalBaseline"][()]),
            "boundingPolygon": poly,
        },
        "processing": {
            "softwareVersion": s(pr["algorithms/softwareVersion"]),
            "unwrappingAlgorithm": opt("algorithms/unwrapping/unwrappingAlgorithm", "unknown"),
            "flatteningMethod": opt("algorithms/interferogramFormation/flatteningMethod", "unknown"),
            "wrappedFiltering": opt("algorithms/interferogramFormation/wrappedInterferogramFiltering", "unknown"),
            "topographicFlatteningApplied": s(pr[f"{p_ig}/topographicFlatteningApplied"]),
            "ellipsoidalFlatteningApplied": s(pr[f"{p_ig}/ellipsoidalFlatteningApplied"]),
            # Geocoding-scoped flags (geolocation corrections, NOT phase
            # corrections) — preserved for provenance:
            **geocode_flags,
            # Authoritative correction status of the consumed phase layer:
            "phaseIonosphericCorrectionAppliedToLayer": "False (screen supplied, not applied — ASF GUNW guide)",
            "phaseTroposphericCorrectionAppliedToLayer": "False (LUTs supplied, not removed — ASF GUNW guide)",
            "demSource": opt("inputs/demSource", "unknown")[:200],
            "l1ReferenceSlcGranules": [g.decode() if isinstance(g, bytes) else str(g)
                                       for g in pr["inputs/l1ReferenceSlcGranules"][()]],
            "l1SecondarySlcGranules": [g.decode() if isinstance(g, bytes) else str(g)
                                       for g in pr["inputs/l1SecondarySlcGranules"][()]],
            "looksAzimuth": looks_az,
            "looksRange": looks_rg,
        },
        "stats": {
            "phaseNanFraction": float(np.isnan(phase).mean()),
            "coherenceMean": float(np.nanmean(coherence)) if np.isfinite(coherence).any() else None,
            "coherenceMedian": float(np.nanmedian(coherence)) if np.isfinite(coherence).any() else None,
        },
    }

    with open(os.path.join(args.outdir, "meta.json"), "w") as fh:
        json.dump(meta_out, fh, indent=2)
    print(json.dumps({
        "slug": args.slug,
        "grid": f"{width}x{height}",
        "epsg": epsg,
        "polarization": pol,
        "specVersion": spec_version,
        "wavelength": wavelength,
        "incidence": inc,
        "heading": heading,
        "pass": pass_dir,
        "looks": looks_az * looks_rg,
        "ionoScreenVariationMadRad": iono_variation_mad,
        "ionoScreenVariationStdRad": iono_variation_std,
        "ionoScreenUncertaintyMedianRad": iono_uncertainty_median,
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
