"""Put every convention engine/common assumes next to what a real extracted product says.

    rows = reality_check(retriever, bundle)       # [{topic, engine, real, source, verdict, note}]

verdict: confirmed   engine value matches the real one (numerically, or the spec text says the same)
         consistent  agrees with the documented convention but could not be tested against ground truth
         differs     the real product disagrees with the engine DEFAULT (the debug run used the real value)
         info        a measured fact about the real data, no assumption involved
         missing     the real product / extraction does not carry this, so it cannot be checked

Everything computed here comes from meta.json + the bundle. Sources that are only quoted (specs) are named in `source`.
"""
from __future__ import annotations

import math

import numpy as np

from engine.common.config import load_common_config
from engine.common.layers import integer_fill

C = 299_792_458.0
SPEC = "NISAR L2 GUNW spec JPL D-102272 v1.2.1"
ATBD = "NISAR SDS ATBD JPL D-95677 RevA"
ASF = "ASF NISAR GUNW guide (as cited by dancing-with-sars-eq)"


def _row(topic, engine, real, source, verdict, note=""):
    return {"topic": topic, "engine": str(engine), "real": str(real), "source": source, "verdict": verdict, "note": note}


def _unit(az_deg: float, inc_deg: float):
    a, t = math.radians(az_deg), math.radians(inc_deg)
    return math.sin(t) * math.sin(a), math.sin(t) * math.cos(a)


def reality_check(retriever, bundle, base_cfg=None) -> list:
    base = base_cfg or load_common_config()
    m = retriever.meta
    r, ident, proc = m["radar"], m["identification"], m.get("processing", {})
    rows = []

    # --- radar scalars the clip does not carry ------------------------------------------------------------
    lam_cf = C / float(r["centerFrequencyHz"])
    d = abs(base.radar.wavelength_m - r["wavelength"]) / r["wavelength"]
    rows.append(_row("wavelength", f"{base.radar.wavelength_m} m (NISAR L-band default)",
                     f"{r['wavelength']:.7f} m  (c/centerFrequency = {lam_cf:.7f} m)", "meta.json radar.wavelength",
                     "differs" if d > 0.002 else "confirmed",
                     f"{100 * d:.2f} % off: every LOS displacement scales by that ratio. This product is ALOS-1 L-band "
                     "(1.27 GHz) processed by the NISAR SDS; NISAR itself is ~1.2575 GHz. The clip carries no "
                     "centerFrequency, so the right wavelength has to come from outside."))
    lk = float(r["numberOfLooks"])
    rows.append(_row("effective looks (80 m pixel)", f"{base.radar.gunw_effective_looks:g}",
                     f"{lk:g}  (= {proc.get('looksAzimuth')} az x {proc.get('looksRange')} rg)", "meta.json radar.numberOfLooks",
                     "differs" if abs(lk - base.radar.gunw_effective_looks) / lk > 0.05 else "confirmed",
                     f"sigma scales with 1/sqrt(L): the default under-/over-states it by x{math.sqrt(lk / base.radar.gunw_effective_looks):.2f}"))

    # --- LOS vector direction: independent numbers from the product ---------------------------------------
    inc, hd = float(r["incidenceAngleDeg"]), float(r["headingDeg"])
    sx, sy = float(r["losUnitVectorProductX"]), float(r["losUnitVectorProductY"])
    right = str(r["lookDirection"]).lower() == "right"
    gs = _unit(hd - 90 if right else hd + 90, inc)         # ground -> sensor for this look side
    sg = (-gs[0], -gs[1])
    dgs, dsg = max(abs(gs[0] - sx), abs(gs[1] - sy)), max(abs(sg[0] - sx), abs(sg[1] - sy))
    rows.append(_row("LOS vector direction (losUnitVectorX/Y)", base.radar.los_vector_points,
                     f"stored ({sx:+.4f}, {sy:+.4f}); ground->sensor from heading {hd:.1f}, incidence {inc:.1f}, "
                     f"{r['lookDirection']}-looking = ({gs[0]:+.4f}, {gs[1]:+.4f}); sensor->ground = ({sg[0]:+.4f}, {sg[1]:+.4f})",
                     f"{SPEC}: 'from target to sensor'; numeric check on meta.json",
                     "confirmed" if base.radar.los_vector_points == "ground_to_sensor" and dgs < 0.01 < dsg else "differs",
                     f"max |difference|: ground->sensor {dgs:.4f}, sensor->ground {dsg:.4f}. NOTE dancing-with-sars-eq "
                     "src/lib/science/los-projection.ts labels the SAME stored vector 'satellite-to-ground' and returns "
                     "the opposite sign from losUnitVector(); see docs/common_vs_eq_comparison.md."))
    side = next((f.detail.get("side") for f in bundle.flags if f.code == "look_side_implied"), None)
    rows.append(_row("look side implied by P05", "from LOS vector x along-track vector", f"{side}  (identification/lookDirection = {r['lookDirection']})",
                     "P05 flag look_side_implied vs meta.json", "confirmed" if side == str(r["lookDirection"]).lower() else "differs",
                     "the along-track cubes of this run are built from the real heading, so this tests the LOS sign and P05's logic, "
                     "not the along-track direction in a real product"))

    # --- phase sign ---------------------------------------------------------------------------------------
    rows.append(_row("phase -> displacement sign", f"phase_sign {base.radar.phase_sign:+d}: toward-sensor = {base.radar.phase_sign:+d} * lambda/4pi * phase",
                     "phi = 4pi/lambda * (R_sec - R_ref)  =>  range change = +lambda*phi/4pi (away from sensor positive)",
                     f"{ATBD} 5.7 / 5.11.1 (via dancing-with-sars-eq docs/science.md)", "consistent",
                     "toward-sensor = -(range change), so the engine's -1 and the other repo's +lambda*phi/4pi are the same physics "
                     "under opposite labels. Not confirmed with a known ground displacement."))

    # --- corrections --------------------------------------------------------------------------------------
    flags_true = sorted(k for k, v in proc.items() if k.endswith("CorrectionApplied") and str(v) == "True")
    rows.append(_row("corrections already applied to unwrappedPhase", str(dict(base.radar.corrections_applied_by_product)),
                     f"{proc.get('phaseIonosphericCorrectionAppliedToLayer')}; {proc.get('phaseTroposphericCorrectionAppliedToLayer')}",
                     ASF, "consistent",
                     f"metadata flags {', '.join(flags_true) or 'none'} read 'True' but describe the GEOCODING step, not the phase layer - "
                     "reading them naively would make P07 double-subtract. P07 does not read them."))

    # --- fills / classes ----------------------------------------------------------------------------------
    cc = retriever.cc
    n0, nfill = int((cc == 0).sum()), int((cc == integer_fill(cc.dtype)).sum())
    rows.append(_row("connectedComponents fill", f"integer_fill(uint16) = {integer_fill(np.uint16)}", "_FillValue 65535",
                     f"{SPEC} (gunspect.txt)", "confirmed",
                     f"whole scene: {nfill} fill px, {n0} px with component 0 ('no component', NOT fill), "
                     f"{int((cc >= 1).sum() - nfill)} px in components >= 1. P03 keeps phase only where cc >= {base.decode.cc_min}."))
    rows.append(_row("float layer fill (unwrappedPhase, coherenceMagnitude)", "NaN", "_FillValue nan", f"{SPEC} (gunspect.txt)", "confirmed",
                     f"whole scene: {100 * float(np.isnan(retriever.phase).mean()):.1f} % of phase pixels are NaN"))
    rows.append(_row("canonical posting", f"G80 = {base.grids.g80_m} m", f"xSpacing {m['grid']['xSpacing']} m, EPSG {m['grid']['epsg']}",
                     "meta.json grid", "confirmed" if base.grids.g80_m == abs(m["grid"]["xSpacing"]) else "differs"))

    # --- P01 against the identification group -------------------------------------------------------------
    st = bundle.stack()
    if st is not None and st.interferometric and st.interferometric.pairs:
        p = st.interferometric.pairs[0]
        ok = (p.ref_time or "")[:16] == ident["referenceZeroDopplerStartTime"][:16] and \
             (p.sec_time or "")[:16] == ident["secondaryZeroDopplerStartTime"][:16]
        rows.append(_row("pair dates parsed from the granule id (P01)", f"{p.ref_time} -> {p.sec_time}",
                         f"{ident['referenceZeroDopplerStartTime'][:19]} -> {ident['secondaryZeroDopplerStartTime'][:19]}",
                         "identification group in meta.json", "confirmed" if ok else "differs",
                         f"dt = {p.dt_days:.3f} d vs temporalBaselineDays {ident.get('temporalBaselineDays')}. First time P01's id regex meets a real id."))

    # --- measured facts about this tile --------------------------------------------------------------------
    if st is not None and st.interferometric is not None:
        i = st.interferometric
        coh, los = np.asarray(i.coherence)[0], np.asarray(i.los_m)[0]
        v = np.isfinite(los)
        thr = base.stable.coherence_min
        rows.append(_row("coherence on this tile", f"stable.coherence_min = {thr}",
                         f"mean {float(np.nanmean(coh)):.2f}; {100 * float((coh[v] >= thr).mean()) if v.any() else 0:.0f} % of valid-LOS pixels reach {thr}",
                         "bundle", "info", f"scene mean coherence {m['stats']['coherenceMean']:.2f}. The synthetic test scenes use 0.8 (GranuleSpec default), "
                         "so the 0.5 threshold was never stressed by them."))
    rows.append(_row("iono / tropo screens, pixelOffsets, mask layer, radarGrid spatial variation", "requested by P02", "not in the extracted rasters",
                     "data/derived/<slug>/ holds only phase, coherence, components + meta.json", "missing",
                     "P07 therefore audits nothing and P05 sees a flat geometry. Real values for these exist only inside the 264 MB .h5, "
                     "which the other repo ships as a Git-LFS pointer (134 bytes)."))
    return rows
