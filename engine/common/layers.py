"""Which NISAR layers exist, what they mean, and how to ask the retriever for exactly those.

The retrieval layer names every dataset by its HDF5 path with '/' replaced by '__', e.g.
`science__LSAR__GUNW__grids__frequencyA__unwrappedInterferogram__HH__unwrappedPhase`. This module parses
those keys into a `LayerId` (product, section, polarization, dataset name) and maps them onto *roles*
(`unw_phase`, `cov`, `incidence`, ...) that the rest of engine/common works with, so no other module
hard-codes an HDF5 path. The layout was read from the product catalogs shipped in config/catalog/.

Measurement codes (from the pipeline manifests, because `engine/retrieval/sources/` is still empty):
    M1 = GUNW unwrapped interferogram stack      M2 = GCOV backscatter covariance
    M3 = pixel offsets (GUNW pixelOffsets / GOFF) M4 = soil moisture   M5 = GSLC
    M6 = Sentinel-1                              M7 = OPERA cross-check products
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

# ---------------------------------------------------------------------------------------------
# roles
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RoleInfo:
    role: str
    measurement: str        # M1 | M2 | M3 | geometry | corrections
    grid: str               # canonical target grid: G80 | G20
    method: str             # nearest | bilinear | area
    kind: str               # phase | coherence | cc | mask | power | float | angle | count


def _r(role, measurement, grid, method, kind) -> tuple[str, RoleInfo]:
    return role, RoleInfo(role, measurement, grid, method, kind)


ROLES: dict[str, RoleInfo] = dict([
    # GUNW / GOFF  grids/frequencyX/unwrappedInterferogram/<pol>/<name>
    _r("unw_phase", "M1", "G80", "bilinear", "phase"),
    _r("unw_coherence", "M1", "G80", "bilinear", "coherence"),
    _r("conn_comp", "M1", "G80", "nearest", "cc"),
    _r("iono_phase", "M1", "G80", "bilinear", "phase"),
    _r("iono_sigma", "M1", "G80", "bilinear", "phase"),
    _r("unw_mask", "M1", "G80", "nearest", "mask"),
    # grids/frequencyX/pixelOffsets/<pol>/<name>
    _r("off_range", "M3", "G80", "bilinear", "float"),
    _r("off_azimuth", "M3", "G80", "bilinear", "float"),
    _r("off_peak", "M3", "G80", "bilinear", "coherence"),
    _r("off_mask", "M3", "G80", "nearest", "mask"),
    # GCOV grids/frequencyX/<name>
    _r("cov", "M2", "G20", "area", "power"),
    _r("cov_mask", "M2", "G20", "nearest", "mask"),
    _r("n_looks", "M2", "G20", "area", "count"),
    _r("rtc_g2s", "M2", "G20", "area", "float"),
    _r("noise_floor", "M2", "G80", "bilinear", "float"),
    # metadata/radarGrid/<name>: cubes over (height, y, x), harmonized to G80
    _r("incidence", "geometry", "G80", "bilinear", "angle"),
    _r("los_x", "geometry", "G80", "bilinear", "float"),
    _r("los_y", "geometry", "G80", "bilinear", "float"),
    _r("along_x", "geometry", "G80", "bilinear", "float"),
    _r("along_y", "geometry", "G80", "bilinear", "float"),
    _r("elevation_angle", "geometry", "G80", "bilinear", "angle"),
    _r("bperp", "geometry", "G80", "bilinear", "float"),
    _r("bpar", "geometry", "G80", "bilinear", "float"),
    _r("tropo_hydro_phase", "corrections", "G80", "bilinear", "phase"),
    _r("tropo_wet_phase", "corrections", "G80", "bilinear", "phase"),
    _r("tide_phase", "corrections", "G80", "bilinear", "phase"),
])

# (section, dataset name) -> role, for the interferometric products
_GUNW_NAMES = {
    ("unwrappedInterferogram", "unwrappedPhase"): "unw_phase",
    ("unwrappedInterferogram", "coherenceMagnitude"): "unw_coherence",
    ("unwrappedInterferogram", "connectedComponents"): "conn_comp",
    ("unwrappedInterferogram", "ionospherePhaseScreen"): "iono_phase",
    ("unwrappedInterferogram", "ionospherePhaseScreenUncertainty"): "iono_sigma",
    ("unwrappedInterferogram", "mask"): "unw_mask",
    ("pixelOffsets", "slantRangeOffset"): "off_range",
    ("pixelOffsets", "alongTrackOffset"): "off_azimuth",
    ("pixelOffsets", "correlationSurfacePeak"): "off_peak",
    ("pixelOffsets", "mask"): "off_mask",
}
_RADARGRID_NAMES = {
    "incidenceAngle": "incidence",
    "losUnitVectorX": "los_x",
    "losUnitVectorY": "los_y",
    "alongTrackUnitVectorX": "along_x",
    "alongTrackUnitVectorY": "along_y",
    "elevationAngle": "elevation_angle",
    "perpendicularBaseline": "bperp",
    "parallelBaseline": "bpar",
    "hydrostaticTroposphericPhaseScreen": "tropo_hydro_phase",
    "wetTroposphericPhaseScreen": "tropo_wet_phase",
    "slantRangeSolidEarthTidesPhase": "tide_phase",
}
_GCOV_NAMES = {"mask": "cov_mask", "numberOfLooks": "n_looks", "rtcGammaToSigmaFactor": "rtc_g2s"}
_COV_TERM = re.compile(r"^([HV])([HV])\1\2$")        # HHHH, HVHV, VVVV, VHVH: the diagonal (power) terms
_POL = re.compile(r"^[HV]{2}$")

INTERFEROMETRIC = ("GUNW", "GOFF")


@dataclass(frozen=True)
class LayerId:
    key: str
    product: str
    sensor: str
    area: str                    # grids | metadata
    frequency: str | None
    section: str | None
    pol: str | None
    name: str
    role: str | None

    @property
    def path(self) -> str:
        return "/" + self.key.replace("__", "/")

    @property
    def info(self) -> RoleInfo | None:
        return ROLES.get(self.role) if self.role else None


def parse_layer_key(key: str) -> LayerId:
    """Flat retrieval key -> LayerId. Unknown layouts parse with role=None (never raises on layout)."""
    parts = key.split("__")
    if len(parts) < 5 or parts[0] != "science":
        return LayerId(key, "?", "?", "?", None, None, None, parts[-1], None)
    sensor, product, area, rest = parts[1], parts[2], parts[3], parts[4:]
    freq = section = pol = None
    role = None
    name = rest[-1]
    if area == "grids" and rest and rest[0].startswith("frequency"):
        freq = rest[0][len("frequency"):] or None
        tail = rest[1:]
        if product in INTERFEROMETRIC:
            if len(tail) == 3 and _POL.match(tail[1]):
                section, pol, name = tail
            elif len(tail) == 2:
                section, name = tail
            role = _GUNW_NAMES.get((section, name)) if section else None
        elif product == "GCOV" and len(tail) == 1:
            name = tail[0]
            m = _COV_TERM.match(name)
            if m:
                pol, role = m.group(1) + m.group(2), "cov"
            else:
                role = _GCOV_NAMES.get(name)
    elif area == "metadata" and rest and rest[0] == "radarGrid" and len(rest) == 2:
        section, name = "radarGrid", rest[1]
        role = _RADARGRID_NAMES.get(name)
    elif area == "metadata" and "noiseEquivalentBackscatter" in rest and rest[-1] in ("HH", "HV", "VV", "VH"):
        section, pol, role = "noiseEquivalentBackscatter", rest[-1], "noise_floor"
        for p in rest:
            if p.startswith("frequency"):
                freq = p[len("frequency"):]
    return LayerId(key, product, sensor, area, freq, section, pol, name, role)


# ---------------------------------------------------------------------------------------------
# asking the retriever for layers
# ---------------------------------------------------------------------------------------------
_GEOMETRY_NAMES = ("incidenceAngle", "losUnitVectorX", "losUnitVectorY", "alongTrackUnitVectorX",
                   "alongTrackUnitVectorY", "elevationAngle", "perpendicularBaseline", "parallelBaseline")
_CORRECTION_NAMES = ("hydrostaticTroposphericPhaseScreen", "wetTroposphericPhaseScreen",
                     "slantRangeSolidEarthTidesPhase")


def _alt(items) -> str:
    return "(" + "|".join(sorted(items)) + ")"


def wanted_products(codes: set[str] | None, include_goff: bool = False) -> list[str]:
    """Which NISAR products have to be fetched to serve these measurement codes (None = all)."""
    out = []
    if codes is None or codes & {"M1", "M3", "P07"}:
        out.append("GUNW")
    if codes is None or "M2" in codes:
        out.append("GCOV")
    if include_goff and (codes is None or "M3" in codes):
        out.append("GOFF")
    return out


def request_layers(product: str, codes: set[str] | None, frequency: str = "A", pols=("HH", "HV", "VV", "VH"),
                   use_noise_floor: bool = False) -> str | None:
    """Regex handed to NisarClient.fetch_tile(layers=...) for one product, or None if nothing is needed.
    Matches HDF5 paths (with '/'), anchored at the dataset name."""
    f = re.escape(frequency)
    alts: list[str] = []
    need = (lambda c: codes is None or c in codes)
    co = _alt(p for p in pols if p in ("HH", "VV"))
    if product in INTERFEROMETRIC:
        if need("M1") or need("P07"):
            alts.append(rf"/{product}/grids/frequency{f}/unwrappedInterferogram/{co}/"
                        + _alt(n for (s, n) in _GUNW_NAMES if s == "unwrappedInterferogram" and n != "mask") + "$")
            alts.append(rf"/{product}/grids/frequency{f}/unwrappedInterferogram/mask$")
            alts.append(rf"/{product}/metadata/radarGrid/" + _alt(_CORRECTION_NAMES) + "$")
        if need("M3"):
            alts.append(rf"/{product}/grids/frequency{f}/pixelOffsets/{co}/"
                        + _alt(n for (s, n) in _GUNW_NAMES if s == "pixelOffsets" and n != "mask") + "$")
            alts.append(rf"/{product}/grids/frequency{f}/pixelOffsets/mask$")
        if alts:
            alts.append(rf"/{product}/metadata/radarGrid/" + _alt(_GEOMETRY_NAMES) + "$")
    elif product == "GCOV":
        if need("M2"):
            terms = sorted({p + p for p in pols if len(p) == 2})
            alts.append(rf"/GCOV/grids/frequency{f}/" + _alt(terms) + "$")
            alts.append(rf"/GCOV/grids/frequency{f}/" + _alt(_GCOV_NAMES) + "$")
            alts.append(rf"/GCOV/metadata/radarGrid/" + _alt(_GEOMETRY_NAMES[:5]) + "$")
            if use_noise_floor:
                alts.append(rf"/GCOV/metadata/calibrationInformation/frequency{f}/noiseEquivalentBackscatter/"
                            + _alt(pols) + "$")
    return "|".join(alts) if alts else None


# ---------------------------------------------------------------------------------------------
# dtype / fill conventions
# ---------------------------------------------------------------------------------------------
def integer_fill(dtype) -> int:
    """HDF5 fill for integer layers when the clip does not carry the dataset's _FillValue attribute:
    unsigned -> dtype max (255, 65535, ...), signed -> dtype min."""
    info = np.iinfo(dtype)
    return int(info.max) if np.dtype(dtype).kind == "u" else int(info.min)
