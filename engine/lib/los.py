"""The ONE line-of-sight projection — sign convention fixed here and pinned by gate G0.

Why this module exists (finding F1 of the port review): the two codebases described the LOS
direction in OPPOSITE ways. The engine says `los_enu` points ground→sensor and `los_m` is positive
TOWARD the sensor (later date minus earlier). The old app said the product stores sensor→ground and
worked away-positive; its `projectDisplacementToLOS` returned the NEGATIVE of the dot product, so a
real coseismic signal would have correlated negatively with its model — invisible to its synthetic
tests because the fixtures used the same function to make the truth.

The rule (never re-derived anywhere else in the engine):

    los_m  = +λ/(4π)·phase, later minus earlier, positive TOWARD the sensor  (P06)
    los_enu = ground→sensor unit vector (E, N, Up), stored per pixel by P05   (SceneBundle)
    predicted toward-LOS = d_enu · los_enu        <- THIS MODULE, no negation anywhere

NISAR is LEFT-looking (ASF NISAR introduction) — never infer the look side from a constant; read it
from product metadata when available (config radar.look_side) and otherwise trust the bundle's own
stored vectors. Gate G0 (tests/test_g0_los_sign.py) pins the convention against a real product's
geometry and runs the metamorphic check: flipping the bundle's LOS sign must flip the verdict.
"""
from __future__ import annotations

import numpy as np


def los_project(d_east, d_north, d_up, los_enu) -> np.ndarray:
    """Project an ENU displacement field onto the LOS.

    d_east/d_north/d_up: arrays (n, n) of metres.
    los_enu: (3, n, n) ground→sensor unit vector from the bundle geometry.

    Returns metres, + = TOWARD the sensor (engine convention, matching los_m).
    """
    ux, uy, uz = los_enu[0], los_enu[1], los_enu[2]
    return np.asarray(d_east) * ux + np.asarray(d_north) * uy + np.asarray(d_up) * uz


def los_project_point(d_east: float, d_north: float, d_up: float, los_enu) -> float:
    """Scalar convenience for tests: one ENU displacement at one pixel's unit vector."""
    u = np.asarray(los_enu, dtype=np.float64).reshape(3)
    return float(d_east * u[0] + d_north * u[1] + d_up * u[2])


def los_unit_ok(los_enu, atol: float = 1e-4) -> bool:
    """Contract check the deep bundle check also runs: unit vectors, Up >= 0 (ground→sensor)."""
    a = np.asarray(los_enu, dtype=np.float64)
    if a.shape[0] != 3 or a.ndim < 2:
        return False
    norms = np.linalg.norm(a, axis=0)
    if not np.all(np.isfinite(norms)) or np.max(np.abs(norms - 1.0)) > atol:
        return False
    return bool(np.nanmin(a[2]) >= -1e-9)


def los_flip(los_enu) -> np.ndarray:
    """The metamorphic twin: the same vectors pointing sensor→ground (away-positive world).

    Used ONLY by gate G0: a pipeline fed the flipped vectors must reach the opposite conclusion
    about the same ground displacement — if it does not, the sign convention leaked somewhere.
    """
    return -np.asarray(los_enu)


__all__ = ["los_project", "los_project_point", "los_unit_ok", "los_flip"]
