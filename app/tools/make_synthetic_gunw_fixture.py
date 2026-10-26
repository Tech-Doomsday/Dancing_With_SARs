#!/usr/bin/env python3
"""
Build a small SYNTHETIC NISAR-L2-GUNW-layout HDF5 fixture for testing
tools/extract_gunw.py.

The fixture is explicitly synthetic: the phase layer is a smooth analytic
ramp, coherence is a constant, and every array is tiny (24x16). It exists
ONLY to exercise the extractor's structural validation paths (correct
layout -> success; corrupted layouts -> clear failure). It must never be
presented as, or mistaken for, a real NISAR product: the granule id carries
the marker SYNTHETIC-FIXTURE and the identification group records
missionId=SYNTHETIC.

Usage:
    python3 tools/make_synthetic_gunw_fixture.py <out.h5> [--corrupt KIND]

Corruption kinds (for the failure-path tests):
    missing-layer     omit unwrappedPhase
    bad-shape         coherence has a different shape than phase
    positive-dy       yCoordinateSpacing is positive (invalid convention)
    bad-spec          productSpecificationVersion 9.9 (unsupported layout)
    no-copol          only an HV layer exists
    mismatched-coords xCoordinates length != raster width
"""
import argparse
import math

import h5py
import numpy as np

W, H = 16, 24
DX, DY = 80.0, -80.0
EPSG = 32611


def build(path, corrupt=None):
    with h5py.File(path, "w") as f:
        # --- identification (product layout: science/LSAR/identification) ---
        ident = f.create_group("science/LSAR/identification")
        strings = {
            "granuleId": "NISAR_L2_PR_GUNW_SYNTHETIC-FIXTURE_001",
            "missionId": "SYNTHETIC",
            "lookDirection": "Right",
            "orbitPassDirection": "Ascending",
            "productSpecificationVersion": "1.2.1",
            "productDoi": "10.0000/SYNTHETIC",
            "processingDateTime": "2026-01-01T00:00:00Z",
            "referenceZeroDopplerStartTime": "2026-01-01T00:00:00Z",
            "referenceZeroDopplerEndTime": "2026-01-01T00:00:10Z",
            "secondaryZeroDopplerStartTime": "2026-01-13T00:00:00Z",
            "secondaryZeroDopplerEndTime": "2026-01-13T00:00:10Z",
            "boundingPolygon": "POLYGON((-118.0 34.0,-117.0 34.0,-117.0 35.0,-118.0 35.0,-118.0 34.0))",
        }
        for k, v in strings.items():
            ident[k] = v
        ident["frameNumber"] = np.uint32(1)
        ident["trackNumber"] = np.uint32(1)

        # --- grids/frequencyA/unwrappedInterferogram/HH ---
        grid = f.create_group("science/LSAR/GUNW/grids/frequencyA")
        grid["centerFrequency"] = np.float64(1.26999975e9)
        ui = grid.create_group("unwrappedInterferogram/HH") if corrupt != "no-copol" else None

        xs = np.arange(W, dtype=np.float64) * DX
        ys = np.arange(H, dtype=np.float64) * DY
        # Smooth analytic ramp: clearly synthetic, non-constant.
        phase = (0.5 * np.sin(np.linspace(0, 1.0, W)[None, :] * math.pi)
                 * np.cos(np.linspace(0, 1.0, H)[:, None] * math.pi)).astype(np.float32)
        coherence = np.full((H, W), 0.8, dtype=np.float32)
        components = np.ones((H, W), dtype=np.uint16)
        iono = np.full((H, W), 0.1, dtype=np.float32)
        iono_unc = np.full((H, W), 0.05, dtype=np.float32)

        if corrupt == "no-copol":
            # Only an HV (cross-pol) layer: the co-pol check must reject this.
            target = grid.create_group("unwrappedInterferogram/HV")
        else:
            target = ui

        if corrupt != "missing-layer":
            target["unwrappedPhase"] = phase
        target["coherenceMagnitude"] = (
            np.full((H - 1, W), 0.8, dtype=np.float32) if corrupt == "bad-shape" else coherence
        )
        target["connectedComponents"] = components
        target["ionospherePhaseScreen"] = iono
        target["ionospherePhaseScreenUncertainty"] = iono_unc
        target["xCoordinates"] = xs[:-1] if corrupt == "mismatched-coords" else xs
        target["yCoordinates"] = ys
        target["xCoordinateSpacing"] = np.float64(DX)
        target["yCoordinateSpacing"] = np.float64(DY if corrupt != "positive-dy" else 80.0)
        target["projection"] = np.uint32(EPSG)

        # --- metadata ---
        meta = f.create_group("science/LSAR/GUNW/metadata")
        rg = meta.create_group("radarGrid")
        inc = np.full((4, 4), 34.0, dtype=np.float32)
        rg["incidenceAngle"] = inc
        rg["losUnitVectorX"] = np.full((4, 4), -0.55, dtype=np.float32)
        rg["losUnitVectorY"] = np.full((4, 4), -0.12, dtype=np.float32)
        meta.create_group("orbit")["temporalBaseline"] = np.int32(12)

        pr = meta.create_group("processingInformation")
        alg = pr.create_group("algorithms")
        alg["softwareVersion"] = "0.0.0-synthetic"
        alg.create_group("unwrapping")["unwrappingAlgorithm"] = "synthetic"
        iff = alg.create_group("interferogramFormation")
        iff["flatteningMethod"] = "synthetic"
        iff["wrappedInterferogramFiltering"] = "none"
        params = pr.create_group("parameters")
        uip = params.create_group("unwrappedInterferogram/frequencyA")
        uip["numberOfAzimuthLooks"] = np.int32(2)
        uip["numberOfRangeLooks"] = np.int32(3)
        uip["topographicFlatteningApplied"] = "True"
        uip["ellipsoidalFlatteningApplied"] = "True"
        geo = params.create_group("geocoding")
        geo["rangeIonosphericCorrectionApplied"] = "True"
        geo["azimuthIonosphericCorrectionApplied"] = "True"
        geo["hydrostaticTroposphericCorrectionApplied"] = "True"
        geo["wetTroposphericCorrectionApplied"] = "True"
        inputs = pr.create_group("inputs")
        inputs["demSource"] = "synthetic"
        inputs["l1ReferenceSlcGranules"] = np.array(["SYNTHETIC-REF"], dtype="S32")
        inputs["l1SecondarySlcGranules"] = np.array(["SYNTHETIC-SEC"], dtype="S32")

        if corrupt == "bad-spec":
            del ident["productSpecificationVersion"]
            ident["productSpecificationVersion"] = "9.9.9"

    print(f"wrote {'corrupt:' + corrupt + ' ' if corrupt else ''}{path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--corrupt", default=None, choices=[
        "missing-layer", "bad-shape", "positive-dy", "bad-spec", "no-copol", "mismatched-coords"])
    a = ap.parse_args()
    build(a.out, a.corrupt)
