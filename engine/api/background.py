"""Gated /api/background/* worker logic (§4.5 / §5 / §6) — read-only, metadata-honest.

Endpoints live in routes_background.py; this module holds the worker-side functions the single
JobRunner pool executes. Modelled on the debug hub's ideas (tools/debug_hub/runner.py,
imaging.py, recorder.py) WITHOUT importing the hub server: the same clip -> array -> PNG/probe
path, the same "never invent a value" rules. Every number the UI sees was stated by the engine
or the provider; unknown stays null.

Honesty rules carried over (§0):
  * a granule id that matches no verified layout is refused with the decoder's own text;
  * a preview is CLIPPED to the requested box (never the whole granule), max_px-capped;
  * an all-NaN clip answers "no valid pixels" — never a blank image;
  * results cache under NISAR_DATA_DIR; previews are click-only and single-concurrent.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from engine.common.granule_id import decode_granule_id


def scrub_paths(text: str, bundles_dir) -> str:
    """Replace the bundles-store prefix (raw and resolved form) so an error never shows an absolute path."""
    from pathlib import Path as _P
    out = str(text)
    forms = {str(_P(bundles_dir)), str(_P(bundles_dir).resolve())}
    for form in sorted(forms, key=len, reverse=True):
        if form:
            out = out.replace(form, "<bundles>")
    return out


class BackgroundError(RuntimeError):
    """Carries the HTTP status the route must answer with; the message is the engine's own text."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------------- #
# granule source protocol (the retriever seam, same idea as the search's factory)
# --------------------------------------------------------------------------- #
class NisarSource:
    """Production source: the engine's real NisarClient. search() finds one granule by its own
    acquisition window; fetch() clips it; the layout index (metadata-only) drives the inventory."""

    def __init__(self):
        from engine.retrieval.clients.nisar_client import NisarClient
        self.client = NisarClient()

    def search(self, bbox, t0, t1, product, fresh=False, max_results=50):
        return self.client.search(bbox, t0, t1, product, fresh=fresh, max_results=max_results)

    def fetch(self, bbox, t0, t1, product, layers, granules):
        return self.client.fetch(bbox, t0, t1, product, layers, granules=granules)

    @property
    def last_search_truncated(self) -> bool:
        return bool(getattr(self.client, "last_search_truncated", False))

    def find_by_id(self, gid: str):
        """Exact lookup of one granule by its id (the provider's own granule name); None when absent."""
        return self.client.find_by_id(gid)

    def inventory(self, ref) -> dict:
        """The granule's layer inventory from its LAYOUT INDEX (metadata-only: shapes, dtypes,
        grids, axes — no data chunks). Reuses the client's cached/persisted index; builds one
        metadata pass when absent. Failure surfaces the engine's own text, never a fake list."""
        import h5py

        from engine.retrieval.clients.h5_index import GranuleIndex, build_index
        from engine.retrieval.clients.range_io import RangeFile
        client = self.client
        ix: GranuleIndex | None = client._indexes.get(ref.granule_id)
        if ix is None:
            path = client._index_path(ref)
            ix = GranuleIndex.load(path)
        if ix is None:
            rf = RangeFile(client.io, ref.url,
                           block=int(client.cfg.net["index_block_size_bytes"]), max_blocks=256)
            rf.warm()
            with h5py.File(rf, "r") as h5:
                ix = build_index(h5, ref.granule_id, rf.size)
            try:
                ix.save(client._index_path(ref))
            except Exception:
                pass                      # an unwritable cache never blocks the inventory
            client._indexes[ref.granule_id] = ix
        grids = []
        for gpath, gl in ix.grids.items():
            dx = float(gl.x[1] - gl.x[0]) if len(gl.x) > 1 else None
            dy = float(gl.y[0] - gl.y[1]) if len(gl.y) > 1 else None
            grids.append({
                "path": gpath,
                "key": gl.key,
                "epsg": int(gl.epsg) if gl.epsg else None,
                "nx": int(len(gl.x)), "ny": int(len(gl.y)), "dx": dx, "dy": dy,
                "extras": [{"name": k, "size": int(np_len(v))} for k, v in gl.extras.items()],
                "layers": [{
                    "path": l.path, "key": l.key,
                    "shape": list(l.shape), "dtype": l.dtype.str if l.dtype.fields is None else str(l.dtype),
                    "chunks": list(l.chunks) if l.chunks else None,
                    "readable": bool(l.fast), "why_not": l.reason or None,
                } for l in (ix.layers[p] for p in gl.layers)],
            })
        return {"grids": grids, "source": "layout index (metadata only)", "n_layers": sum(len(g["layers"]) for g in grids)}

    def close(self):
        try:
            self.client.close()
        except Exception:
            pass


def np_len(v) -> int:
    try:
        return int(len(v))
    except TypeError:
        return 1


# --------------------------------------------------------------------------- #
# shared helpers
# --------------------------------------------------------------------------- #
_FIND_NARROW_MIN = 3            # first ask: an acquisition time +/- this (a ~35 s acquisition + margin)
_FIND_WIDE_MIN = 120            # last resort: +/- 2 h
_FIND_NARROW_MAX = 50
_FIND_CAP_BOX = 200             # results asked for when the search is scoped to a box
_FIND_WIDE_MAX = 250            # config/retrieval.yaml search.max_results
_LAYER_CAP = 512                # hard cap on preview max_px (longest side)


def _fmt_z(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def find_granule(source, gid: str, bbox=None):
    """One granule ref by id; a hit must match the id EXACTLY. The provider's own start/stop for a PAIR product need not
    be the reference acquisition (a GUNW can be listed under its secondary date, or over the whole ref..sec span), and a
    world-wide window holds more granules than `max_results`, so a capped list silently drops the one asked for. In order,
    cheapest and most direct first:

      1. a tight world-wide window around EACH decoded acquisition time (reference and secondary);
      2. the provider asked for that id by name (independent of window, footprint and result budget);
      3. `bbox` (the box the caller is already looking at, when it has one) over the pair's whole span;
      4. the wide (+/- 2 h) window around each acquisition time, with the full result budget.

    Unknown layout -> 404 with the decoder's text. Not found -> 404 saying what was tried and whether a provider list was
    cut off (a capped list is NOT proof the granule does not exist)."""
    dec = decode_granule_id(gid)
    product = dec["product_decoded"]
    times_s = [t for t in (dec["ref_time"], dec["sec_time"]) if t] or ([dec["start"]] if dec["start"] else [])
    if product is None or not times_s:
        raise BackgroundError(404, f"granule {gid!r} matches no verified filename layout — "
                                   "the engine cannot search for it (undecoded: "
                                   + "; ".join(dec["undecoded"]) + ")")
    times = sorted({datetime.strptime(t, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) for t in times_s})
    client = getattr(source, "client", None)
    tried: list[str] = []
    cut_off = False

    def _scan(box, t0: datetime, t1: datetime, cap: int):
        nonlocal cut_off
        refs, _degraded = source.search(box, _fmt_z(t0), _fmt_z(t1), product, max_results=cap)
        cut = bool(getattr(source, "last_search_truncated", False) or getattr(client, "last_search_truncated", False)) \
            or len(refs) >= cap
        cut_off = cut_off or cut
        tried.append(f"{'box' if box else 'world'} {_fmt_z(t0)} .. {_fmt_z(t1)} "
                     f"({len(refs)} result(s){', list truncated' if cut else ''})")
        return next((r for r in refs if r.granule_id == gid), None)

    narrow, wide = timedelta(minutes=_FIND_NARROW_MIN), timedelta(minutes=_FIND_WIDE_MIN)
    for t in times:
        hit = _scan(None, t - narrow, t + narrow, _FIND_NARROW_MAX)
        if hit is not None:
            return hit, dec
    by_name = getattr(source, "find_by_id", None)
    if by_name is not None:
        try:
            hit = by_name(gid)
            tried.append("by granule name")
        except Exception as ex:                          # an unusable name lookup never blocks the fallbacks
            hit = None
            tried.append(f"by granule name (unavailable: {type(ex).__name__}: {ex})")
        if hit is not None:
            return hit, dec
    if bbox is not None:
        hit = _scan(tuple(bbox), times[0] - wide, times[-1] + wide, _FIND_CAP_BOX)
        if hit is not None:
            return hit, dec
    for t in times:
        hit = _scan(None, t - wide, t + wide, _FIND_WIDE_MAX)
        if hit is not None:
            return hit, dec
    raise BackgroundError(404, f"granule {gid!r} not found by the provider search (tried: {'; '.join(tried)})"
                               + (" — a provider list was truncated at its result limit, so the granule may exist; "
                                  "this is NOT proof it does not" if cut_off else ""))


def _clip_of(source, ref, bbox, layer_key: str | None, also: tuple = ()):
    """One GranuleClip for the bbox; layer_key is a flat key ('__' naming) or None (all layers). `also` = further
    flat keys fetched in the same clip (the coherence candidates); the one that does not exist is simply absent."""
    layers = "|".join(re.escape(k.replace("__", "/")) for k in (layer_key, *also)) if layer_key else None
    fr = source.fetch(tuple(bbox), None, None, None, layers, granules=[ref])
    if fr.errors:
        gid, why = next(iter(fr.errors.items()))
        raise BackgroundError(502, f"the engine could not read this granule: {gid}: {why}")
    if not fr.clips:
        why = next(iter(fr.skipped.values()), "bbox does not overlap this granule's grids")
        raise BackgroundError(422, why)
    clip = fr.clips[0]
    if layer_key is not None:
        for g in clip.grids.values():
            if layer_key in g.layers:
                return clip, g, g.layers[layer_key], fr
        raise BackgroundError(404, f"layer {layer_key!r} is not part of granule {ref.granule_id} "
                                   "(the provider's clip did not include it)")
    return clip, None, None, fr


def _lonlat_bbox_of_ring(ring) -> tuple | None:
    if not ring or len(ring) < 3:
        return None
    lons = [float(p[0]) for p in ring]
    lats = [float(p[1]) for p in ring]
    w, e, s, n = min(lons), max(lons), min(lats), max(lats)
    if w > e:
        return None                 # seam-crossing ring: the caller reports the honest refusal
    return (w, s, e, n)


def _extent_lonlat(g, epsg: int) -> tuple | None:
    """The clip's lon/lat box from its UTM axes (engine-side geometry; the UI never projects)."""
    try:
        import numpy as np
        from engine.common._geo import transform_xy
        x0, x1 = float(g.x[0]), float(g.x[-1])
        y0, y1 = float(g.y[-1]), float(g.y[0])          # row 0 = north
        lon, lat = transform_xy(np.array([x0, x1]), np.array([y0, y1]), f"EPSG:{epsg}", "EPSG:4326")
        return (float(lon.min()), float(lat.min()), float(lon.max()), float(lat.max()))
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# GET /api/background/granule
# --------------------------------------------------------------------------- #
def granule_detail(source, gid: str) -> dict:
    ref, dec = find_granule(source, gid)
    from .granules_bbox import enrich_granule, group_acquisitions
    enriched = enrich_granule(ref)
    inv_note = None
    inventory: dict | None = None
    try:
        inventory = source.inventory(ref)
    except BackgroundError:
        raise
    except Exception as ex:
        inv_note = f"layer inventory unavailable: {type(ex).__name__}: {ex}"
    # §4.3 Data parts: what a scan WOULD request for this product (the chain's own layer
    # request, engine/common/layers.request_layers with the default active codes) vs what the
    # granule actually holds — per-layer booleans, engine-computed, never guessed.
    requested_note = None
    if inventory is not None:
        try:
            import re as _re
            from engine.common.layers import request_layers
            product = dec["product_decoded"] or ""
            freq = "A"
            req = request_layers(product, None, freq)
            hit = _re.compile(req) if req else None
            n_req = 0
            for grid in inventory["grids"]:
                for layer in grid["layers"]:
                    layer["requested_by_scan"] = bool(hit and hit.search(layer["path"]))
                    n_req += 1 if layer["requested_by_scan"] else 0
            inventory["scan_request_regex"] = req
            inventory["n_layers_requested_by_scan"] = n_req
        except Exception as ex:
            requested_note = f"scan request not computable: {type(ex).__name__}: {ex}"
    if inventory is not None:
        _annotate_inventory(inventory)
    groups = group_acquisitions([enriched])
    group = groups[0] if groups and groups[0]["grouped"] else None
    return {
        "ok": True,
        "id": gid,
        "granule": enriched,
        "decoded": dec,
        "group": group,
        "grouped": group is not None,
        "inventory": inventory,
        "inventory_note": inv_note or requested_note,
        "footprint": ref.footprint,
    }


# --------------------------------------------------------------------------- #
# GET /api/background/preview  (click-only; clipped; capped; cached; single concurrent)
# --------------------------------------------------------------------------- #
_PREVIEW_LOCK = threading.Lock()      # one preview at a time (the fetch can move hundreds of MB)


def _cache_paths(gid: str, layer: str, bbox, max_px: int, cmap: str, scale: str = "auto", render_version: int = 0,
                 rng: tuple | None = None, quality: str = ""):
    """Cache key = what was ASKED (cmap/scale as requested) + the render version, so changing either
    parameter makes a new entry and bumping render_version in config/preview.yaml retires old ones."""
    from engine.common.config import data_root
    key = hashlib.sha256("|".join([gid, layer,
                                   ",".join(f"{v:.6f}" for v in bbox),
                                   str(max_px), cmap, scale, f"v{render_version}"]
                                  + ([f"range={rng[0]!r},{rng[1]!r}"] if rng else [])
                                  + ([f"quality={quality}"] if quality else [])).encode()).hexdigest()[:32]
    root = Path(data_root()) / "background" / "previews"
    return root / f"{key}.png", root / f"{key}.json"


def _annotate_inventory(inventory: dict) -> None:
    """Add role / kind / previewable / default_cmap / what_it_is to every inventory layer (engine-decided).
    previewable = the layer is readable, a plain numeric dtype and its role is known to engine/common/layers.
    A layer with no known role is NOT previewable and gets what_it_is = null (the UI shows a dash).
    A broken preview.yaml / explain.yaml never breaks the inventory: the fields just stay null."""
    from engine.common.layers import parse_layer_key
    try:
        from .preview_cfg import load_preview_config
        pcfg = load_preview_config()
    except Exception:
        pcfg = None
    try:
        from .explain import layer_explanation, load_explain_config
        ecfg = load_explain_config()
    except Exception:
        ecfg, layer_explanation = None, None
    for grid in inventory.get("grids", []):
        for layer in grid.get("layers", []):
            info = parse_layer_key(layer["key"]).info
            kind = info.kind if info else None
            dtype = str(layer.get("dtype") or "")
            numeric = dtype.lstrip("<>=|")[:1] in ("f", "i", "u", "c")      # structured dtypes start with "[" / "("
            layer["role"] = info.role if info else None
            layer["kind"] = kind
            layer["previewable"] = bool(layer.get("readable") and info is not None and numeric)
            layer["default_cmap"] = pcfg.resolve_cmap("auto", kind) if (pcfg and kind) else None
            layer["what_it_is"] = (layer_explanation(layer["key"], ecfg)["what_it_is"] if ecfg is not None else None)


_QUALITY_CHOICES = ("off", "coherence")
_POLS = ("HH", "VV", "HV", "VH", "RH", "RV")


def coherence_candidates(layer_key: str) -> list:
    """Flat keys of the coherence layers that can validate `layer_key`, best first: the same polarization (every
    polarization when the layer has none, e.g. a group-level mask), and the coherence of the layer's own group
    before the other one. NISAR GUNW only - a GCOV product has no coherence layer, so the list is empty."""
    parts = layer_key.split("__")
    if "GUNW" not in parts or "grids" not in parts:
        return []
    gi = parts.index("grids")
    if len(parts) < gi + 3:
        return []
    prefix, group = parts[: gi + 2], parts[gi + 2]
    pol = next((x for x in parts[gi + 3:] if x in _POLS), None)
    groups = ["unwrappedInterferogram", "wrappedInterferogram"]
    if group == "wrappedInterferogram":
        groups.reverse()
    return ["__".join(prefix + [g, p, "coherenceMagnitude"]) for g in groups for p in ([pol] if pol else _POLS)]


_CMAP_CHOICES = ("auto", "viridis", "diverging", "magma", "gray", "categorical")
_SCALE_CHOICES = ("auto", "db", "linear")


def check_range(lo, hi) -> tuple | None:
    """Explicit colour range (in the units of meta.scale: dB when the scale resolved to dB) -> (lo, hi) or
    None when neither is given. One without the other, a non-finite value or lo >= hi is a 400."""
    import math
    if lo is None and hi is None:
        return None
    if lo is None or hi is None:
        raise BackgroundError(400, "lo and hi go together: give both or neither")
    lo, hi = float(lo), float(hi)
    if not (math.isfinite(lo) and math.isfinite(hi)):
        raise BackgroundError(400, "lo and hi must be finite numbers")
    if lo >= hi:
        raise BackgroundError(400, f"lo {lo} must be below hi {hi}")
    return lo, hi


def _coherence_on_grid(clip, grid, layer: str, view_shape: tuple, qcfg: dict) -> tuple:
    """Coherence aligned pixel-for-pixel with the drawn layer -> (q float32 array | None, info dict).
    Same grid -> used as is; another grid in the same CRS (pixelOffsets vs the interferogram) -> mean of the valid
    coherence pixels inside each drawn pixel. Anything that cannot be done honestly returns (None, info) with the
    reason spelled out; the image is then drawn unfaded and the reason is shown, never silently skipped."""
    import numpy as np
    from . import background_img as bimg
    info = {"mode": "coherence", "applied": False, "layer": None, "reason": None,
            "lo": qcfg["lo"], "hi": qcfg["hi"], "min_alpha": qcfg["min_alpha"]}
    cands = coherence_candidates(layer)
    if not cands:
        info["reason"] = ("coherence validation needs a GUNW interferogram product; this layer has no coherence "
                          "to validate against (GCOV and metadata layers have none)")
        return None, info
    found = next(((g, k) for k in cands for g in clip.grids.values() if k in g.layers), None)
    if found is None:
        info["reason"] = "this granule's clip has no coherenceMagnitude layer for this polarization"
        return None, info
    qg, qkey = found
    info["layer"] = qkey
    if grid is None or getattr(grid, "x", None) is None or getattr(grid, "y", None) is None:
        info["reason"] = "the drawn layer has no grid axes to align the coherence to"
        return None, info
    q = bimg.finite_view(qg.layers[qkey], mask_int_fill=True)
    same = qg is grid or (qg.epsg == grid.epsg and q.shape == tuple(view_shape)
                          and np.array_equal(np.asarray(qg.x), np.asarray(grid.x))
                          and np.array_equal(np.asarray(qg.y), np.asarray(grid.y)))
    info["same_grid"] = bool(same)
    if not same:
        if qg.epsg != grid.epsg:
            info["reason"] = (f"coherence is on EPSG:{qg.epsg} but the layer is on EPSG:{grid.epsg}; "
                              "the engine does not reproject for validation")
            return None, info
        try:
            q = bimg.resample_mean(q, qg.x, qg.y, grid.x, grid.y)
        except ValueError as err:
            info["reason"] = f"coherence could not be aligned to the layer's grid: {err}"
            return None, info
    if q.shape != tuple(view_shape):
        info["reason"] = f"coherence shape {q.shape} does not match the drawn layer {tuple(view_shape)}"
        return None, info
    return q.astype(np.float32, copy=False), info


def preview(source, *, gid: str, layer: str, bbox: tuple, max_px: int, cmap: str = "auto",
            scale: str = "auto", lo: float | None = None, hi: float | None = None,
            use_cache: bool = True, cfg=None, quality: str = "off") -> dict:
    """-> {png: bytes, meta: {...}}. The meta is the engine's own statement about the array:
    shape/dtype/epsg/box/min-max/percentiles/nan fraction/wire bytes/cache hit, the scale and colour
    map actually used (+ what was requested), the unit (null = unknown), and the image's corner
    coordinates (corners_lonlat, null + corners_note when they cannot be stated honestly).
    All-NaN clips answer 422 with a clear message instead of a blank image."""
    if cmap not in _CMAP_CHOICES:
        raise BackgroundError(400, f"cmap {cmap!r} not in {'|'.join(_CMAP_CHOICES)}")
    if scale not in _SCALE_CHOICES:
        raise BackgroundError(400, f"scale {scale!r} not in {'|'.join(_SCALE_CHOICES)}")
    if quality not in _QUALITY_CHOICES:
        raise BackgroundError(400, f"quality {quality!r} not in {'|'.join(_QUALITY_CHOICES)}")
    from .preview_cfg import PreviewConfigError, load_preview_config
    try:
        cfg = cfg or load_preview_config()
    except PreviewConfigError as err:
        raise BackgroundError(500, str(err))
    max_px = max(16, min(int(max_px or 256), _LAYER_CAP))
    fixed = check_range(lo, hi)           # explicit colour range, e.g. so two dates share one stretch
    qcfg = cfg.quality["coherence"]
    qtoken = (f"coherence:{qcfg['lo']}:{qcfg['hi']}:{qcfg['min_alpha']}" if quality == "coherence" else "")
    png_path, json_path = _cache_paths(gid, layer, bbox, max_px, cmap, scale, cfg.render_version, fixed, qtoken)
    if use_cache and json_path.exists() and png_path.exists():
        try:
            meta = json.loads(json_path.read_text())
            meta["cache_hit"] = True
            return {"png": png_path.read_bytes(), "meta": meta}
        except Exception:
            pass                      # a corrupt cache entry is re-rendered, never served
    if not _PREVIEW_LOCK.acquire(blocking=False):
        raise BackgroundError(409, "a preview is already running — try again when it finishes "
                                   "(single concurrent preview by design)")
    try:
        import numpy as np
        from engine.common.grids import edge_corners_lonlat
        from engine.common.layers import parse_layer_key
        ref, _dec = find_granule(source, gid, bbox)
        also = tuple(coherence_candidates(layer)) if quality == "coherence" else ()
        clip, grid, arr, fr = _clip_of(source, ref, bbox, layer, also)
        from . import background_img as bimg
        info = parse_layer_key(layer).info
        kind = info.kind if info else None
        use_scale = cfg.resolve_scale(scale, kind)
        use_cmap = cfg.resolve_cmap(cmap, kind)
        linear = bimg.finite_view(arr, mask_int_fill=True)
        view = bimg.to_db(linear) if use_scale == "db" else linear
        note = bimg.slice_note(arr)
        finite = view[np.isfinite(view)]
        if finite.size == 0:
            raise BackgroundError(422, "no valid pixels in this clip — every sampled value of "
                                       f"layer {layer!r} is NaN / no-data here"
                                       + (" (values <= 0 have no dB value)" if use_scale == "db" else ""))
        shown, stride = bimg.decimate(view, max_px)
        qinfo, alpha_shown, q_shown = {"mode": "off", "applied": False}, None, None
        if quality == "coherence":
            q_full, qinfo = _coherence_on_grid(clip, grid, layer, view.shape[-2:], qcfg)
            if q_full is not None:
                a_full = bimg.quality_alpha(q_full, qcfg["lo"], qcfg["hi"], qcfg["min_alpha"])
                alpha_shown = a_full[::stride, ::stride] if stride > 1 else a_full
                q_shown = q_full[::stride, ::stride] if stride > 1 else q_full
                qinfo["applied"] = True
        basis = shown
        if alpha_shown is not None:
            # the colour stretch must come from the pixels we trust: failed matches (saturated at a search-window
            # limit, say) would otherwise stretch the scale and flatten the real signal they are being faded from
            trusted = np.where(alpha_shown >= 0.5, shown, np.nan)
            if int(np.isfinite(trusted).sum()) >= 25:
                basis, qinfo["range_from"] = trusted, "pixels with coherence above the fade midpoint"
            else:
                qinfo["range_from"] = "all pixels (too few have usable coherence)"
        if use_cmap == "categorical":
            lo = hi = 0.0
            fixed = None                  # a categorical map has no range to fix
        elif fixed is not None:
            lo, hi = fixed
        else:
            lo, hi = bimg.auto_range(basis, symmetric=(use_cmap == "diverging"))
        body = bimg.render(shown, use_cmap, lo, hi, alpha=alpha_shown)
        if alpha_shown is not None:
            okp = np.isfinite(shown)
            if okp.any():
                al = alpha_shown[okp]
                qinfo["hidden_fraction"] = float(np.mean(al <= qcfg["min_alpha"] + 1e-6))
                qinfo["faded_fraction"] = float(np.mean(al < 1.0 - 1e-6))
                qinfo["unvalidated_fraction"] = float(np.mean(~np.isfinite(q_shown[okp])))
        notes = _render_notes(finite, shown, use_cmap, lo, hi, fixed is not None)
        corners, corners_note = (None, "no grid axes for this layer")
        if grid is not None and getattr(grid, "x", None) is not None and getattr(grid, "y", None) is not None:
            if (len(grid.y), len(grid.x)) != tuple(view.shape[-2:]):
                corners_note = "no corners: the grid axes do not match the array shape"
            else:       # corners of the DRAWN (decimated) image, not of the full-resolution array
                corners, corners_note = edge_corners_lonlat(grid.x, grid.y, int(grid.epsg), stride=int(stride))
        meta = {
            "id": gid, "layer": layer, "cmap": use_cmap, "cmap_requested": cmap,
            "scale": use_scale, "scale_requested": scale,
            "unit": cfg.unit(use_scale) or (dict(getattr(grid, "units", None) or {}).get(layer) if grid is not None else None),
            "render_version": cfg.render_version, "lo": lo, "hi": hi,
            "range_fixed": fixed is not None,
            "nan_fraction": float(1.0 - np.isfinite(view).mean()),
            "shown_stride": int(stride), "shown_shape": [int(shown.shape[0]), int(shown.shape[1])],
            "epsg": int(grid.epsg) if grid is not None else None,
            "clip_bbox_lonlat": _extent_lonlat(grid, grid.epsg) if grid is not None else None,
            "corners_lonlat": corners, "corners_note": corners_note,
            "requested_bbox": [float(v) for v in bbox],
            "slice_note": note,
            "notes": notes + _quality_notes(qinfo),
            "quality": qinfo,
            "nodata_rule": ("NaN / +-Inf; integer fill = dtype max (unsigned) or dtype min (signed)"
                            + ("; values <= 0 (no dB value)" if use_scale == "db" else "")),
            "stats": bimg.stats(arr, linear),
            "wire_bytes": {k: v for k, v in (fr.stats or {}).items() if isinstance(v, (int, float))},
            "clip_source": clip.source,
            "max_px_cap": _LAYER_CAP,
            "cache_hit": False,
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        try:
            png_path.parent.mkdir(parents=True, exist_ok=True)
            png_path.write_bytes(body)
            json_path.write_text(json.dumps(meta))
        except Exception:
            pass                      # cache write failures never fail the preview
        return {"png": body, "meta": meta}
    finally:
        _PREVIEW_LOCK.release()


def _render_notes(finite, shown, cmap: str, lo: float, hi: float, range_fixed: bool) -> list:
    """Plain-language warnings about WHY an image may look empty, stated from numbers the engine just measured:
    a colour range that misses the data, or one value that most pixels share. Never a verdict, only facts."""
    import numpy as np
    out: list = []
    if cmap == "categorical" or finite.size == 0:
        return out
    dmin, dmax = float(finite.min()), float(finite.max())
    if dmax < lo or dmin > hi:
        out.append(f"the colour range {lo:g} … {hi:g} does not overlap the data ({dmin:g} … {dmax:g}): "
                   "the image is one flat colour")
    elif range_fixed and (hi - lo) > 0:
        p2, p98 = (float(v) for v in np.percentile(finite, [2, 98]))
        if (p98 - p2) < 0.02 * (hi - lo):
            out.append(f"98 % of the data sits inside {p2:g} … {p98:g}, a sliver of the fixed colour range "
                       f"{lo:g} … {hi:g}: the image looks flat")
    mv, mf = bimg_modal(shown)
    if mv is not None and mf >= 0.5:
        out.append(f"{round(mf * 100)} % of the drawn pixels share one value ({mv:g})")
    return out


def _quality_notes(q: dict) -> list:
    """Facts about what the coherence validation did (or why it did nothing), for the legend."""
    if q.get("mode") != "coherence":
        return []
    if not q.get("applied"):
        return [f"coherence validation not applied: {q.get('reason') or 'unknown reason'}"]
    out = []
    hid, un = q.get("hidden_fraction", 0.0), q.get("unvalidated_fraction", 0.0)
    if hid >= 0.9:
        out.append(f"coherence validation fades {round(hid * 100)}% of the pixels to near-transparent "
                   f"(coherence ≤ {q['lo']:g}): almost nothing here is reliable")
    if un >= 0.25:
        out.append(f"{round(un * 100)}% of the pixels had no coherence value and were treated as unreliable")
    return out


def bimg_modal(shown) -> tuple:
    import numpy as np
    from . import background_img as bimg
    return bimg.modal(shown[np.isfinite(shown)])


def probe(source, *, gid: str, layer: str, lon: float, lat: float) -> dict:
    """Value at one point (nearest pixel centre); the UI draws it, the engine reads it."""
    d = 0.05
    ref, _dec = find_granule(source, gid, (lon - d, lat - d, lon + d, lat + d))
    clip, grid, arr, _fr = _clip_of(source, ref, (lon - d, lat - d, lon + d, lat + d), layer)
    if grid is None or arr is None:
        raise BackgroundError(404, f"layer {layer!r} not found in granule {gid}")
    import numpy as np
    from engine.common._geo import transform_xy
    view = arr if arr.ndim == 2 else arr[arr.shape[0] // 2]
    x, y = transform_xy(np.array([lon]), np.array([lat]), "EPSG:4326", f"EPSG:{grid.epsg}")
    x, y = float(x[0]), float(y[0])
    xi = int(np.argmin(np.abs(np.asarray(grid.x, float) - x)))
    yi = int(np.argmin(np.abs(np.asarray(grid.y, float) - y)))
    v = view[yi, xi]
    px_lon, px_lat = transform_xy(np.array([float(grid.x[xi])]), np.array([float(grid.y[yi])]),
                                  f"EPSG:{grid.epsg}", "EPSG:4326")
    finite = bool(np.isfinite(v))
    return {
        "ok": True, "id": gid, "layer": layer,
        "value": float(v) if finite else None, "finite": finite,
        "row": int(yi), "col": int(xi),
        "epsg": int(grid.epsg),
        "x": float(grid.x[xi]), "y": float(grid.y[yi]),
        "lon": float(px_lon[0]), "lat": float(px_lat[0]),
        "requested_lon": float(lon), "requested_lat": float(lat),
        "dtype": str(view.dtype),
    }


# --------------------------------------------------------------------------- #
# GET /api/background/stack-pair — the GCOV acquisitions at a GUNW's two ends
# --------------------------------------------------------------------------- #
def stack_pair(source, *, gid: str) -> dict:
    dec = decode_granule_id(gid)
    product = dec["product_decoded"]
    if product not in ("GUNW", "GOFF") or not dec["ref_time"] or not dec["sec_time"]:
        raise BackgroundError(422, f"{gid!r} is not a pair product with decoded timestamps — "
                                   "a before/after comparison needs a GUNW (or GOFF) pair")
    from .granules_bbox import enrich_granule
    ref, _ = find_granule(source, gid)
    box = _lonlat_bbox_of_ring(ref.footprint)
    if box is None:
        raise BackgroundError(422, "this granule has no usable footprint ring from the provider "
                                   "— the GCOV search cannot be scoped; nothing is guessed")
    from engine.common.config import load_common_config
    merge = float(load_common_config().window.epoch_merge_minutes)
    gcov_name = str(load_common_config().products.gcov)

    def _end(iso_end: str) -> dict:
        end = datetime.strptime(iso_end, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        t0 = (end - timedelta(minutes=merge)).strftime("%Y-%m-%dT%H:%M:%SZ")
        t1 = (end + timedelta(minutes=merge)).strftime("%Y-%m-%dT%H:%M:%SZ")
        refs, _deg = source.search(box, t0, t1, gcov_name, max_results=50)
        members = sorted((r for r in refs
                          if abs((_parse_utc(r.start) - end).total_seconds()) <= merge * 60
                          and _parse_utc(r.start) is not None),
                         key=lambda r: (_parse_utc(r.start), r.granule_id))
        return {"epoch": iso_end, "granules": [enrich_granule(r) for r in members]}

    return {
        "ok": True, "id": gid, "product": product,
        "bbox_used": [float(v) for v in box], "epoch_merge_minutes": merge,
        "ref": _end(dec["ref_time"]),
        "sec": _end(dec["sec_time"]),
        "note": ("each end lists every GCOV granule acquired within ±epoch_merge_minutes "
                 "of the pair's own date (variants included); an empty list means the engine "
                 "found no backscatter acquisition for that date"),
    }


def _parse_utc(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# §5 preprocessing — the chain recorder (the debug hub's Tracer idea, engine-owned)
# --------------------------------------------------------------------------- #
def build_trace_payload(tracer, bundle) -> dict:
    """Serialize a Tracer's steps + arrays for the preprocessing endpoint. Thumbnails stay in
    memory (bounded, evicted with the run) and are rendered to PNG on demand."""
    from engine.common.chain import STEPS
    needs_of = {s.code: list(s.needs) for s in STEPS}
    steps = []
    arrays: dict[str, dict] = {}
    for snap in tracer.trace.steps:
        steps.append({
            "code": snap.code, "name": snap.name, "status": snap.status,
            "seconds": round(float(snap.seconds), 4), "message": snap.message,
            "new_flags": list(snap.new_flags), "new_assumptions": list(snap.new_assumptions),
            "n_arrays_total": int(snap.n_arrays_total),
            "reads": needs_of.get(snap.code, []),
            "arrays": [a for a in (arrays.get(p) for p in snap.arrays) if a],
        })
    return {"steps": steps, "arrays": arrays, "_recs": dict(tracer.trace.arrays)}


def register_trace(runner: "JobRunnerLike", run_id: str, payload: dict) -> dict:
    """Split the tracer's arrays into (endpoint-visible meta, in-memory records) and bind them
    to the run. The records are evicted with the run's state."""
    from .background_img import finite_view, slice_note, stats as img_stats
    recs = payload.pop("_recs")
    meta: dict[str, dict] = {}
    store: dict[str, object] = {}
    for i, (path, rec) in enumerate(sorted(recs.items()), start=1):
        aid = f"t{i}"
        view = finite_view(rec.thumb)
        from engine.common.grids import extent_corners_lonlat
        corners, corners_note = extent_corners_lonlat(rec.extent, rec.epsg, stride=int(rec.stride), shape=rec.shape)
        m = {
            "corners_lonlat": corners, "corners_note": corners_note,
            "aid": aid, "path": path, "label": rec.label, "group": rec.group,
            "step": rec.step, "first_step": rec.first_step,
            "shape": list(rec.shape), "dtype": rec.dtype, "kind": rec.kind,
            "stats": rec.stats, "stride": int(rec.stride),
            "epsg": rec.epsg, "extent": list(rec.extent) if rec.extent else None,
            "note": rec.note or slice_note(rec.thumb),
            "fill": rec.fill, "categories": rec.categories,
            "thumb_shape": [int(view.shape[0]), int(view.shape[1])],
            "thumb_stats": img_stats(view),
        }
        meta[aid] = m
        store[aid] = rec
    with runner._lock:      # trace_arrays is read/evicted under this lock by the other threads
        runner.trace_arrays[run_id] = store
    payload["arrays"] = meta
    payload["n_arrays"] = len(meta)
    return payload


class JobRunnerLike:
    """Type comment target only; the real JobRunner owns trace_arrays and its lock."""
    trace_arrays: dict
    _lock: "threading.Lock"


def trace_array_png(rec, cmap: str, lo: float | None, hi: float | None, max_px: int) -> tuple:
    """One stored trace array -> (png bytes, meta). Same rules as the preview renderer."""
    from . import background_img as bimg
    view = bimg.finite_view(rec.thumb)
    if not np_finite_any(view):
        raise BackgroundError(422, "no valid pixels in this trace array")
    shown, stride = bimg.decimate(view, max(16, min(int(max_px or 320), 512)))
    kind = rec.kind
    if cmap == "auto":
        cmap = "categorical" if kind in ("int", "bool") else "viridis"
    if cmap == "categorical":
        lo = hi = 0.0
    elif lo is None or hi is None:
        lo, hi = bimg.auto_range(shown, symmetric=(cmap == "diverging"))
    body = bimg.render(shown, cmap, lo, hi)
    meta = {"cmap": cmap, "lo": lo, "hi": hi, "shown_stride": int(stride),
            "shown_shape": [int(shown.shape[0]), int(shown.shape[1])],
            "epsg": rec.epsg, "extent": list(rec.extent) if rec.extent else None,
            "label": rec.label, "path": rec.path}
    from engine.common.grids import extent_corners_lonlat
    meta["corners_lonlat"], meta["corners_note"] = extent_corners_lonlat(
        rec.extent, rec.epsg, stride=int(rec.stride) * int(stride), shape=rec.shape)
    return body, meta


def trace_array_probe(rec, fx: float, fy: float) -> dict:
    """Pixel probe over the stored thumbnail (fractional coordinates, like the hub's /api/px)."""
    import numpy as np
    from . import background_img as bimg
    view = bimg.finite_view(rec.thumb)
    h, w = view.shape
    col = min(w - 1, max(0, int(float(fx) * w)))
    row = min(h - 1, max(0, int(float(fy) * h)))
    v = view[row, col]
    out = {"row": int(row * rec.stride), "col": int(col * rec.stride), "stride": int(rec.stride),
           "value": float(v) if np.isfinite(v) else None, "finite": bool(np.isfinite(v))}
    if rec.extent and rec.epsg:
        x0, x1, y0, y1 = rec.extent
        x = x0 + (col + 0.5) / w * (x1 - x0)
        y = y1 - (row + 0.5) / h * (y1 - y0)
        out["x"], out["y"], out["epsg"] = float(x), float(y), int(rec.epsg)
        try:
            from engine.common._geo import transform_xy
            lon, lat = transform_xy(np.array([x]), np.array([y]), f"EPSG:{rec.epsg}", "EPSG:4326")
            out["lon"], out["lat"] = float(lon[0]), float(lat[0])
        except Exception as ex:
            out["lonlat_error"] = f"{type(ex).__name__}: {ex}"
    return out


def np_finite_any(a) -> bool:
    import numpy as np
    f = np.abs(a) if np.asarray(a).dtype.kind == "c" else a
    return bool(np.isfinite(np.asarray(f, np.float32)).any())


def bundle_preprocessing(bundle) -> dict:
    """What a CACHED bundle itself recorded: P01..P13 step records, flags, assumptions, the
    SceneBundle summary and the hand-off table (Registry.plan + declared-input presence).
    Array thumbnails do not exist for bundles built without a trace — stated, not faked."""
    from tools.debug_hub.handoff import bundle_summary, pipeline_feed
    from engine.common.chain import STEPS
    needs_of = {s.code: list(s.needs) for s in STEPS}
    steps = [{
        "code": s.code, "name": s.name, "status": s.status,
        "seconds": round(float(s.seconds), 4), "message": s.message,
        "new_flags": [], "new_assumptions": [], "n_arrays_total": 0, "arrays": [],
        "reads": needs_of.get(s.code, []),
    } for s in bundle.provenance.steps]
    return {
        "steps": steps,
        "arrays": {}, "n_arrays": 0,
        "bundle_summary": bundle_summary(bundle),
        "handoff": pipeline_feed(bundle),
        "flags": [{"severity": f.severity, "step": f.step, "code": f.code, "message": f.message}
                  for f in bundle.flags],
        "assumptions": list(bundle.provenance.assumptions),
        "source": "bundle records (built without a live trace — array thumbnails are not available)",
    }


# --------------------------------------------------------------------------- #
# §6 pipelines — input / process / output for one pipeline of one run
# --------------------------------------------------------------------------- #
def pipeline_detail(runner, registry, run_id: str, name: str) -> dict:
    from .jobs import ScanError  # noqa: F401  (import kept local; errors carry engine text)
    state = runner.get_state(run_id)
    if state is None:
        raise BackgroundError(404, f"unknown run: {run_id}")
    manifest = next((m for m in registry.manifests if m.name == name), None)
    if manifest is None:
        raise BackgroundError(404, f"unknown pipeline: {name!r}; available: "
                                   + ", ".join(sorted(registry.names())))
    result = next((r for r in state.get("results", []) if r.get("pipeline") == name), None)
    if result is None:
        raise BackgroundError(404, f"pipeline {name!r} has no result in run {run_id} — "
                                   "it was not launched in this run (or the run is still going)")
    bundle = None
    bundle_error = None
    bundle_id = state.get("bundle_id") or None
    if bundle_id:
        try:
            from engine.common.bundle_io import load_bundle
            bundle = load_bundle(runner.bundles_dir / bundle_id)
        except Exception as ex:
            bundle_error = f"{type(ex).__name__}: {scrub_paths(str(ex), runner.bundles_dir)}"

    from tools.debug_hub.handoff import bundle_summary, pipeline_feed
    feed_row = None
    bundle_summary_out = None
    if bundle is not None:
        feed = pipeline_feed(bundle, registry)
        feed_row = next((r for r in feed["rows"] if r["name"] == name), None)
        bundle_summary_out = bundle_summary(bundle)

    detail = (result or {}).get("detail") or {}
    return {
        "ok": True,
        "run": {"id": run_id, "status": state.get("status"), "tile": state.get("tile"),
                "kind": state.get("kind"), "bundle_id": bundle_id},
        "manifest": {
            "name": manifest.name, "code": manifest.code, "sensor": manifest.sensor,
            "applies": manifest.applies, "uses_codes": list(manifest.uses_codes),
            "fetch_codes": list(manifest.fetch_codes), "min_epochs": manifest.min_epochs,
            "status_values": list(manifest.status_values),
            "execution": manifest.execution,
            "method": list(manifest.raw.get("method") or []),
            "own_prep": list(manifest.raw.get("own_prep") or []),
            "own_fetch": list(manifest.raw.get("own_fetch") or []),
            "assumptions": list(manifest.raw.get("assumptions") or []),
        },
        "routing": feed_row.get("routing") if feed_row else None,
        "feed": feed_row,
        "bundle_summary": bundle_summary_out,
        "bundle_error": bundle_error,
        "result": result,
        "result_missing": None if result is not None else
        f"pipeline {name!r} has no result in run {run_id} (it was not launched or the run is still going)",
        "process": {
            "runtime_s": (result or {}).get("runtime_s"),
            "diagnostics": detail.get("diagnostics") if isinstance(detail, dict) else None,
            "detail_steps": detail.get("steps") if isinstance(detail, dict) else None,
            "warnings": (result or {}).get("warnings", []),
            "versions": (result or {}).get("versions", {}),
        },
    }


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
