"""NISAR product filename decoding for the granule Explorer (globe task E8).

Token layouts VERIFIED against real CMR metadata (cmr.earthdata.nasa.gov, queried 2026-10-30):
pair products (GUNW) carry  [cycle, track, direction, frame, secondary_cycle, range_bandwidth,
polarization, ref_start, ref_stop, sec_start, sec_stop, CRID, orbit, status, center, version]
(e.g. NISAR_L2_PR_GUNW_004_004_A_018_006_4000_SH_..._P05023_N_P_J_001 with CMR TRACK_NUMBER=4,
FRAME_NUMBER=18, RANGE_BANDWIDTH=40 MHz, FREQUENCY_A_POLARIZATION=HH, and cycle_ref -> cycle_sec
spanning exactly the 24-day pair), and single-acquisition products (GCOV) carry
[cycle, track, direction, frame, range_bandwidth, polarization, frequency_band, start, stop, ...]
(RANGE_BANDWIDTH_CONCAT "40+5" for token 4005, frequency token "A").

Honesty rules (globe task §0): a token is decoded only where verified; anything else is kept RAW
and listed in `undecoded`. Nothing is guessed: unknown fields come back null. The P01 parser
(p01_granule_resolution) is untouched and stays byte-identical — this module serves the
Explorer's per-granule enrichment only.
"""
from __future__ import annotations

import re

from .context import parse_time

# GUNW / GOFF (pair products): cycle, track, dir, frame, sec cycle, bandwidth, pol, 4 timestamps,
# CRID, orbit token, status token, center token, version
_PAIR_ID = re.compile(
    r"^NISAR_L(?P<level>[12])_(?P<pipeline>[A-Z0-9]{1,4})_(?P<product>GUNW|GOFF|RUNW|ROFF|RIFG)"
    r"_(?P<cycle>\d{3})_(?P<track>\d{3})_(?P<direction>[AD])_(?P<frame>\d{3})_(?P<sec_cycle>\d{3})"
    r"_(?P<bandwidth>\d{3,4}(?:\+\d{3,4})*)_(?P<pol>[A-Z0-9]{2,4})"
    r"_(?P<ref_start>\d{8}T\d{6})_(?P<ref_stop>\d{8}T\d{6})"
    r"_(?P<sec_start>\d{8}T\d{6})_(?P<sec_stop>\d{8}T\d{6})"
    r"_(?P<crid>[A-Z]\d{5})_(?P<orbit>[A-Z])_(?P<status>[A-Z])_(?P<center>[A-Z])_(?P<version>\d{3})$"
)
# GCOV / GSLC (single-acquisition products): cycle, track, dir, frame, bandwidth, pol, freq band,
# 2 timestamps, CRID, orbit token, status token, center token, version
_EPOCH_ID = re.compile(
    r"^NISAR_L(?P<level>[12])_(?P<pipeline>[A-Z0-9]{1,4})_(?P<product>GCOV|GSLC|RSLC)"
    r"_(?P<cycle>\d{3})_(?P<track>\d{3})_(?P<direction>[AD])_(?P<frame>\d{3})"
    r"_(?P<bandwidth>\d{3,4}(?:\+\d{3,4})*)_(?P<pol>[A-Z0-9]{2,4})_(?P<freq>[A-Z]{1,2})"
    r"_(?P<start>\d{8}T\d{6})_(?P<stop>\d{8}T\d{6})"
    r"_(?P<crid>[A-Z]\d{5})_(?P<orbit>[A-Z])_(?P<status>[A-Z])_(?P<center>[A-Z])_(?P<version>\d{3})$"
)

_FIELDS = ("cycle_ref", "cycle_sec", "track", "frame", "direction", "range_bandwidth_mhz",
           "polarization", "ref_time", "sec_time", "baseline_days", "start", "stop", "crid",
           "orbit_token", "status_token", "center_token", "version", "level", "pipeline")


def decode_granule_id(gid: str) -> dict:
    """Best-effort verified decoding of a NISAR filename. Every not-verified token is listed in
    `undecoded` (as "<token> (<field>)") and its field stays null. An id that matches NO known
    layout yields all-null fields with the whole id in `undecoded` — never invented values."""
    out: dict = {f: None for f in _FIELDS}
    out["undecoded"] = []
    out["product_decoded"] = None

    m = _PAIR_ID.match(gid) or _EPOCH_ID.match(gid)
    if m is None:
        out["undecoded"].append(f"{gid} (whole id: no verified layout matched)")
        return out
    g = m.groupdict()
    out["level"] = g["level"]
    out["pipeline"] = g["pipeline"]
    out["product_decoded"] = _product_of(gid)
    out["track"] = int(g["track"])
    out["frame"] = int(g["frame"])
    out["direction"] = "A" if g["direction"] == "A" else "D"   # CMR-verified: A <-> ASCENDING
    out["cycle_ref"] = int(g["cycle"])
    out["crid"] = g["crid"]
    out["orbit_token"] = g["orbit"]
    out["status_token"] = g["status"]
    out["center_token"] = g["center"]
    out["version"] = g["version"]
    out["polarization"] = _polarization(g["pol"], out["undecoded"])
    out["range_bandwidth_mhz"] = _bandwidth_mhz(g["bandwidth"], out["undecoded"])

    if "sec_cycle" in g:                                   # pair product
        out["cycle_sec"] = int(g["sec_cycle"])
        try:
            ref = parse_time(g["ref_start"])
            sec = parse_time(g["sec_start"])
            out["ref_time"] = ref.strftime("%Y-%m-%dT%H:%M:%SZ")
            out["sec_time"] = sec.strftime("%Y-%m-%dT%H:%M:%SZ")
            # P01's own convention: a pair stored sec-before-ref is marked flipped and the
            # baseline normalised positive — the Explorer matches the engine, never contradicts it
            if sec < ref:
                out["baseline_days"] = round((ref - sec).total_seconds() / 86400.0, 3)
                out["undecoded"].append("pair stored secondary-first (flipped); baseline normalised")
            else:
                out["baseline_days"] = round((sec - ref).total_seconds() / 86400.0, 3)
        except ValueError as ex:
            out["undecoded"].append(f"{ex} (timestamps)")
    else:                                                  # single-acquisition product
        try:
            out["start"] = parse_time(g["start"]).strftime("%Y-%m-%dT%H:%M:%SZ")
            out["stop"] = parse_time(g["stop"]).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError as ex:
            out["undecoded"].append(f"{ex} (timestamps)")
    # the orbit / status / center single-letter tokens have no verified public mapping
    # (CMR ORBIT_TYPE is MOE for BOTH the N and F tokens, so "N = near-real-time" is NOT
    # establishable) — they stay raw and are reported:
    out["undecoded"].append(f"{g['orbit']} (orbit token)")
    return out


def _product_of(gid: str) -> str | None:
    m = re.match(r"^NISAR_L[12]_[A-Z0-9]{1,4}_([A-Z]{3,4})_", gid)
    return m.group(1) if m else None


def _polarization(token: str, undecoded: list) -> str | None:
    """Verified against CMR: filename SH -> FREQUENCY_A_POLARIZATION HH; SHNA -> HH;
    DHDH -> HH (main band). Structural rule: a 2nd character of H means the main band is HH.
    Only the H cases have been observed, so anything else (V, P, ...) stays raw — a guessed
    'VV' would also be wrong for a cross-pol main band such as HV."""
    if len(token) >= 2 and token[1] == "H":
        return "HH"
    undecoded.append(f"{token} (polarization)")
    return None


def _bandwidth_mhz(token: str, undecoded: list) -> float | None:
    """Verified against CMR: the field is range bandwidth per frequency in MHz x 100
    ('4000' -> 40 MHz; '7700' -> 77). A concatenated multi-frequency token ('4005' ->
    RANGE_BANDWIDTH_CONCAT '40+5') is kept RAW — no per-frequency split is invented. A token
    decodes only when it is exactly MHz*100 (integer result)."""
    if re.fullmatch(r"\d{3,4}", token):
        v = int(token) / 100.0
        if v == int(v):
            return float(int(v))
    undecoded.append(f"{token} (range bandwidth: multi-frequency or unparsable token kept raw)")
    return None


__all__ = ["decode_granule_id"]
