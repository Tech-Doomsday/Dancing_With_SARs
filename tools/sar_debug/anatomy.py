"""
File anatomy: walk an HDF5 file (h5py or any object with the same duck-typed API) and report
the tree, the small "interesting" parameters, the gridded layers and the grids they sit on.

Duck typing on purpose: a dataset is anything with `.shape`, a group is anything with `.keys()`,
so the same code runs on h5py objects and on the in-memory stand-in used by the demo/self-test.
"""
from __future__ import annotations

import re
import time

import numpy as np

from . import geo

C_LIGHT = 299_792_458.0

# datasets worth reading the value of (everything else is only listed by name/shape)
KEY = re.compile(r"frequency|wavelength|bandwidth|ionosph|tropo|tide|baseline|lookDirection|"
                 r"orbitPassDirection|polariz|looks|unwrap|filter|algorithm|version|spacing|"
                 r"referenceTerrain|processingType|isGeocoded|mask|dem|projection|epsg|"
                 r"trackNumber|frameNumber|missionId|productType|zeroDoppler", re.I)
KEY_PATH = re.compile(r"/identification/|/processingInformation/parameters/", re.I)
PRUNE = {"orbit", "attitude", "calibrationInformation"}   # large, no rasters; skipped unless deep=True

# first match wins; add a row to add a kind of data
CATEGORIES = [
    ("components", "Connected components", r"connectedComponents$", "which pixels were unwrapped together"),
    ("unwrapped", "Unwrapped phase", r"unwrappedPhase$", "main deformation layer, radians"),
    ("wrapped", "Wrapped interferogram", r"(?<!un)wrappedInterferogram$", "complex fringes (shown as amplitude and phase)"),
    ("coherence", "Coherence", r"coherence", "phase quality, 0 to 1"),
    ("iono", "Ionosphere", r"ionosph", "phase screen estimate and its uncertainty"),
    ("offsets", "Pixel offsets", r"offset|correlationSurfacePeak", "sub-pixel shifts from cross-correlation"),
    ("mask", "Masks", r"mask", "valid-data and similar masks"),
    ("tropo", "Troposphere", r"tropo|hydrostatic|wetDelay", "lookup cubes (recorded, not necessarily applied)"),
    ("tide", "Solid-earth tide", r"tide", "lookup cube (recorded, not necessarily applied)"),
    ("baseline", "Baselines", r"baseline", "orbit separation"),
    ("geometry", "Geometry", r"incidenceAngle|losUnitVector|elevationAngle|alongTrackUnitVector|groundTrackVelocity|slantRange|heightAboveEllipsoid",
     "needed to turn line-of-sight motion into vertical or horizontal"),
    ("other", "Everything else", r"", "other gridded layers in the file"),
]
CAT_LABEL = {k: l for k, l, _, _ in CATEGORIES}
CAT_NOTE = {k: n for k, _, _, n in CATEGORIES}
CAT_ORDER = [k for k, *_ in CATEGORIES]
# categories read by default (cheap, and what every downstream pipeline relies on first)
DEFAULT_CATS = ("unwrapped", "coherence", "components", "wrapped", "iono", "mask")


def categorize(path: str) -> str:
    return next(k for k, _, rx, _ in CATEGORIES if re.search(rx, path, re.I))


def is_dataset(o) -> bool:
    return hasattr(o, "shape")


def is_group(o) -> bool:
    return hasattr(o, "keys") and not hasattr(o, "shape")


def to_py(v, limit=200):
    """h5py value -> something printable / JSON-able."""
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")[:limit]
    if isinstance(v, np.ndarray):
        return [to_py(x, limit) for x in v.ravel().tolist()[:64]]
    if isinstance(v, np.generic):
        return to_py(v.item(), limit)
    if isinstance(v, str):
        return v[:limit]
    return v


def find_epsg(grp):
    """EPSG code of the nearest `projection` at or above grp (None if there is none)."""
    node = grp
    for _ in range(12):
        if "projection" in node.keys():
            p = node["projection"]
            for key in ("epsg_code", "EPSG", "epsg"):
                if key in p.attrs:
                    return int(np.asarray(p.attrs[key]).item())
            try:
                return int(np.asarray(p[()]).item())
            except Exception:
                pass
        parent = getattr(node, "parent", None)
        if parent is None or getattr(node, "name", "/") == "/":
            break
        node = parent
    return None


def grid_info(h5, grid_path: str, default_epsg=None) -> dict:
    """Size, spacing, projection and lon/lat extent of one x/y grid group (reads only the 1-D axes).
    A grid without its own projection (e.g. radarGrid) inherits `default_epsg` and is flagged as such."""
    g = h5[grid_path]
    xs = np.asarray(g["xCoordinates"][()], dtype="float64")
    ys = np.asarray(g["yCoordinates"][()], dtype="float64")
    epsg = find_epsg(g)
    inherited = epsg is None and default_epsg is not None
    epsg = epsg if epsg is not None else default_epsg
    info = {"path": grid_path, "nx": int(xs.size), "ny": int(ys.size), "epsg": epsg, "epsg_inherited": inherited,
            "dx": float(xs[1] - xs[0]) if xs.size > 1 else None, "dy": float(ys[1] - ys[0]) if ys.size > 1 else None,
            "x0": float(xs[0]) if xs.size else None, "x1": float(xs[-1]) if xs.size else None,
            "y0": float(ys[0]) if ys.size else None, "y1": float(ys[-1]) if ys.size else None,
            "lonlat": None, "bounds": None}
    tr = geo.make_transformer(epsg) if epsg else None
    if tr is not None and xs.size and ys.size:
        dx, dy = (info["dx"] or 0) / 2, (info["dy"] or 0) / 2
        L, R, T, B = xs[0] - dx, xs[-1] + dx, ys[0] - dy, ys[-1] + dy
        lon, lat = tr.inv([L, R, L, R], [T, T, B, B])
        lon, lat = [float(v) for v in lon], [float(v) for v in lat]
        info["lonlat"] = [(lon[0], lat[0]), (lon[1], lat[1]), (lon[3], lat[3]), (lon[2], lat[2])]  # UL UR LR LL ring
        info["bounds"] = (min(lon), min(lat), max(lon), max(lat))
    return info


def inspect_file(h5, trace=None, max_nodes: int = 6000, deep: bool = False) -> dict:
    """Walk the file once. Returns tree, params, rasters, grids and derived facts."""
    t_start = time.time()
    st = {"groups": 0, "datasets": 0, "nodes": 0, "truncated": False, "pruned": []}
    params, rasters = {}, []

    def note(level, msg):
        if trace is not None:
            trace.note(level, msg)

    def walk(grp, grid):
        grid = grp.name if "xCoordinates" in grp.keys() else grid   # nearest group above holding x/y
        node = {"name": grp.name.rstrip("/").split("/")[-1] or "/", "path": grp.name, "type": "group", "children": []}
        st["groups"] += 1
        for k in list(grp.keys()):
            if st["nodes"] >= max_nodes:
                st["truncated"] = True
                break
            if k in PRUNE and not deep:
                st["pruned"].append(grp.name.rstrip("/") + "/" + k)
                continue
            try:
                o = grp.get(k) if hasattr(grp, "get") else grp[k]
            except Exception as ex:                               # broken soft/external link
                node["children"].append({"name": k, "type": "error", "detail": f"{type(ex).__name__}: {ex}"})
                continue
            if o is None:
                continue
            st["nodes"] += 1
            if is_dataset(o):
                st["datasets"] += 1
                shape = tuple(int(s) for s in o.shape)
                n = int(np.prod(shape)) if shape else 1
                path = o.name
                d = {"name": k, "path": path, "type": "ds", "shape": shape, "dtype": str(o.dtype),
                     "chunks": getattr(o, "chunks", None), "compression": getattr(o, "compression", None)}
                if n <= 16 and len(params) < 500 and (KEY.search(path) or KEY_PATH.search(path)):
                    try:
                        params[path] = to_py(o[()])
                    except Exception as ex:
                        params[path] = f"<unreadable: {type(ex).__name__}>"
                if len(shape) in (2, 3) and grid is not None and k not in ("xCoordinates", "yCoordinates"):
                    isz = np.dtype(o.dtype).itemsize if getattr(o.dtype, "kind", "O") != "O" else 0
                    rasters.append({"path": path, "shape": list(shape), "dtype": str(o.dtype), "grid": grid,
                                    "cat": categorize(path), "chunks": d["chunks"], "compression": d["compression"],
                                    "bytes": n * isz})
                node["children"].append(d)
            elif is_group(o):
                node["children"].append(walk(o, grid))
        return node

    tree = walk(h5, None)

    grids = {}
    gps = sorted({r["grid"] for r in rasters})
    own = {}
    for gp in gps:
        try:
            own[gp] = find_epsg(h5[gp])
        except Exception:
            own[gp] = None
    default_epsg = next((e for e in own.values() if e is not None), None)
    for gp in gps:
        try:
            grids[gp] = grid_info(h5, gp, default_epsg)
        except Exception as ex:
            grids[gp] = {"path": gp, "error": f"{type(ex).__name__}: {ex}"}
            note("WARN", f"grid {gp}: {type(ex).__name__}: {ex}")

    facts = derive_facts(params, rasters)
    note("INFO", f"file anatomy: {st['groups']} groups, {st['datasets']} datasets, {len(rasters)} rasters on "
                 f"{len(grids)} grid(s) in {time.time() - t_start:.1f}s" + (" (tree truncated)" if st["truncated"] else ""))
    return {"tree": tree, "stats": st, "params": params, "rasters": rasters, "grids": grids, "facts": facts,
            "default_epsg": default_epsg, "seconds": round(time.time() - t_start, 2)}


def derive_facts(params: dict, rasters: list) -> dict:
    def first(rx, pred=lambda v: True):
        return next((v for k, v in params.items() if re.search(rx, k, re.I) and pred(v)), None)

    num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)
    freq = first(r"(processed)?centerFrequency$", lambda v: num(v) and v > 1e8)
    wl = first(r"wavelength$", num)
    src = "file (wavelength dataset)" if wl else ("c / centerFrequency" if freq else None)
    if wl is None and freq:
        wl = C_LIGHT / freq

    def has(rx):
        return [r["path"] for r in rasters if re.search(rx, r["path"], re.I)]

    return {
        "frequency_hz": freq, "wavelength_m": wl, "wavelength_source": src,
        "pass_direction": first(r"orbitPassDirection$"), "look_direction": first(r"lookDirection$"),
        "track": first(r"trackNumber$"), "frame": first(r"frameNumber$"),
        "polarizations": first(r"polarizations?$|mainBandPolarization$"),
        "ionosphere_layers": has("ionosph"), "troposphere_layers": has("tropo|hydrostatic|wetDelay"),
        "tide_layers": has("tide"),
    }
