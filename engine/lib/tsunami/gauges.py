"""Gauge/observation science: de-tiding, k·σ tsunami detection, screening, evidence levels.

Ported from the app's detection.ts + evidence.ts (same numbers, same honesty contract).

Detection method (documented; parameters configurable, defaults justified inline):
  1. De-tiding: local quadratic regression over a ±3 h window centred on each sample — the
     astronomical tide (period ≈ 12.4/24 h) is locally well-approximated by a quadratic over 6 h
     (residual tide leak ≪ 1 cm); also removes instrumental drift. Replaces the UTide/FES2014
     harmonic approach because the Copernicus FES2014 service requires an account (documented
     deviation). ROBUST two-pass: samples whose pass-1 residual exceeds 3× the window's robust
     (MAD) scale are excluded and the fit redone — the tsunami itself must not bias the fit.
  2. Noise scale: σ from the residual over [origin−6 h, origin−10 min], MAD-based (a seismic-phase
     excursion inside the window would inflate an RMS and push the threshold above the excursion).
  3. Trigger: first sample after origin−15 min with |residual| > k·σ (default k = 5). Excursions
     within the seismic-phase mask (default 8 min after origin, extendable by the eikonal ray
     time) are flagged as caveats and never reported as the tsunami arrival.
  4. Amplitude: max |residual| within [trigger, trigger+3 h].
  5. Period: dominant period from mean zero-crossing spacing in the first hour after the trigger.

Honesty contract: a trigger is a DETECTION of an anomalous wave, not proof of a tsunami — causal
attribution happens in classify_evidence, separately. Missing evidence is never evidence of
absence. Buoy agreement is CORRELATED evidence (all stations sample the same wave field).
"""
from __future__ import annotations

import math

import numpy as np

MS_H = 3_600_000.0
MS_MIN = 60_000.0


class DetectOptions:
    """k in the k·σ trigger; de-tide half-window; noise window; masks (see module docstring)."""

    def __init__(self, k: float = 5.0, detide_half_window_h: float = 3.0,
                 noise_window_h: tuple = (6.0, 1.0 / 6.0), amplitude_window_h: float = 3.0,
                 min_noise_samples: int = 8, seismic_mask_min: float = 8.0,
                 mask_until_min: float | None = None):
        self.k = k
        self.detide_half_window_h = detide_half_window_h
        self.noise_window_h = noise_window_h
        self.amplitude_window_h = amplitude_window_h
        self.min_noise_samples = min_noise_samples
        self.seismic_mask_min = seismic_mask_min
        self.mask_until_min = mask_until_min

    @classmethod
    def from_mapping(cls, m: dict | None) -> "DetectOptions":
        m = dict(m or {})
        opts = cls()
        for k, v in m.items():
            if hasattr(opts, k) and v is not None:
                setattr(opts, k, v)
        return opts


DEFAULT_DETECT_OPTIONS = DetectOptions()


def time_series_from_lists(t, v) -> dict:
    """Adapter payload -> {"t": float64 epoch ms ascending, "v": float64 metres}."""
    t = np.asarray(t, dtype=np.float64)
    v = np.asarray(v, dtype=np.float64)
    return {"t": t, "v": v}


# ------------------------------------------------------------------ #
# de-tiding
# ------------------------------------------------------------------ #
def _fit_quad(t: np.ndarray, v: np.ndarray, m: int, lo: int, hi: int, use: np.ndarray | None):
    """Weighted quadratic fit over [lo, hi] centred on m (x in hours); None when degenerate."""
    idx = np.arange(lo, hi + 1)
    if use is not None:
        idx = idx[use[idx]]
    if idx.size < 3:
        return None
    x = (t[idx] - t[m]) / MS_H
    y = v[idx]
    A = np.stack([np.ones_like(x), x, x * x], axis=1)
    try:
        coeffs, *_ = np.linalg.lstsq(A, y, rcond=None)
    except np.linalg.LinAlgError:
        return None
    if not np.all(np.isfinite(coeffs)):
        return None
    return coeffs


def local_quadratic_detide(t: np.ndarray, v: np.ndarray, half_window_h: float) -> dict:
    """Robust two-pass local quadratic de-tiding -> {"t", "resid"} (see module docstring)."""
    t = np.asarray(t, dtype=np.float64)
    v = np.asarray(v, dtype=np.float64)
    n = t.size
    out_t, out_v = [], []
    hw_ms = half_window_h * MS_H

    lo = 0
    hi = 0
    for m in range(n):
        while lo < m and t[m] - t[lo] > hw_ms:
            lo += 1
        if hi < m:
            hi = m
        while hi + 1 < n and t[hi + 1] - t[m] <= hw_ms:
            hi += 1
        if hi - lo + 1 < 3 or t[hi] - t[lo] < 0.5 * hw_ms:
            continue

        c1 = _fit_quad(t, v, m, lo, hi, None)
        if c1 is None:
            continue
        idx = np.arange(lo, hi + 1)
        x = (t[idx] - t[m]) / MS_H
        resid = np.abs(v[idx] - (c1[0] + c1[1] * x + c1[2] * x * x))
        mad = float(np.median(resid))
        rob_sig = 1.4826 * mad
        use = np.ones(n, dtype=bool)
        outliers = resid > 3.0 * max(rob_sig, 1e-4)
        n_out = int(outliers.sum())
        if n_out:
            use[idx[outliers]] = False
        coeffs = c1
        if n_out and (idx.size - n_out) >= 3:
            c2 = _fit_quad(t, v, m, lo, hi, use)
            if c2 is not None:
                coeffs = c2
        out_t.append(t[m])
        out_v.append(v[m] - coeffs[0])
    return {"t": np.asarray(out_t, dtype=np.float64), "resid": np.asarray(out_v, dtype=np.float64)}


# ------------------------------------------------------------------ #
# detection
# ------------------------------------------------------------------ #
def _dominant_period_min(t: np.ndarray, r: np.ndarray, from_ms: float, to_ms: float) -> float | None:
    """Mean zero-crossing period (minutes) in a residual segment."""
    gaps: list = []
    last_cross = math.nan
    for m in range(t.size):
        if t[m] < from_ms or t[m] > to_ms:
            continue
        if m == 0:
            last_cross = t[m]
            continue
        if t[m - 1] < from_ms:
            continue
        if (r[m - 1] <= 0 < r[m]) or (r[m - 1] >= 0 > r[m]):
            if math.isfinite(last_cross) and t[m] > last_cross:
                gaps.append(t[m] - last_cross)
            last_cross = t[m]
    if len(gaps) < 2:
        return None
    gaps.sort()
    return 2.0 * gaps[len(gaps) // 2] / MS_MIN


def detect_tsunami_signal(station_id: str, series: dict, origin_ms: float,
                          provenance: dict | None = None,
                          opts: DetectOptions | None = None) -> dict:
    """Full detection for one station given its record and the event origin.

    Returns an explicit availability state when the data cannot support a decision — never a
    silent "no tsunami".
    """
    opts = opts or DEFAULT_DETECT_OPTIONS
    caveats: list = []
    t_all = np.asarray(series["t"], dtype=np.float64)
    v_all = np.asarray(series["v"], dtype=np.float64)

    win_start = origin_ms - 8 * MS_H
    win_end = origin_ms + 8 * MS_H
    idx = np.where((t_all >= win_start) & (t_all <= win_end))[0]

    def insufficient(reason: str) -> dict:
        return {"station_id": station_id, "provenance": provenance or {},
                "t": np.empty(0), "resid": np.empty(0), "sigma_noise": math.nan,
                "trigger_ms": None, "arrival_min": None, "pre_origin_excursion": False,
                "max_amplitude_m": None, "dominant_period_min": None,
                "n_samples": int(idx.size), "caveats": [reason],
                "availability": {"state": "missing", "reason": reason}}

    if idx.size < opts.min_noise_samples + 10:
        return insufficient(
            f"only {idx.size} samples cover ±8 h of the origin — the record does not cover the event window")

    t = t_all[idx]
    v = v_all[idx]

    # pre-event coverage check: need noise-window samples
    noise_count = int((((origin_ms - t) >= opts.noise_window_h[1] * MS_H)
                       & ((origin_ms - t) <= opts.noise_window_h[0] * MS_H)).sum())
    if noise_count < opts.min_noise_samples:
        return insufficient(
            f"only {noise_count} pre-event samples in the noise window — no baseline to define the noise scale")

    # gap detection (cadence jumps)
    dts = np.diff(t)
    cadence = float(dts[dts > 0].min()) if (dts > 0).any() else math.inf
    max_gap = float(dts.max()) if dts.size else 0.0
    if max_gap > 4 * cadence:
        caveats.append(f"data gap of {round(max_gap / MS_MIN)} min inside the window "
                       f"(nominal cadence {round(cadence / MS_MIN)} min)")

    detided = local_quadratic_detide(t, v, opts.detide_half_window_h)
    dt_, resid = detided["t"], detided["resid"]

    # noise sigma over [origin−6h, origin−10min], ROBUST (MAD-based)
    d = origin_ms - dt_
    noise_mask = (d >= opts.noise_window_h[1] * MS_H) & (d <= opts.noise_window_h[0] * MS_H)
    noise_vals = np.abs(resid[noise_mask])
    if noise_vals.size < opts.min_noise_samples:
        return insufficient(f"de-tided series has only {noise_vals.size} noise-window samples")
    sigma = 1.4826 * float(np.median(noise_vals))
    threshold = opts.k * sigma

    # trigger search from origin−15min, excluding the seismic-phase mask
    search_start = origin_ms - 15 * MS_MIN
    mask_min = max(opts.seismic_mask_min, opts.mask_until_min or 0.0)
    mask_end = origin_ms + mask_min * MS_MIN
    trigger_ms = None
    seismic_phase = False
    masked_trigger_min = None
    for m in range(dt_.size):
        if dt_[m] < search_start:
            continue
        if abs(resid[m]) > threshold:
            if origin_ms <= dt_[m] < mask_end:
                if masked_trigger_min is None:
                    masked_trigger_min = (dt_[m] - origin_ms) / MS_MIN
                seismic_phase = True
                continue            # keep searching for the water wave
            trigger_ms = dt_[m]
            break
    if seismic_phase:
        caveats.append(
            (f"excitation at +{masked_trigger_min:.0f} min excluded: a water wave cannot arrive before "
             f"~85% of the eikonal ray time ({opts.mask_until_min:.0f} min) — seismic phase on the pressure record"
             if masked_trigger_min is not None and (opts.mask_until_min or 0.0) > opts.seismic_mask_min
            else f"excitation within {opts.seismic_mask_min} min of the origin excluded from the arrival "
                 "(seismic phase on the pressure record, not the water wave)"))
    pre_origin = bool(trigger_ms is not None and trigger_ms < origin_ms)

    max_amp = None
    period = None
    if trigger_ms is not None:
        t_end = trigger_ms + opts.amplitude_window_h * MS_H
        seg = np.abs(resid[(dt_ >= trigger_ms) & (dt_ <= t_end)])
        max_amp = float(seg.max()) if seg.size else None
        period = _dominant_period_min(dt_, resid, trigger_ms, trigger_ms + MS_H)
        if pre_origin:
            caveats.append("trigger precedes the origin — likely the seismic phase on the pressure sensor, not the water wave")

    prov = dict(provenance or {})
    prov.setdefault("processing", [])
    prov["processing"] = list(prov["processing"]) + [
        f"local quadratic de-tiding (±{opts.detide_half_window_h} h window)",
        f"noise σ = {sigma * 100:.2f} cm (robust MAD estimate) from {int(noise_vals.size)} pre-event samples",
        f"trigger rule |residual| > {opts.k}·σ = {threshold * 100:.2f} cm from origin−15 min",
    ]
    return {"station_id": station_id, "provenance": prov, "t": dt_, "resid": resid,
            "sigma_noise": sigma, "trigger_ms": trigger_ms,
            "arrival_min": ((trigger_ms - origin_ms) / MS_MIN) if trigger_ms is not None else None,
            "pre_origin_excursion": pre_origin, "max_amplitude_m": max_amp,
            "dominant_period_min": period, "n_samples": int(dt_.size),
            "caveats": caveats, "availability": {"state": "available"}}


# ------------------------------------------------------------------ #
# screening + evidence classification (ported evidence.ts)
# ------------------------------------------------------------------ #
class ScreeningThresholds:
    def __init__(self, min_magnitude: float = 6.5, max_depth_km: float = 50.0,
                 min_dip_slip_fraction: float = 0.3):
        self.min_magnitude = min_magnitude
        self.max_depth_km = max_depth_km
        self.min_dip_slip_fraction = min_dip_slip_fraction


def screen_seismic(magnitude: float, depth_km: float, rake_deg: float | None,
                   th: ScreeningThresholds | None = None) -> dict:
    """Seismic screening — a heuristic, never a verdict (order-of-magnitude screening values)."""
    th = th or ScreeningThresholds()
    reasons, notes = [], []
    mag_ok = magnitude >= th.min_magnitude
    depth_ok = depth_km <= th.max_depth_km
    dip_slip = abs(math.sin(math.radians(rake_deg))) if rake_deg is not None else None
    mech_ok = (dip_slip is not None) and dip_slip >= th.min_dip_slip_fraction

    if mag_ok:
        reasons.append(f"magnitude M {magnitude:.1f} ≥ {th.min_magnitude}")
    else:
        notes.append(f"magnitude M {magnitude:.1f} below the {th.min_magnitude} screening value")
    if depth_ok:
        reasons.append(f"hypocentre depth {depth_km:.0f} km ≤ {th.max_depth_km} km")
    else:
        notes.append(f"hypocentre depth {depth_km:.0f} km deeper than the {th.max_depth_km} km screening value")
    if rake_deg is not None:
        mech = ("predominantly dip-slip" if dip_slip >= 0.7
                else "oblique (mixed strike- and dip-slip)" if dip_slip >= 0.3
                else "predominantly strike-slip")
        if mech_ok:
            reasons.append(f"{mech} mechanism (rake {rake_deg:.0f}°, dip-slip fraction |sin rake| = {dip_slip:.2f})")
        else:
            notes.append(f"predominantly strike-slip mechanism (rake {rake_deg:.0f}°) — vertical seafloor "
                         "displacement expected to be small unless bathymetric slopes are steep")
    else:
        notes.append("no moment-tensor mechanism available")
    return {"met": bool(mag_ok and depth_ok and mech_ok), "reasons": reasons, "notes": notes}


class ConsistencyTolerances:
    def __init__(self, arrival_tol_min: float = 15.0, amplitude_factor: float = 3.0):
        self.arrival_tol_min = arrival_tol_min
        self.amplitude_factor = amplitude_factor


def station_consistency(obs_arrival_min, obs_max_amplitude_m, sim_arrival_min, sim_max_amplitude_m,
                        tol: ConsistencyTolerances | None = None) -> bool | None:
    """Per-station obs-vs-sim consistency (±15 min, ×3 amplitude — documented diagnostics)."""
    tol = tol or ConsistencyTolerances()
    if obs_arrival_min is None or sim_arrival_min is None:
        return None
    if obs_max_amplitude_m is None or sim_max_amplitude_m is None:
        return None
    dt_ok = abs(obs_arrival_min - sim_arrival_min) <= tol.arrival_tol_min
    ratio = obs_max_amplitude_m / max(1e-6, sim_max_amplitude_m)
    amp_ok = (1.0 / tol.amplitude_factor) <= ratio <= tol.amplitude_factor
    return bool(dt_ok and amp_ok)


def classify_evidence(detections: list, comparisons: list | None, screening: dict | None = None,
                      n_detections_attempted: int | None = None) -> dict:
    """Classify the evidence — five transparent states (see module docstring).

    L0 insufficient / L1 none-detected / L2 possible / L3 consistent. L4 (confirmed-official) is
    NEVER machine-asserted — it exists only as a curated citation, rendered as such.
    """
    screening = screening or {"met": False, "reasons": [], "notes": []}
    usable = [d for d in detections if d.get("availability", {}).get("state") == "available"]
    triggered = [d for d in usable if d.get("trigger_ms") is not None]
    caveats: list = []
    contributing: list = []
    reasons: list = []
    n_miss = len(detections) if n_detections_attempted is None else n_detections_attempted

    if not usable:
        level = "insufficient"
        headline = ("No ocean-sensor records in range of this event" if n_miss == 0
                    else "Ocean-sensor records could not be retrieved for this event")
        if detections:
            reasons.append(f"{len(detections)} station record(s) attempted; none usable ("
                           + "; ".join(f"{d['station_id']}: {d['availability'].get('reason', 'unknown')}"
                                       for d in detections[:3]) + ")")
        else:
            reasons.append("no DART station within the search radius")
        caveats.append("missing evidence is not evidence of absence — the event may or may not have generated a tsunami")
        if screening.get("met"):
            reasons.append("seismic screening criteria met (heuristic)")
            contributing.append("seismic-screen")
        return {"level": level, "headline": headline, "reasons": reasons, "caveats": caveats,
                "contributing": contributing}

    contributing.append("ocean-sensor")
    if not triggered:
        level = "none-detected"
        headline = (f"No tsunami-like signal detected above the noise-adaptive threshold at "
                    f"{len(usable)} station{'s' if len(usable) != 1 else ''}")
        reasons.append(f"{len(usable)} station record(s) with event coverage show no trigger (k·σ rule, k = 5)")
        caveats.append("a null result is limited to the sensors, cadence and noise level actually available")
        caveats.append("deep-ocean sensors can miss small local tsunamis; coastal impacts cannot be excluded")
        if screening.get("met"):
            reasons.append("seismic screening criteria met (heuristic) despite the null sensor result")
            contributing.append("seismic-screen")
        return {"level": level, "headline": headline, "reasons": reasons, "caveats": caveats,
                "contributing": contributing}

    contributing.append("seismic-screen")
    reasons.append(f"{len(triggered)} station(s) triggered: "
                   + ", ".join(f"{d['station_id']} (+{d.get('arrival_min') or 0:.0f} min, "
                               f"max {100 * (d.get('max_amplitude_m') or 0):.0f} cm)" for d in triggered))

    if comparisons is None:
        level = "possible"
        headline = "Possible tsunami signal — run the simulation to test consistency"
        caveats.append("an anomalous wave is not yet attributed: tides, meteotsunamis and sensor glitches can also trigger")
        return {"level": level, "headline": headline, "reasons": reasons, "caveats": caveats,
                "contributing": contributing}

    contributing.append("simulation")
    cmp_ok = [c for c in comparisons if c.get("consistent") is True]
    cmp_bad = [c for c in comparisons if c.get("consistent") is False]
    cmp_null = [c for c in comparisons if c.get("consistent") is None]

    if cmp_ok:
        level = "consistent"
        headline = (f"{len(cmp_ok)} station{'s' if len(cmp_ok) != 1 else ''} show observations "
                    "consistent with the simulated tsunami")
        for c in cmp_ok:
            res = c.get("arrival_residual_min")
            reasons.append(f"{c['station_id']}: arrival {'—' if res is None else f'{res:+.0f} min'}, "
                           f"amplitude ratio {c.get('amplitude_ratio', float('nan')):.2f}")
    else:
        level = "possible"
        headline = "Wave detected, but not yet consistent with this simulation"
        for c in cmp_bad:
            reasons.append(f"{c['station_id']} inconsistent: {c.get('note', '')}")
        if cmp_null:
            reasons.append(f"{len(cmp_null)} station(s) without a comparable simulated signal "
                           f"({', '.join(c['station_id'] for c in cmp_null)})")

    caveats.append("buoy agreement is correlated evidence, not independent confirmation — all stations sample the same wave field")
    caveats.append("consistency tolerances (±15 min, ×3 amplitude) are documented diagnostics, not validated error bars")
    return {"level": level, "headline": headline, "reasons": reasons, "caveats": caveats,
            "contributing": contributing}
