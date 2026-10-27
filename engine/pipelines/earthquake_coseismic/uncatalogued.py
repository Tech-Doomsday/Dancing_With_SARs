"""3.01 earthquake_coseismic — uncatalogued-source branch (docs/IMPLEMENTATION_PLAN.md 8.6).

Trigger: a coherent deformation signal at or above the credible area and NO catalog
event in the window. One rectangular Okada source is fitted to a quadtree-subsampled
copy of the referenced LOS field with a bounded global optimizer.

v1 simplifications — all RECORDED in the returned dict and surfaced as evidence:
  * dip fixed at 90 deg (vertical fault); signed strike-slip dislocation (slip < 0 is
    dextral, slip > 0 sinistral — this covers both rake signs, 0 and +/-180);
  * slip is solved analytically per candidate geometry (the prediction is linear in slip), with a
    stress-drop cap on slip (|slip| <= STRAIN_DROP_MAX * min(L, W), ~15 MPa) rejects the
    small-patch / huge-slip degeneracy that otherwise "converges" on unphysical sources;
  * uniform elastic half-space, uniform slip, one rectangular patch.
Consequence: pure dip-slip / thrust-like uplift fields are NOT representable by this
branch and will (honestly) fail to converge. A failed fit never produces a candidate.

Referencing rule (docs 8.7): the prediction is referenced exactly like the measurement —
the median of the prediction over the SAME reference region is removed — otherwise the
fit would chase the elastic model's arbitrary datum.

Convergence (docs 8.6, interpretation recorded): weighted residual RMS < `rms_ratio` x
the weighted RMS of the fitted field, and no free parameter pinned to a bound. (The plan's
"robust sigma" of the field is the noise scale for a localised signal, which no model can
beat; the field RMS is the scale the fit is meant to explain.)

Nothing here is random except the optimizer, which is seeded (deterministic).
"""
from __future__ import annotations

import heapq
import math

import numpy as np

from engine.lib.okada import GeoFault, geo_fault_displacement_enu

from .lib_local import pearson

LOCATION_RADIUS_M = 4000.0     # search radius around the anomaly centroid
STRAIN_DROP_MAX = 5.0e-4      # slip / min(L, W); ~ 15 MPa stress drop at mu = 30 GPa

SIMPLIFICATIONS = (
    "dip fixed at 90 degrees (vertical rectangular source)",
    "strike fitted over [0, 180) degrees (the anomaly principal axis is reported as a diagnostic only)",
    "source location searched within 4 km of the anomaly centroid",
    "slip limited by a stress-drop cap: |slip| <= 5e-4 x min(length, width)",
    "signed strike-slip dislocation only (covers both rake signs)",
    "uniform slip on one rectangular patch in a uniform elastic half-space",
)

PARAM_NAMES = ("east_m", "north_m", "centroid_depth_m", "log10_length_m", "log10_width_m", "strike_deg")


def pca_strike_deg(orientation_deg):
    """Azimuth (clockwise from north, [0,180)) of the anomaly principal axis, or None."""
    if orientation_deg is None:
        return None
    return float((90.0 + float(orientation_deg)) % 180.0)


# --------------------------------------------------------------------------- #
# quadtree subsampling
# --------------------------------------------------------------------------- #
def quadtree_leaves(disp: np.ndarray, mask: np.ndarray, max_points: int, noise_m: float):
    """Best-first quadtree: split the cell holding the most unexplained variance.

    Returns (rows, cols, values, weights) — one point per leaf at the centroid of the
    valid pixels in the leaf, value = leaf mean, weight = valid pixel count. Splitting
    stops at `max_points` leaves, at single pixels, or when no cell's within-cell
    scatter exceeds half of `noise_m` (high-gradient cells get resolution, flat far field stays coarse).
    """
    h, w = disp.shape
    d = np.where(mask & np.isfinite(disp), disp, np.nan)
    valid = np.isfinite(d)

    def stats(r0, r1, c0, c1):
        sub = d[r0:r1, c0:c1]
        v = sub[np.isfinite(sub)]
        n = v.size
        if n == 0:
            return 0, 0.0
        return n, float(np.sum((v - v.mean()) ** 2))      # within-cell sum of squares

    heap: list = []
    leaves: list = []

    def push(r0, r1, c0, c1):
        n, ss = stats(r0, r1, c0, c1)
        if n == 0:
            return
        splittable = (r1 - r0 > 1) or (c1 - c0 > 1)
        scatter = math.sqrt(ss / n) if n else 0.0
        if splittable and scatter > 0.5 * noise_m:
            heapq.heappush(heap, (-ss, r0, r1, c0, c1))
        else:
            leaves.append((r0, r1, c0, c1))

    push(0, h, 0, w)
    while heap and len(heap) + len(leaves) < max_points:
        _, r0, r1, c0, c1 = heapq.heappop(heap)
        rm, cm = (r0 + r1) // 2, (c0 + c1) // 2
        rs = [(r0, rm), (rm, r1)] if r1 - r0 > 1 else [(r0, r1)]
        cs = [(c0, cm), (cm, c1)] if c1 - c0 > 1 else [(c0, c1)]
        for a0, a1 in rs:
            for b0, b1 in cs:
                push(a0, a1, b0, b1)
    leaves.extend((r0, r1, c0, c1) for _, r0, r1, c0, c1 in heap)

    rows, cols, vals, wts = [], [], [], []
    for r0, r1, c0, c1 in sorted(leaves):
        sub_v = valid[r0:r1, c0:c1]
        n = int(sub_v.sum())
        if n == 0:
            continue
        ii, jj = np.nonzero(sub_v)
        rows.append(r0 + ii.mean())
        cols.append(c0 + jj.mean())
        vals.append(float(np.nanmean(d[r0:r1, c0:c1])))
        wts.append(float(n))
    return np.array(rows), np.array(cols), np.array(vals), np.array(wts)


# --------------------------------------------------------------------------- #
# forward model (referenced like the measurement)
# --------------------------------------------------------------------------- #
def _predict_unit(theta6, east, north, los3, ref_east, ref_north, ref_los3):
    """Referenced toward-LOS prediction for UNIT (1 m, rake 0) slip at the data points.

    The prediction is linear in slip, and the median reference is homogeneous of degree 1,
    so slip is solved analytically per geometry (variable projection) instead of searched.
    Engine sign: d_enu . los_enu (ground->sensor), + = toward the sensor.
    """
    e0, n0, depth, log_l, log_w, strike_deg = theta6
    fault = GeoFault(strike_deg=strike_deg, dip_deg=90.0, rake_deg=0.0,
                     length_m=10.0 ** log_l, width_m=10.0 ** log_w, top_depth_m=0.0,
                     slip_m=1.0, centroid_lat=0.0, centroid_lon=0.0)
    x = np.concatenate([east, ref_east]) - e0
    y = np.concatenate([north, ref_north]) - n0
    d_e, d_n, d_u, singular = geo_fault_displacement_enu(fault, depth, x, y)
    los = np.concatenate([los3, ref_los3], axis=1)
    pred = d_e * los[0] + d_n * los[1] + d_u * los[2]
    pred = np.where(singular, np.nan, pred)
    n = east.size
    ref_vals = pred[n:]
    ref_vals = ref_vals[np.isfinite(ref_vals)]
    if ref_vals.size == 0:
        return None
    return pred[:n] - float(np.median(ref_vals))


def _best_slip(g, vals, wts, theta6):
    """Weighted least-squares slip for unit prediction g, clipped by the stress-drop cap."""
    denom = float(np.sum(wts * g * g))
    if not math.isfinite(denom) or denom <= 0.0:
        return None
    s = float(np.sum(wts * vals * g)) / denom
    cap = STRAIN_DROP_MAX * min(10.0 ** theta6[3], 10.0 ** theta6[4])
    return max(-cap, min(cap, s)), cap


class _Data:
    """Quadtree leaves + the fixed reference points, ready for repeated forward evaluation."""

    def __init__(self, disp, mask, gx, gy, los_enu, ref_mask, noise, max_points):
        rows, cols, vals, wts = quadtree_leaves(disp, mask, max_points, noise)
        ri, ci = np.rint(rows).astype(int), np.rint(cols).astype(int)
        self.n = int(rows.size)
        self.east, self.north, self.los3 = gx[ci], gy[ri], los_enu[:, ri, ci]
        self.vals, self.wts = vals, wts
        self.w_tot = float(wts.sum())
        self.field_rms = math.sqrt(float(np.sum(wts * vals * vals)) / self.w_tot) if self.n else 0.0
        # reference points: deterministic stride over the SAME reference region used in prep
        rr, rc = np.nonzero(ref_mask)
        stride = max(1, rr.size // 100)
        rr, rc = rr[::stride], rc[::stride]
        self.ref_east, self.ref_north, self.ref_los3 = gx[rc], gy[rr], los_enu[:, rr, rc]

    def residuals(self, theta6) -> np.ndarray:
        """sqrt(weight)-scaled residual vector (for least-squares refinement)."""
        bad = np.full(self.n, 1e3)
        g = _predict_unit(theta6, self.east, self.north, self.los3,
                          self.ref_east, self.ref_north, self.ref_los3)
        if g is None or not np.all(np.isfinite(g)):
            return bad
        bs = _best_slip(g, self.vals, self.wts, theta6)
        if bs is None:
            return bad
        return np.sqrt(self.wts / self.w_tot) * (self.vals - bs[0] * g)

    def objective(self, theta6) -> float:
        """Weighted RMS residual with slip solved analytically (clipped to the stress-drop cap)."""
        g = _predict_unit(theta6, self.east, self.north, self.los3,
                          self.ref_east, self.ref_north, self.ref_los3)
        if g is None or not np.all(np.isfinite(g)):
            return 1e3
        bs = _best_slip(g, self.vals, self.wts, theta6)
        if bs is None:
            return 1e3
        r = self.vals - bs[0] * g
        return float(np.sqrt(np.sum(self.wts * r * r) / self.w_tot))


def fit_uncatalogued(analysis, summary, epsg: int, params) -> dict:
    """Fit one vertical strike-slip source; always returns a dict (never raises on science)."""
    out: dict = {"attempted": False, "converged": False, "reason": None,
                 "simplifications": list(SIMPLIFICATIONS)}
    try:
        from scipy.optimize import differential_evolution, least_squares
    except Exception as ex:  # pragma: no cover - R7: scipy missing is a visible state
        out["reason"] = f"scipy is not available ({ex}); the uncatalogued branch cannot run"
        return out

    utm = (32601 <= epsg <= 32660) or (32701 <= epsg <= 32760)
    if not utm:
        out["reason"] = f"grid EPSG {epsg} is not WGS84 UTM; source fit refused rather than misplaced"
        return out
    if analysis.los_enu is None:
        out["reason"] = "no per-pixel LOS geometry (los_enu) in the bundle; cannot project a source model"
        return out

    disp = np.asarray(analysis.disp, dtype=np.float64)
    mask = np.asarray(analysis.mask, dtype=bool)
    gx = np.asarray(analysis.grid_x, dtype=np.float64)
    gy = np.asarray(analysis.grid_y, dtype=np.float64)
    los_enu = np.asarray(analysis.los_enu, dtype=np.float64)
    ref_mask = np.asarray(getattr(analysis, "reference_mask", mask), dtype=bool)

    # noise scale for the quadtree stopping rule: median effective sigma (never below 1 mm)
    sig = np.asarray(analysis.sigma_eff, dtype=np.float64)
    noise = float(np.nanmedian(sig[mask])) if np.isfinite(sig[mask]).any() else 0.0
    noise = max(noise, 1e-3)

    fine = _Data(disp, mask, gx, gy, los_enu, ref_mask, noise, int(params.uncat_max_points))
    if fine.n < 12:
        out["reason"] = f"quadtree produced only {fine.n} points; too few to constrain a source"
        return out
    if fine.field_rms <= 0:
        out["reason"] = "fitted field has zero amplitude"
        return out

    # PCA strike = diagnostic only. Orientation is measured in (col,row) pixel space (rows increase
    # SOUTH), i.e. clockwise from east on the map => azimuth clockwise from north = 90 + theta.
    pca_strike = pca_strike_deg(summary.anomaly_orientation_deg)

    span_x, span_y = float(gx.max() - gx.min()), float(gy.max() - gy.min())
    # Source location is searched within +-LOCATION_RADIUS_M of the |disp|-weighted anomaly centroid
    # (detection already computes it; centroid is in pixel (col, row) space), clipped to the padded tile.
    box_e = (float(gx.min() - 0.25 * span_x), float(gx.max() + 0.25 * span_x))
    box_n = (float(gy.min() - 0.25 * span_y), float(gy.max() + 0.25 * span_y))
    if summary.anomaly_centroid is not None and len(gx) > 1 and len(gy) > 1:
        cx_pix, cy_pix = summary.anomaly_centroid
        c_e = float(gx[0] + cx_pix * (gx[1] - gx[0]))
        c_n = float(gy[0] + cy_pix * (gy[1] - gy[0]))
        box_e = (max(box_e[0], c_e - LOCATION_RADIUS_M), min(box_e[1], c_e + LOCATION_RADIUS_M))
        box_n = (max(box_n[0], c_n - LOCATION_RADIUS_M), min(box_n[1], c_n + LOCATION_RADIUS_M))
    bounds = [
        box_e,                                                                # east
        box_n,                                                                # north
        (1000.0, 20000.0),                                                    # centroid depth
        (2.7, 5.0),                                                           # log10 length: 0.5 .. 100 km
        (2.7, 4.7),                                                           # log10 width : 0.5 .. 50 km
        (-45.0, 225.0),                                                       # strike (180-periodic; slack avoids edge-pinning)
    ]
    lo = np.array([b[0] for b in bounds])
    hi = np.array([b[1] for b in bounds])

    # Strike makes the landscape multimodal (a single 6-D global search stalls in wrong basins even
    # at large budgets, and per-node DE capture is stochastic), so:
    #   (1) scan strike on a coarse circular grid; per node a short 5-D global search;
    #   (2) refine the best candidates locally over all six parameters.
    # (Evaluation cost is dominated by per-call overhead, not point count, so a coarser quadtree for
    #  the scan does not pay; reliability comes from the strike grid and the number of refined candidates.)
    n_strikes = max(1, int(params.uncat_scan_strikes))
    strikes = [180.0 * k / n_strikes for k in range(n_strikes)]
    cands: list = []
    nfev = 0
    for st in strikes:
        for k in range(max(1, int(params.uncat_scan_seeds))):
            r = differential_evolution(
                lambda th5, st=st: fine.objective(np.append(th5, st)), bounds[:5],
                seed=int(params.uncat_seed) + 7919 * k, maxiter=int(params.uncat_scan_iter),
                popsize=int(params.uncat_scan_popsize), tol=1e-4, mutation=(0.4, 1.0),
                recombination=0.8, polish=False, init="sobol", updating="immediate", workers=1)
            nfev += int(r.nfev)
            cands.append((float(r.fun), np.append(r.x, st)))
    cands.sort(key=lambda c: c[0])

    def unit_residuals(u):
        return fine.residuals(lo + np.clip(u, 0.0, 1.0) * (hi - lo))

    theta, rms = None, math.inf
    for _, x0 in cands[: max(1, int(params.uncat_refine_top))]:
        x0 = np.asarray(x0, dtype=float)
        u0 = np.clip((x0 - lo) / (hi - lo), 0.0, 1.0)
        try:
            ls = least_squares(unit_residuals, u0, bounds=(0.0, 1.0), method="trf", diff_step=1e-3,
                               x_scale=0.05, max_nfev=int(params.uncat_refine_nfev))
            nfev += int(ls.nfev) * 7
            cand = lo + np.clip(ls.x, 0.0, 1.0) * (hi - lo)
        except Exception:                                  # refinement is an optimisation aid only
            cand = x0
        val = float(fine.objective(cand))
        if val < rms:
            rms, theta = val, cand
    theta = np.asarray(theta, dtype=float)

    g = _predict_unit(theta, fine.east, fine.north, fine.los3, fine.ref_east, fine.ref_north, fine.ref_los3)
    bs = _best_slip(g, fine.vals, fine.wts, theta) if g is not None else None
    out["attempted"] = True
    if bs is None:
        out["reason"] = "optimizer returned a degenerate source (zero predicted amplitude)"
        return out
    slip, cap = bs

    # not pinned to a bound (within 1 % of the range); strike is circular so it is exempt
    pinned = [PARAM_NAMES[i] for i, (b_lo, b_hi) in enumerate(bounds[:5])
              if min(theta[i] - b_lo, b_hi - theta[i]) < 0.01 * (b_hi - b_lo)]
    if abs(slip) >= 0.999 * cap:
        pinned.append("slip_m (at stress-drop cap)")
    ratio_rms = rms / fine.field_rms
    converged = bool(ratio_rms < params.uncat_rms_ratio and not pinned and abs(slip) > 1e-4)

    # full-grid prediction (residual layer + equivalents), referenced identically
    GX, GY = np.meshgrid(gx, gy)
    full = _predict_unit(theta, GX.ravel(), GY.ravel(), los_enu.reshape(3, -1),
                         fine.ref_east, fine.ref_north, fine.ref_los3)
    pred_grid = None if full is None else (slip * full).reshape(disp.shape)
    corr = math.nan
    if pred_grid is not None:
        ok = mask & np.isfinite(pred_grid) & np.isfinite(disp)
        corr = pearson(pred_grid[ok], disp[ok]) if ok.any() else math.nan

    q = max(0.0, min(1.0, 1.0 - ratio_rms))       # residual-based equivalent of s_amp / s_corr
    out.update({
        "converged": converged,
        "reason": None if converged else (
            f"best fit did not explain the field (residual RMS {rms * 1000:.1f} mm = "
            f"{ratio_rms:.2f} x field RMS {fine.field_rms * 1000:.1f} mm; need < {params.uncat_rms_ratio})"
            if ratio_rms >= params.uncat_rms_ratio else
            "best fit has a parameter pinned to its bound: " + ", ".join(pinned or ["slip ~ 0"])),
        "parameters": {
            "east_m": float(theta[0]), "north_m": float(theta[1]),
            "centroid_depth_m": float(theta[2]),
            "length_m": float(10.0 ** theta[3]), "width_m": float(10.0 ** theta[4]),
            "slip_m": float(slip), "strike_deg": float(theta[5] % 180.0), "dip_deg": 90.0,
            "sense": "dextral" if slip < 0 else "sinistral",
        },
        "epsg": int(epsg),
        "rms_m": rms, "field_rms_m": fine.field_rms, "rms_ratio": float(ratio_rms),
        "pattern_correlation": (None if math.isnan(corr) else float(corr)),
        "quality": float(q),
        "pinned_parameters": pinned,
        "anomaly_pca_strike_deg": pca_strike,
        "quadtree_points": fine.n, "reference_points": int(fine.ref_east.size),
        "optimizer": {"name": "strike scan (scipy differential_evolution per strike, multi-seed) + least-squares refinement",
                      "seed": int(params.uncat_seed), "strikes_scanned": len(strikes),
                      "seeds_per_strike": int(params.uncat_scan_seeds), "evaluations": int(nfev)},
        "weakly_constrained": ["centroid_depth_m", "width_m", "slip_m"],
        "_prediction": pred_grid,
    })
    return out
