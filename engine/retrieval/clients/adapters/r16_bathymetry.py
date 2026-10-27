"""R16 — ETOPO1 bathymetry/topography via NCEI THREDDS OPeNDAP (REAL implementation).

Source (verified live 2026-10-27):
    https://www.ngdc.noaa.gov/thredds/dodsC/global/ETOPO1_Bed_g_gmt4.nc.ascii
    ETOPO1 Bedrock, 1 arc-minute global relief, cell-centred at exact arc-minute coordinates:
    z[lat][lon], lat[0] = -90, lon[0] = -180; metres, positive up, ~MSL.

Subsetting through the .ascii interface returns a text payload (~1.5 MB per 8°x6° at 1').

Caching: every fetched region is stored under <data>/cache/etopo/ as
    <key>.json        — grid metadata + provenance
    <key>.npy         — float32 bed elevations (row-major)
so runs are reproducible without the network. Failures are degraded RESULTS, never exceptions,
and never synthetic bathymetry.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

OPENDAP_BASE = "https://www.ngdc.noaa.gov/thredds/dodsC/global/ETOPO1_Bed_g_gmt4.nc.ascii"
LAT_ROWS = 10801          # -90 .. +90 at 1' (verified geometry)
LON_COLS = 21601          # -180 .. +180 at 1'
DEFAULT_TIMEOUT_S = 120.0


def lat_row(lat: float) -> int:
    return int(round((lat + 90) * 60))


def lon_col(lon: float) -> int:
    return int(round((lon + 180) * 60))


def snap_bbox(bbox, res: int) -> tuple:
    """Snap a bbox to the res-arc-minute lattice so cached regions are reusable."""
    step = res / 60.0
    return (int(np.floor(bbox[0] / step) * step), int(np.floor(bbox[1] / step) * step),
            int(np.ceil(bbox[2] / step) * step), int(np.ceil(bbox[3] / step) * step))


def cache_key(bbox, res: int) -> str:
    b = snap_bbox(bbox, res)
    return f"etopo1_bed_{b[1]:.3f}N{b[0]:.3f}E_{b[3]:.3f}N{b[2]:.3f}E_{res}am"


def _cache_dir() -> Path:
    try:
        from engine.retrieval.settings import load_settings
        root = Path(load_settings().data_dir)
    except Exception:
        root = Path("data")
    d = root / "cache" / "etopo"
    d.mkdir(parents=True, exist_ok=True)
    return d


def parse_opendap_ascii(text: str) -> dict:
    """OPeNDAP .ascii response for a 2-D z[lat][lon] subset -> {z, lat, lon, ny, nx}.

    Format (verified): "z.z[ny][nx]" header, rows "[i], v, v, ...", MAPS vectors
    "z.lat[ny]" / "z.lon[nx]" with the comma-separated values on the NEXT non-empty line.
    """
    lines = text.split("\n")
    ny = nx = 0
    header = re.compile(r"^z\.z\[(\d+)\]\[(\d+)\]")
    for l in lines:
        m = header.match(l.strip())
        if m:
            ny, nx = int(m.group(1)), int(m.group(2))
            break
    if not ny or not nx:
        raise ValueError("ETOPO1 response missing z.z[ny][nx] header")
    z = np.full(ny * nx, np.nan, dtype=np.float32)
    row = -1
    lat_line = lon_line = None
    row_re = re.compile(r"^\[(\d+)\],(.*)$")
    for li, raw in enumerate(lines):
        l = raw.strip()
        if l.startswith("z.lat"):
            lat_line = np.array([float(x) for x in (lines[li + 1] or "").split(",") if x.strip()], dtype=np.float64)
            continue
        if l.startswith("z.lon"):
            lon_line = np.array([float(x) for x in (lines[li + 1] or "").split(",") if x.strip()], dtype=np.float64)
            continue
        rm = row_re.match(l)
        if rm:
            row = int(rm.group(1))
            if row < 0 or row >= ny:
                continue
            col = 0
            for tok in rm.group(2).split(","):
                tok = tok.strip()
                if col >= nx:
                    break
                if tok:
                    try:
                        z[row * nx + col] = float(tok)
                    except ValueError:
                        pass
                col += 1
    if row != ny - 1:
        raise ValueError(f"ETOPO1 parse incomplete: last row {row} != {ny - 1}")
    if lat_line is None or lon_line is None:
        raise ValueError("ETOPO1 parse missing MAPS vectors")
    return {"z": z, "lat": lat_line, "lon": lon_line, "ny": ny, "nx": nx}


def _provenance(url: str, bbox, res: int, sha256: str | None) -> dict:
    return {
        "adapter": "r16_bathymetry",
        "url_or_id": url,
        "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sha256": sha256,
        "dataset_id": cache_key(bbox, res),
        "product": "ETOPO1_Bed_g_gmt4.nc (NOAA NCEI THREDDS/OPeNDAP)",
        "units": "metres, positive up",
        "crs": "EPSG:4326 (WGS84), cell-centred at exact arc-minutes",
        "vertical_datum": "MSL (per ETOPO1 product documentation)",
        "quality_notes": [
            "ETOPO1 blends ship soundings and satellite-derived gravity; local errors of tens of metres are possible",
            f"{res}' cells cannot resolve coastal flooding; land inundation outputs carry this caveat",
        ],
    }


def get_region(bbox, res_arcmin: int = 2, timeout_s: float | None = None) -> dict:
    """Fetch (or load from cache) an ETOPO1 Bedrock subset on the requested resolution lattice.

    bbox = (min_lon, min_lat, max_lon, max_lat). Returns
    {"source", "grid": {res_arcmin, lat0, lon0, nx, ny, bed(list)}, "degraded", "error", "provenance"}.
    """
    if res_arcmin not in (1, 2, 4):
        return {"source": "ncei-etopo1", "grid": None, "degraded": True,
                "error": f"res_arcmin must be 1, 2 or 4 (got {res_arcmin})", "provenance": None}
    res = res_arcmin
    bb = snap_bbox(tuple(float(v) for v in bbox), res)
    key = cache_key(bb, res)
    meta_path = _cache_dir() / f"{key}.json"
    data_path = _cache_dir() / f"{key}.npy"
    try:
        meta = json.loads(meta_path.read_text())
        bed = np.load(data_path)
        if bed.size != meta["nx"] * meta["ny"]:
            raise ValueError("ETOPO1 cache length mismatch")
        return {"source": "ncei-etopo1 (cache)", "degraded": False, "error": None,
                "grid": {**{k: meta[k] for k in ("res_arcmin", "lat0", "lon0", "nx", "ny")},
                         "bed": bed.tolist()},
                "provenance": meta.get("provenance")}
    except Exception:
        pass  # cache miss — fetch live

    r0, r1 = lat_row(bb[1]), lat_row(bb[3])
    c0, c1 = lon_col(bb[0]), lon_col(bb[2])
    if r0 < 0 or r1 > LAT_ROWS - 1 or c0 < 0 or c1 > LON_COLS - 1:
        return {"source": "ncei-etopo1", "grid": None, "degraded": True,
                "error": f"ETOPO1 region out of range: rows {r0}..{r1}, cols {c0}..{c1}", "provenance": None}
    if r1 <= r0 or c1 <= c0:
        return {"source": "ncei-etopo1", "grid": None, "degraded": True,
                "error": "ETOPO1 empty region", "provenance": None}

    url = f"{OPENDAP_BASE}?z[{r0}:{res}:{r1}][{c0}:{res}:{c1}]"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "nisar-hazard-engine/0.1 (R16 ETOPO1 adapter)"})
        with urllib.request.urlopen(req, timeout=timeout_s or DEFAULT_TIMEOUT_S) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        parsed = parse_opendap_ascii(text)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, ValueError) as ex:
        return {"source": "ncei-etopo1", "grid": None, "degraded": True,
                "error": f"ETOPO1 fetch/parse failed: {type(ex).__name__}: {ex}", "provenance": None}

    grid = {"res_arcmin": res, "lat0": float(parsed["lat"][0]), "lon0": float(parsed["lon"][0]),
            "nx": int(parsed["nx"]), "ny": int(parsed["ny"]), "bed": parsed["z"].tolist()}
    prov = _provenance(url, bb, res, sha)
    try:
        np.save(data_path, parsed["z"].astype(np.float32))
        meta_path.write_text(json.dumps({"res_arcmin": res, "lat0": grid["lat0"], "lon0": grid["lon0"],
                                         "nx": grid["nx"], "ny": grid["ny"], "provenance": prov,
                                         "saved_at": time.time()}))
    except Exception:
        pass  # cache write failure is non-fatal
    return {"source": "ncei-etopo1", "grid": grid, "degraded": False, "error": None, "provenance": prov}


__all__ = ["get_region", "parse_opendap_ascii", "snap_bbox", "cache_key", "LAT_ROWS", "LON_COLS"]
