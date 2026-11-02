"""3.01 earthquake_coseismic — what the verdict REALLY used, stated plainly.

Pure functions (no I/O) so each statement is unit-testable:

    window_summary      the interferogram windows that were analysed, and how much of the search window they cover
    completeness        did ASF return a short / failed listing for GUNW?  (a quiet result cannot be trusted then)
    radar_assumptions   conventions that were assumed, not verified (phase sign, looks, wavelength, look side)
    floor_statement     the detection floor in mm AND as a minimum detectable rate in mm/year
    stack_summary       which stacks existed, which were analysed, which one drove the verdict
"""
from __future__ import annotations

from datetime import datetime, timezone

# engine flag codes about the granule listing, in the order they matter
SEARCH_FLAGS = ("search_incomplete", "search_error", "search_degraded")


def parse_iso(t) -> datetime | None:
    if not t:
        return None
    try:
        d = datetime.fromisoformat(str(t).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _day(t) -> str:
    return str(t)[:10] if t else "?"


# ------------------------------------------------------------------------------------------ analysed window
def window_summary(pair_records: list, request=None) -> dict:
    """The analysed interferogram windows (skipped pairs excluded), merged where they overlap, against the search window."""
    spans = []
    for r in pair_records:
        a, b = parse_iso(r.get("ref_time")), parse_iso(r.get("sec_time"))
        if not r.get("skipped") and a and b and b > a:
            spans.append((a, b, r.get("stack_key"), r.get("ref_time"), r.get("sec_time")))
    spans.sort(key=lambda x: (x[0], x[1]))
    merged: list = []
    for a, b, *_ in spans:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    covered = sum((b - a).total_seconds() for a, b in merged) / 86400.0
    search_days = None
    t0, t1 = parse_iso(getattr(request, "t0", None)), parse_iso(getattr(request, "t1", None))
    if t0 and t1 and t1 > t0:
        search_days = (t1 - t0).total_seconds() / 86400.0
    n = len(spans)
    if not n:
        text = "no interferogram window could be analysed"
    else:
        lo, hi = min(s[3] for s in spans), max(s[4] for s in spans)
        text = (f"{n} interferogram{'s' if n != 1 else ''} analysed, together {covered:.0f} day{'s' if round(covered) != 1 else ''} "
                f"between {_day(lo)} and {_day(hi)}")
        if search_days:
            text += f" — {covered:.0f} of the {search_days:.0f}-day search window"
    return {"n_pairs": n, "covered_days": round(covered, 1),
            "search_days": round(search_days, 1) if search_days else None,
            "first_ref": min((s[3] for s in spans), default=None), "last_sec": max((s[4] for s in spans), default=None),
            "pairs": [{"stack": s[2], "ref_time": s[3], "sec_time": s[4]} for s in spans], "text": text}


# ------------------------------------------------------------------------------------------ listing completeness
def completeness(flags, products=("GUNW",)) -> dict:
    """Engine flags about the ASF listing, restricted to the products this pipeline reads (GUNW). A flag with no
    product is counted too: when in doubt the listing is treated as short, never as complete."""
    hits, notes = [], []
    for f in flags:
        if f.code not in SEARCH_FLAGS:
            continue
        d = dict(getattr(f, "detail", None) or {})
        prod = d.get("product")
        if prod is not None and prod not in products:
            continue
        hits.append({"code": f.code, "product": prod, "expected": d.get("expected"), "got": d.get("got"),
                     "message": f.message})
        notes.append(f"{f.code}: {f.message}")
    short = [h for h in hits if h["code"] in ("search_incomplete", "search_error")]
    return {"complete": not short, "listing_flags": hits, "notes": notes,
            "stale_cache": any(h["code"] == "search_degraded" for h in hits)}


def incomplete_claim(c: dict, scan_mode: bool) -> str:
    counts = "; ".join(f"{h['product'] or 'listing'}: expected {h['expected']}, got {h['got']}"
                       for h in c["listing_flags"] if h.get("expected") is not None)
    tail = ("In scan mode the analysed pair may therefore not be the newest one that exists. " if scan_mode else
            "A pair spanning the event window may be missing from it. ")
    return ("The ASF/CMR granule listing for GUNW was incomplete" + (f" ({counts})" if counts else "") + ". " + tail +
            "A positive result still describes the pair that WAS analysed; a quiet or negative result cannot be trusted "
            "and is reported as INSUFFICIENT_EVIDENCE.")


# ------------------------------------------------------------------------------------------ assumed radar conventions
def radar_assumptions(bundle, radar_cfg) -> tuple[str, dict]:
    """-> (one plain sentence, structured values). Uses the assumption lines P05/P06/P07 recorded in the bundle."""
    keep = ("L-band wavelength", "phase sign", "effective looks", "LOS unit vector", "correction screens")
    recorded = [a for a in getattr(bundle.provenance, "assumptions", ()) if str(a).startswith(keep)]
    look = getattr(radar_cfg, "look_side", None)
    support = {"wavelength_m": radar_cfg.wavelength_m, "phase_sign": radar_cfg.phase_sign,
               "gunw_effective_looks": radar_cfg.gunw_effective_looks, "look_side": look,
               "los_vector_points": radar_cfg.los_vector_points, "recorded": recorded}
    claim = (f"Assumed, not verified against ground truth: wavelength {radar_cfg.wavelength_m} m (the clip carries no "
             f"centre frequency); phase sign {radar_cfg.phase_sign:+d} (flipping it swaps toward/away and changes the "
             f"Okada comparison); {radar_cfg.gunw_effective_looks:g} effective looks per 80 m pixel (sets sigma and "
             f"therefore the detection threshold); line-of-sight vector points {radar_cfg.los_vector_points.replace('_', ' ')}; "
             + ("look side unknown, so a flipped line-of-sight convention cannot be caught." if look is None
                else f"look side {look}, which lets the sign check catch a flipped line of sight."))
    return claim, support


# ------------------------------------------------------------------------------------------ detection floor
def floor_statement(summary, params, ref_time, sec_time) -> tuple[str, dict]:
    a, b = parse_iso(ref_time), parse_iso(sec_time)
    days = (b - a).total_seconds() / 86400.0 if a and b and b > a else None
    thr_mm = (summary.threshold_median_m or 0.0) * 1000.0
    floor_mm = params.detection_floor_m * 1000.0
    rate = (max(thr_mm, floor_mm) / days * 365.25) if days else None
    claim = (f"Detection threshold per pixel = max({params.significance_k:g}·σ, {floor_mm:.0f} mm, ionosphere floor); "
             f"median {thr_mm:.1f} mm over this {days:.0f}-day pair" if days else
             f"Detection threshold per pixel = max({params.significance_k:g}·σ, {floor_mm:.0f} mm, ionosphere floor); "
             f"median {thr_mm:.1f} mm")
    if rate is not None:
        claim += (f", i.e. movement slower than about {rate:.0f} mm/year averaged over the pair cannot cross it. "
                  "Slow processes (a swelling volcano, subsidence: millimetres to centimetres per year) are invisible "
                  "to this rule in a single pair — 'nothing detected' is not 'nothing moving'. ")
    claim += "The floor is a setting: config/common.yaml → earthquake.detection_floor_m."
    return claim, {"floor_mm": round(floor_mm, 2), "k": params.significance_k, "median_threshold_mm": round(thr_mm, 2),
                   "pair_days": round(days, 2) if days else None,
                   "min_detectable_rate_mm_per_year": round(rate, 1) if rate is not None else None}


# ------------------------------------------------------------------------------------------ stacks
def stack_summary(bundle, analysed_keys, best_key) -> dict:
    out = []
    for key, s in bundle.stacks.items():
        ifg = s.interferometric
        n = len([p for p in ifg.pairs if p.times_resolved]) if ifg else 0
        out.append({"stack": key, "track": s.track, "direction": s.direction, "gunw_pairs": n,
                    "gcov_epochs": len(s.backscatter.epochs) if s.backscatter else 0,
                    "mean_coherence": s.quality.mean_coherence if s.quality else None,
                    "analysed": key in analysed_keys, "drove_verdict": key == best_key})
    return {"stacks": out, "analysed": sorted(analysed_keys), "verdict_stack": best_key,
            "note": ("Ascending and descending are analysed separately; they are NOT combined into vertical / east-west "
                     "motion (that needs a joint decomposition this pipeline does not do).")}
