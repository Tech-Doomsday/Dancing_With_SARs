"""2-D nonlinear shallow-water equations (NSWE) — well-balanced finite-volume solver.

NumPy-vectorized port of the app's fv-solver.ts (same scheme, same constants, behavior-level
acceptance tests). Equations (conservative form, bed b positive-up, h = η − b):

    ∂h/∂t    + ∂(hu)/∂x + ∂(hv)/∂y = 0
    ∂(hu)/∂t + ∂(hu² + ½gh²)/∂x + ∂(huv)/∂y = −gh ∂b/∂x − τ_x/ρ
    ∂(hv)/∂t + ∂(huv)/∂x + ∂(hv² + ½gh²)/∂y = −gh ∂b/∂y − τ_y/ρ
    Manning friction τ/ρ = g n² u|u| / h^(1/3) (semi-implicit, per stage).

Numerics (every choice documented in the app's docs/tsunami.md and the evidence matrix):
  - Hydrostatic reconstruction (Audusse et al. 2004): interface states re-centred on max(b_L, b_R)
    with the double-sided pressure correction → exactly well-balanced for the lake at rest
    INCLUDING wet/dry fronts. The correction uses CELL-SIDE (reconstructed) depths so the
    equilibrium cancellation survives the MUSCL reconstruction.
  - HLL flux with Einfeldt speeds and George (2008) dry-front speeds; a dry side emits no waves.
    Positivity-preserving under the CFL bound (HLLC's contact wave is immaterial for tsunami
    propagation — documented).
  - order=2: MUSCL reconstruction of (η, hu, hv) with the monotonized-central limiter (bed
    piecewise-constant) + SSP-RK2 (Heun). order=1: piecewise-constant + one Euler stage.
    MUSCL is disabled below H_MUSCL (flattening) — second-order reconstruction is not
    positivity-safe at wet/dry fronts.
  - Wet/dry: h_dry = 1e-4 m; momentum zeroed below h_vel = 1 cm (thin-film limiter).
  - Adaptive Δt from the 2-D CFL SUM; cells bordering a DRY neighbour use the dry-front
    characteristic speed |u| + 2√(gh) (exact dam-break-into-dry-bed front speed — using |u|+√(gh)
    there violates positivity at run-up fronts).
  - Variable cell areas A_j = Δx_j·Δy: pairwise-conservative flux differencing (the flux leaving
    one cell enters its neighbour with the SAME floating-point value, so mass cancels exactly —
    the dam-break acceptance test pins relative mass drift < 1e-12).
  - Coriolis and spherical metric terms omitted (documented approximation: for tsunami periods
    f ≪ ω; first-arrival error ≪ 1 % regionally).
  - Open boundaries: Rayleigh sponge layers (quadratic ramp on η−η₀ and momentum) on every edge;
    land edges are naturally reflective dry cells. sponge_enabled=False gives a purely reflective
    basin (used by the conservation benchmarks, where absorption MUST NOT happen).

GRID: LOCAL CARTESIAN (tangent-plane) formulation — all rows use the zonal cell width at the grid
CENTRE latitude. With per-row cos(φ) widths and no metric terms, a conservative variable-area
discretization cannot be exactly well-balanced (O(Δcosφ) resting-lake residual); the tangent-plane
form makes conservation and well-balancing EXACT at the cost of a zonal-distance error ≤ ~5 % at
the edges of a ±5° regional domain (verified acceptable against the DART ±15 min tolerance).

Row 0 = southernmost row (ETOPO1 convention); j indexes latitude, i longitude.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

G = 9.81
H_DRY = 1e-4
H_VEL = 0.01                    # momentum zeroed below this depth (m)
H_VEL2 = H_VEL * H_VEL
H_MUSCL = 0.1                   # MUSCL disabled below this depth
SPONGE_MAX = 0.25
EARTH_RADIUS_M = 6371.0088e3


@dataclass(frozen=True)
class SimParams:
    """Simulation parameters (ported SimParams; defaults = the app's DEFAULT_SIM_PARAMS)."""

    res_arcmin: int = 2              # ETOPO1 stride: 1, 2 or 4 arc-minutes per cell
    sim_hours: float = 4.0
    manning_n: float = 0.025         # 0 disables friction
    order: int = 2                   # 1 = HLL forward Euler, 2 = MUSCL + SSP-RK2
    arrival_threshold_m: float = 0.05
    domain_half_deg: float = 5.0
    sponge_enabled: bool = True

    @classmethod
    def from_mapping(cls, m: dict | None) -> "SimParams":
        m = dict(m or {})
        fields = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in m.items() if k in fields})


def build_geometry(res_arcmin: float, lat0: float, nx: int, ny: int) -> dict:
    """Tangent-plane geometry: constant zonal width at the grid's centre latitude (see module doc)."""
    lat_c = lat0 + ((ny - 1) * res_arcmin) / 120.0
    dx = math.radians(res_arcmin / 60.0) * EARTH_RADIUS_M * math.cos(math.radians(lat_c))
    dy = math.radians(res_arcmin / 60.0) * EARTH_RADIUS_M
    return {"dx": dx, "dy": dy, "area": dx * dy, "lat_c": lat_c}


def _mc2(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Two-argument monotonized-central (MC) limiter slope, vectorized."""
    same_sign = a * b > 0
    s = a + b
    m = 2.0 * np.minimum(np.abs(a), np.abs(b))
    out = np.sign(s) * np.minimum(np.abs(s) / 2.0, m)
    return np.where(same_sign & (s != 0), out, 0.0)


def _slopes(f: np.ndarray) -> tuple:
    """MUSCL slopes of a field in both directions (edges 0)."""
    sx = np.zeros_like(f)
    sy = np.zeros_like(f)
    if f.shape[1] >= 3:
        sx[:, 1:-1] = _mc2(f[:, 1:-1] - f[:, :-2], f[:, 2:] - f[:, 1:-1])
    if f.shape[0] >= 3:
        sy[1:-1, :] = _mc2(f[1:-1, :] - f[:-2, :], f[2:, :] - f[1:-1, :])
    return sx, sy


def _hll_flux(hL, uL, vL, hR, uR, vR):
    """HLL flux for the shallow-water system (normal velocity u); vectorized.

    Returns (mass, normal-momentum, tangential-momentum) flux arrays."""
    both_dry = (hL <= 0) & (hR <= 0)
    cL = np.sqrt(np.maximum(G * hL, 0.0))
    cR = np.sqrt(np.maximum(G * hR, 0.0))
    sL = np.minimum(uL - cL, uR - cR)
    sR = np.maximum(uL + cL, uR + cR)
    # Dry-front speeds (George 2008): the dry side emits nothing faster than the wet side's
    # rarefaction edge.
    sL = np.where((hL <= 0) & (hR > 0), np.minimum(sL, uR - 2.0 * cR), sL)
    sR = np.where((hR <= 0) & (hL > 0), np.maximum(sR, uL + 2.0 * cL), sR)

    f0L = hL * uL
    f1L = hL * uL * uL + 0.5 * G * hL * hL
    f2L = hL * uL * vL
    f0R = hR * uR
    f1R = hR * uR * uR + 0.5 * G * hR * hR
    f2R = hR * uR * vR

    supersonic_left = sL >= 0
    supersonic_right = sR <= 0
    inv = 1.0 / np.where(sR - sL == 0, 1e-12, sR - sL)
    hll0 = (sR * f0R - sL * f0L + sL * sR * (hR - hL)) * inv
    hll1 = (sR * f1R - sL * f1L + sL * sR * (hR * uR - hL * uL)) * inv
    hll2 = (sR * f2R - sL * f2L + sL * sR * (hR * vR - hL * vL)) * inv

    out0 = np.where(both_dry, 0.0, np.where(supersonic_left, f0L, np.where(supersonic_right, f0R, hll0)))
    out1 = np.where(both_dry, 0.0, np.where(supersonic_left, f1L, np.where(supersonic_right, f1R, hll1)))
    out2 = np.where(both_dry, 0.0, np.where(supersonic_left, f2L, np.where(supersonic_right, f2R, hll2)))
    return out0, out1, out2


class NsweSolver:
    """The stepping NSWE solver. Construct once per run; call advance() in chunks (progress)."""

    def __init__(self, res_arcmin: float, lat0: float, lon0: float, bed: np.ndarray,
                 eta_init: np.ndarray, params: SimParams, gauge_specs: list | None = None):
        self.res_arcmin = float(res_arcmin)
        self.lat0 = float(lat0)
        self.lon0 = float(lon0)
        self.bed = np.asarray(bed, dtype=np.float64)
        self.params = params
        self.ny, self.nx = self.bed.shape
        self.geom = build_geometry(res_arcmin, lat0, self.nx, self.ny)

        eta0 = np.asarray(eta_init, dtype=np.float64)
        self.eta = np.maximum(eta0, self.bed)          # surface never below the bed
        self.eta0_ref = self.eta.copy()
        self.hu = np.zeros_like(self.eta)
        self.hv = np.zeros_like(self.eta)

        self.max_eta = np.zeros_like(self.eta)
        self.arrival = np.full_like(self.eta, np.nan)
        self.max_flow_depth = np.zeros_like(self.eta)
        self.arrival_threshold = float(params.arrival_threshold_m)
        self.order = int(params.order)
        self.manning_n = float(params.manning_n)

        # Sponge width scales with resolution (~35 cells) but never more than 15 % of either
        # dimension — a 2-D min-distance sponge would otherwise blanket small domains entirely.
        if params.sponge_enabled:
            self.sponge_cells = max(2, min(int(round(35.0 / math.sqrt(self.res_arcmin))),
                                           int(math.floor(0.15 * min(self.nx, self.ny)))))
            jj, ii = np.meshgrid(np.arange(self.ny), np.arange(self.nx), indexing="ij")
            d = np.minimum(np.minimum(jj, self.ny - 1 - jj), np.minimum(ii, self.nx - 1 - ii))
            f = 1.0 - d / self.sponge_cells
            self.sponge = np.where(d < self.sponge_cells, SPONGE_MAX * f * f, 0.0)
        else:
            self.sponge = np.zeros_like(self.eta)

        h = self.eta - self.bed
        self.mass0 = float(h[h > 0].sum() * self.geom["area"])

        # gauges: nearest cell, -1 when outside the domain
        self.gauges = []
        for spec in (gauge_specs or []):
            j = int(round((spec["lat"] - self.lat0) * 60.0 / self.res_arcmin))
            i = int(round((spec["lon"] - self.lon0) * 60.0 / self.res_arcmin))
            idx = j * self.nx + i if (0 <= j < self.ny and 0 <= i < self.nx) else -1
            self.gauges.append({"spec": spec, "j": j, "i": i, "t_min": [], "eta": []})
        self.gauge_dt = 30.0          # s
        self.next_gauge_t = 0.0

        self.t = 0.0
        self.n_steps = 0
        self.min_dt = math.inf
        self.max_dt = 0.0
        self.max_vel = 0.0
        self.n_neg_clamped = 0

        self._record_fields()

    # ------------------------------------------------------------------ properties
    @property
    def sim_time(self) -> float:
        return self.t

    @property
    def progress_info(self) -> dict:
        return {"t": self.t, "steps": self.n_steps, "max_vel": self.max_vel,
                "fraction": min(1.0, self.t / max(1.0, self.params.sim_hours * 3600.0))}

    # ------------------------------------------------------------------ internals
    def _compute_dt(self) -> float:
        cfl = 0.45 if self.order == 2 else 0.5
        h = self.eta - self.bed
        wet = h > H_DRY
        if not wet.any():
            return 0.0
        c = np.sqrt(np.maximum(G * h, 0.0))
        hh = np.where(wet, h, 1.0)
        u = np.where(wet, self.hu / hh, 0.0)
        v = np.where(wet, self.hv / hh, 0.0)
        dry = (~wet) | (h <= H_DRY)
        dry_l = np.zeros_like(wet)
        dry_l[:, 1:] = dry[:, :-1]
        dry_r = np.zeros_like(wet)
        dry_r[:, :-1] = dry[:, 1:]
        dry_d = np.zeros_like(wet)
        dry_d[1:, :] = dry[:-1, :]
        dry_u = np.zeros_like(wet)
        dry_u[:-1, :] = dry[1:, :]
        # domain walls are reflective, not dry — their mirrored-ghost speeds stay <= |u|+c
        cx = np.where(dry_l | dry_r, np.abs(u) + 2.0 * c, np.abs(u) + c)
        cy = np.where(dry_d | dry_u, np.abs(v) + 2.0 * c, np.abs(v) + c)
        rate = np.where(wet, cx / self.geom["dx"] + cy / self.geom["dy"], 0.0)
        max_rate = max(float(rate.max()), 1e-9)
        return cfl / max_rate

    def _euler_stage(self, dt: float, eta_in, hu_in, hv_in, eta_out, hu_out, hv_out) -> None:
        bed = self.bed
        nx, ny = self.nx, self.ny
        dxg = self.geom["dx"]
        dyg = self.geom["dy"]
        area = self.geom["area"]
        second = self.order == 2

        slope_xe = slope_xu = slope_xv = slope_ye = slope_yu = slope_yv = None
        if second:
            slope_xe, slope_ye = _slopes(eta_in)
            slope_xu, slope_yu = _slopes(hu_in)
            slope_xv, slope_yv = _slopes(hv_in)

        np.copyto(eta_out, eta_in)
        np.copyto(hu_out, hu_in)
        np.copyto(hv_out, hv_in)

        # ---------------- x sweep (normal = u, along longitude) ----------------
        if nx >= 2:
            bL, bR = bed[:, :-1], bed[:, 1:]
            eL, eR = eta_in[:, :-1].copy(), eta_in[:, 1:].copy()
            pL, pR = hu_in[:, :-1].copy(), hu_in[:, 1:].copy()
            qL, qR = hv_in[:, :-1].copy(), hv_in[:, 1:].copy()
            b_max = np.maximum(bL, bR)
            if second:
                hLc, hRc = eL - bL, eR - bR
                safe = ((hLc > H_MUSCL) & (hRc > H_MUSCL)
                        & ((eL - b_max) > H_MUSCL) & ((eR - b_max) > H_MUSCL))
                eL += np.where(safe, 0.5 * slope_xe[:, :-1], 0.0)
                pL += np.where(safe, 0.5 * slope_xu[:, :-1], 0.0)
                qL += np.where(safe, 0.5 * slope_xv[:, :-1], 0.0)
                eR -= np.where(safe, 0.5 * slope_xe[:, 1:], 0.0)
                pR -= np.where(safe, 0.5 * slope_xu[:, 1:], 0.0)
                qR -= np.where(safe, 0.5 * slope_xv[:, 1:], 0.0)
            hs_l = np.maximum(eL - b_max, 0.0)
            hs_r = np.maximum(eR - b_max, 0.0)
            d_l, d_r = eL - bL, eR - bR
            uL = np.where((d_l > H_DRY) & (hs_l > 0), (pL * d_l) / (d_l * d_l + H_VEL2), 0.0)
            vL = np.where((d_l > H_DRY) & (hs_l > 0), (qL * d_l) / (d_l * d_l + H_VEL2), 0.0)
            uR = np.where((d_r > H_DRY) & (hs_r > 0), (pR * d_r) / (d_r * d_r + H_VEL2), 0.0)
            vR = np.where((d_r > H_DRY) & (hs_r > 0), (qR * d_r) / (d_r * d_r + H_VEL2), 0.0)
            f0, f1, f2 = _hll_flux(hs_l, uL, vL, hs_r, uR, vR)
            # Audusse side corrections use the CELL-SIDE (reconstructed) depth in the cell's own
            # bed frame (equals the cell-centre depth when flattened / order 1).
            h_l0 = np.maximum(eL - bL, 0.0)
            h_r0 = np.maximum(eR - bR, 0.0)
            fx_l1 = f1 + 0.5 * G * (h_l0 * h_l0 - hs_l * hs_l)
            fx_r1 = f1 + 0.5 * G * (h_r0 * h_r0 - hs_r * hs_r)
            cx = (dt * dyg) / area
            eta_out[:, :-1] -= cx * f0
            hu_out[:, :-1] -= cx * fx_l1
            hv_out[:, :-1] -= cx * f2
            eta_out[:, 1:] += cx * f0
            hu_out[:, 1:] += cx * fx_r1
            hv_out[:, 1:] += cx * f2

        # x domain edges: reflective walls (mirrored ghost, u -> −u)
        if nx >= 1:
            for edge, sign in ((0, +1.0), (nx - 1, -1.0)):
                b = bed[:, edge]
                e = eta_in[:, edge]
                hs = np.maximum(e - b, 0.0)
                d = e - b
                wet = (hs > H_DRY) & (d > H_DRY)
                dd = np.where(wet, d, 1.0)
                u = np.where(wet, hu_in[:, edge] / dd, 0.0)
                v = np.where(wet, hv_in[:, edge] / dd, 0.0)
                h_ghost = hs
                if sign > 0:      # west wall: ghost on the left mirrors −u
                    f0, f1, f2 = _hll_flux(h_ghost, -u, v, hs, u, v)
                else:             # east wall: ghost on the right mirrors −u
                    f0, f1, f2 = _hll_flux(hs, u, v, h_ghost, -u, v)
                h0 = np.maximum(e - b, 0.0)
                fw = f1 + 0.5 * G * (h0 * h0 - hs * hs)
                cx = (dt * dyg) / area
                hu_out[:, edge] += sign * cx * fw
                hv_out[:, edge] += sign * cx * f2

        # ---------------- y sweep (normal = v, along latitude) ----------------
        if ny >= 2:
            bD, bU = bed[:-1, :], bed[1:, :]
            eD, eU = eta_in[:-1, :].copy(), eta_in[1:, :].copy()
            pD, pU = hu_in[:-1, :].copy(), hu_in[1:, :].copy()
            qD, qU = hv_in[:-1, :].copy(), hv_in[1:, :].copy()
            b_max = np.maximum(bD, bU)
            if second:
                hDc, hUc = eD - bD, eU - bU
                safe = ((hDc > H_MUSCL) & (hUc > H_MUSCL)
                        & ((eD - b_max) > H_MUSCL) & ((eU - b_max) > H_MUSCL))
                eD += np.where(safe, 0.5 * slope_ye[:-1, :], 0.0)
                pD += np.where(safe, 0.5 * slope_yu[:-1, :], 0.0)
                qD += np.where(safe, 0.5 * slope_yv[:-1, :], 0.0)
                eU -= np.where(safe, 0.5 * slope_ye[1:, :], 0.0)
                pU -= np.where(safe, 0.5 * slope_yu[1:, :], 0.0)
                qU -= np.where(safe, 0.5 * slope_yv[1:, :], 0.0)
            hs_d = np.maximum(eD - b_max, 0.0)
            hs_u = np.maximum(eU - b_max, 0.0)
            d_d, d_u = eD - bD, eU - bU
            vD = np.where((d_d > H_DRY) & (hs_d > 0), (qD * d_d) / (d_d * d_d + H_VEL2), 0.0)
            uD = np.where((d_d > H_DRY) & (hs_d > 0), (pD * d_d) / (d_d * d_d + H_VEL2), 0.0)
            vU = np.where((d_u > H_DRY) & (hs_u > 0), (qU * d_u) / (d_u * d_u + H_VEL2), 0.0)
            uU = np.where((d_u > H_DRY) & (hs_u > 0), (pU * d_u) / (d_u * d_u + H_VEL2), 0.0)
            # normal direction is y: pass v as the normal velocity
            f0, f1, f2 = _hll_flux(hs_d, vD, uD, hs_u, vU, uU)
            h_d0 = np.maximum(eD - bD, 0.0)
            h_u0 = np.maximum(eU - bU, 0.0)
            fy_d1 = f2                                              # tangential (x) momentum
            fy_d2 = f1 + 0.5 * G * (h_d0 * h_d0 - hs_d * hs_d)
            fy_u1 = f2
            fy_u2 = f1 + 0.5 * G * (h_u0 * h_u0 - hs_u * hs_u)
            # constant tangent-plane widths: the interface width = dx for every row
            fac_d = (dt * dxg) / area
            eta_out[:-1, :] -= fac_d * f0
            hu_out[:-1, :] -= fac_d * fy_d1
            hv_out[:-1, :] -= fac_d * fy_d2
            eta_out[1:, :] += fac_d * f0
            hu_out[1:, :] += fac_d * fy_u1
            hv_out[1:, :] += fac_d * fy_u2

        # y domain edges: reflective walls (v -> −v), edge length = the boundary row's own width
        if ny >= 1:
            for edge, sign in ((0, +1.0), (ny - 1, -1.0)):
                b = bed[edge, :]
                e = eta_in[edge, :]
                hs = np.maximum(e - b, 0.0)
                d = e - b
                wet = (hs > H_DRY) & (d > H_DRY)
                dd = np.where(wet, d, 1.0)
                u = np.where(wet, hu_in[edge, :] / dd, 0.0)
                v = np.where(wet, hv_in[edge, :] / dd, 0.0)
                h_ghost = hs
                if sign > 0:      # south wall: ghost below mirrors −v
                    f0, f1, f2 = _hll_flux(h_ghost, -v, u, hs, v, u)
                else:             # north wall: ghost above mirrors −v
                    f0, f1, f2 = _hll_flux(hs, v, u, h_ghost, -v, u)
                h0 = np.maximum(e - b, 0.0)
                fw = f1 + 0.5 * G * (h0 * h0 - hs * hs)
                cy = (dt * dxg) / area
                hv_out[edge, :] += sign * cy * fw
                hu_out[edge, :] += sign * cy * f2

        # ---------------- friction, wet/dry, sponge ----------------
        h = eta_out - bed
        neg = h < 0
        if neg.any():
            self.n_neg_clamped += int(neg.sum())
            eta_out = np.where(neg, bed, eta_out)
            h = eta_out - bed
        thin = (h <= H_VEL) & (h >= 0)
        if thin.any():
            eta_out = np.where(thin, np.where(h > 0, bed + h, bed), eta_out)
            hu_out = np.where(thin, 0.0, hu_out)
            hv_out = np.where(thin, 0.0, hv_out)
            h = eta_out - bed
        if self.manning_n > 0:
            hh = np.where(h > H_VEL, h, 1.0)
            c_f = (dt * G * self.manning_n * self.manning_n) / np.maximum(h, 1e-6) ** (4.0 / 3.0)

            def fric(u_star):
                a = np.abs(u_star)
                out = (-1.0 + np.sqrt(1.0 + 4.0 * c_f * a)) / (2.0 * np.maximum(c_f, 1e-12))
                return np.where(u_star < 0, -out, out)

            apply = h > H_VEL
            hu_out = np.where(apply, h * fric(hu_out / hh), hu_out)
            hv_out = np.where(apply, h * fric(hv_out / hh), hv_out)
        sf = self.sponge
        if sf.any():
            keep = 1.0 - sf
            hu_out = hu_out * keep
            hv_out = hv_out * keep
            eta_out = self.eta0_ref + (eta_out - self.eta0_ref) * keep
            eta_out = np.maximum(eta_out, bed)

        # write back (stages use scratch arrays; the caller assigns)
        self._out = (eta_out, hu_out, hv_out)

    # ------------------------------------------------------------------ public
    def advance(self, n_steps: int, t_end: float) -> int:
        """Advance up to n_steps steps (or until t_end). Returns steps taken."""
        count = 0
        s1e = np.empty_like(self.eta); s1u = np.empty_like(self.hu); s1v = np.empty_like(self.hv)
        s2e = np.empty_like(self.eta); s2u = np.empty_like(self.hu); s2v = np.empty_like(self.hv)
        while count < n_steps and self.t < t_end:
            dt = self._compute_dt()
            if not math.isfinite(dt) or dt <= 0:
                break
            if self.t + dt > t_end:
                dt = t_end - self.t
            self.min_dt = min(self.min_dt, dt)
            self.max_dt = max(self.max_dt, dt)

            self._euler_stage(dt, self.eta, self.hu, self.hv, s1e, s1u, s1v)
            e1, u1, v1 = self._out
            if self.order == 1:
                np.copyto(self.eta, e1)
                np.copyto(self.hu, u1)
                np.copyto(self.hv, v1)
            else:
                # SSP-RK2 (Heun): U¹ = E(Uⁿ); Uⁿ⁺¹ = ½Uⁿ + ½E(U¹).
                self._euler_stage(dt, e1, u1, v1, s2e, s2u, s2v)
                e2, u2, v2 = self._out
                np.copyto(self.eta, 0.5 * self.eta + 0.5 * e2)
                np.copyto(self.hu, 0.5 * self.hu + 0.5 * u2)
                np.copyto(self.hv, 0.5 * self.hv + 0.5 * v2)

            self.t += dt
            self.n_steps += 1
            count += 1
            self._record_fields()
            if self.t >= self.next_gauge_t:
                for g in self.gauges:
                    g["t_min"].append(self.t / 60.0)
                    g["eta"].append(self.eta[g["j"], g["i"]] if g["j"] >= 0 else math.nan)
                self.next_gauge_t = self.t + self.gauge_dt
        return count

    def _record_fields(self) -> None:
        h = self.eta - self.bed
        wet = h > H_DRY
        a = np.abs(self.eta)
        np.maximum(self.max_eta, np.where(wet, a, self.max_eta), out=self.max_eta)
        newly = wet & (a > self.arrival_threshold) & np.isnan(self.arrival)
        self.arrival[newly] = self.t
        land = self.bed > 0
        np.maximum(self.max_flow_depth, np.where(wet & land, h, self.max_flow_depth),
                   out=self.max_flow_depth)
        hh = np.where(wet, h, 1.0)
        spd = np.hypot(np.where(wet, self.hu / hh, 0.0), np.where(wet, self.hv / hh, 0.0))
        self.max_vel = max(self.max_vel, float(spd.max()))

    def finish(self) -> dict:
        h = self.eta - self.bed
        mass = float(h[h > 0].sum() * self.geom["area"])
        return {
            "diagnostics": {
                "n_steps": self.n_steps,
                "t_sim": self.t,
                "min_dt": 0.0 if math.isinf(self.min_dt) else self.min_dt,
                "max_dt": self.max_dt,
                "mass_initial": self.mass0,
                "mass_final": mass,
                "mass_drift_relative": (abs(mass - self.mass0) / self.mass0) if self.mass0 > 0 else 0.0,
                "max_eta_peak": float(self.max_eta.max()),
                "max_velocity": self.max_vel,
                "n_neg_clamped": self.n_neg_clamped,
            },
            "fields": {"max_eta": self.max_eta, "arrival": self.arrival,
                       "max_flow_depth": self.max_flow_depth},
            "gauges": [{"station_id": g["spec"].get("station_id"), "lat": g["spec"].get("lat"),
                        "lon": g["spec"].get("lon"), "t_min": g["t_min"], "eta": g["eta"]}
                       for g in self.gauges],
        }


__all__ = ["NsweSolver", "SimParams", "build_geometry"]
