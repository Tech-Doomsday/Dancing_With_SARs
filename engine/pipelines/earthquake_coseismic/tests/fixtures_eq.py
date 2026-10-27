"""Test fixtures for 3.01 — synthetic Okada scenes and event providers.

These are validation fixtures in the engine's own tradition
(tests/common_fixtures.py), not mocks: they build real-shaped inputs with
known analytic truth so pipeline correctness is provable independent of the
NISAR archive depth.
"""
from __future__ import annotations

from engine.pipelines.earthquake_coseismic.types import EventRecord, EventsResult


def make_event(event_id: str = "eq-fixture-001", magnitude: float = 5.6,
               lat: float = 34.0, lon: float = -118.5, depth_km: float = 5.0,
               time: str = "2026-07-24T00:00:00Z",
               nodal_planes: tuple = ((90.0, 40.0, -90.0),)) -> EventRecord:
    """Fixture event calibrated against the engine's synthetic truth bowl.

    Default source: normal faulting (rake -90), Mw 5.6, depth 5 km,
    strike 90 / dip 40. Grid-searched over real Okada sources so its LOS
    forward prediction matches the common_fixtures truth bowl (peak +50 mm
    toward sensor, sigma 1.5 km) with pattern correlation ~ +0.78 and
    amplitude ratio ~ 1.1 — comfortably inside the consistency screen
    (ratio in (1/3, 3), r > 0.25). Place it ~3 km west/south of the bowl
    centre (see test_pipeline._event_lonlat).
    """
    return EventRecord(
        id=event_id, magnitude=magnitude, place="synthetic fixture event",
        time=time, lat=lat, lon=lon, depth_km=depth_km,
        url=None, nodal_planes=nodal_planes,
    )


def make_provider(events: list[EventRecord] | None = None, error: str | None = None):
    """Injectable event provider (fetch.set_event_provider) — offline tests.

    Passing `error` simulates a provider FAILURE (degraded result, never an
    exception) to prove the pipeline answers INSUFFICIENT_EVIDENCE.
    """
    def provider(bbox, t0, t1) -> EventsResult:
        if error is not None:
            return EventsResult(events=(), source="fixture", degraded=True, error=error)
        return EventsResult(events=tuple(events or ()), source="fixture", degraded=False, error=None)
    return provider


def make_null_provider():
    """Provider that answers with an EMPTY in-window list (a real 'no event')."""
    return make_provider(events=[])


# --------------------------------------------------------------------------- #
# uncatalogued-branch fixtures: scenes whose truth is a known vertical strike-slip Okada source
# (ported from the M2 implementation overlay — earthquake_coseismic_M2_wip_overlay.zip)
# --------------------------------------------------------------------------- #
import math  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import numpy as np  # noqa: E402

from engine.lib.okada import GeoFault, geo_fault_displacement_enu  # noqa: E402

# the source every uncatalogued test recovers (centre in tile-relative metres)
TRUE_SOURCE = dict(strike=90.0, rake=0.0, east=4000.0, north=5500.0, depth=3000.0,
                   length=6000.0, width=4000.0, slip=0.15)


def fixture_los_unit(incidence_deg: float = 40.0, heading_deg: float = -10.0) -> np.ndarray:
    """Ground->sensor unit vector of tests/common_fixtures.GeomSpec (left-looking)."""
    az = math.radians((heading_deg + 90.0) % 360.0)
    s, c = math.sin(math.radians(incidence_deg)), math.cos(math.radians(incidence_deg))
    return np.array([s * math.sin(az), s * math.cos(az), c])


def okada_los_field(x, y, e0: float, n0: float, los, src: dict | None = None):
    """Toward-sensor LOS field (m, engine sign) of the vertical strike-slip TRUE_SOURCE.

    x, y are absolute UTM metres; the source sits at (e0 + src.east, n0 + src.north).
    """
    src = src or TRUE_SOURCE
    fault = GeoFault(src["strike"], 90.0, src["rake"], src["length"], src["width"], 0.0, src["slip"], 0.0, 0.0)
    e, n, u, sing = geo_fault_displacement_enu(fault, src["depth"],
                                               np.asarray(x) - (e0 + src["east"]),
                                               np.asarray(y) - (n0 + src["north"]))
    out = e * los[0] + n * los[1] + u * los[2]
    return np.where(sing, 0.0, out)


def make_okada_analysis(src: dict | None = None, noise_m: float = 0.002, seed: int = 1,
                        los=(0.55, -0.10, 0.83), bowl_peak: float | None = None):
    """A PairAnalysis-shaped namespace on a 125x125, 80 m grid (north-up) + its DetectionSummary.

    `bowl_peak` replaces the Okada truth with an isotropic Gaussian uplift (a field the v1
    vertical strike-slip fit cannot represent). Returns (analysis, summary, (x_true, y_true)).
    """
    from engine.pipelines.earthquake_coseismic import detection as det

    src = src or TRUE_SOURCE
    n, px = 125, 80.0
    x0, y0 = 500000.0, 4000000.0
    gx = x0 + px * (np.arange(n) + 0.5)
    gy = y0 + px * (n - 1 - np.arange(n) + 0.5)
    X, Y = np.meshgrid(gx, gy)
    l = np.asarray(los, float)
    l = l / np.linalg.norm(l)
    if bowl_peak is None:
        truth = okada_los_field(X, Y, x0, y0, l, src)
    else:
        truth = bowl_peak * np.exp(-(((X - (x0 + src["east"])) ** 2 + (Y - (y0 + src["north"])) ** 2)
                                     / (2 * 1500.0 ** 2)))
    meas = truth + np.random.default_rng(seed).normal(0.0, noise_m, truth.shape)
    mask = np.ones((n, n), bool)
    edge = np.zeros_like(mask)
    edge[:15] = edge[-15:] = True
    edge[:, :15] = edge[:, -15:] = True
    disp = (meas - np.median(meas[edge])).astype(np.float32)
    an = SimpleNamespace(
        disp=disp, mask=mask, sigma_eff=np.full((n, n), 0.003, np.float32), grid_x=gx, grid_y=gy,
        los_enu=np.broadcast_to(l[:, None, None], (3, n, n)).copy(), reference_mask=edge,
        pixel_size=(px, -px), iono_floor_m=None)
    summary, _ = det.detection_pass(an.disp, an.sigma_eff, mask, np.full((n, n), 0.9), None, an.pixel_size,
                                    k=3.0, floor_m=0.01, cluster_min_pixels=4)
    return an, summary, (x0 + src["east"], y0 + src["north"])
