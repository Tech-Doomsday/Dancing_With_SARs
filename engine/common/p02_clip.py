# P02 — tile + 1-pixel margin, only the layers the active manifests request
"""Fetch the resolved granules, clipped to this tile.

`retriever.fetch_tile(tile, ..., granules=[...], layers=<regex>)` does the byte-range work; this step decides
*what to ask for*: only the products and layers needed by the measurement codes the active manifests declare
(`request.active_codes`), with the margin from config (`clip.margin_m`, default: retrieval's `tiling.margin_m`)
so edge filters and resampling have context. The retrieval layer additionally snaps its window out by one
native pixel, which is the "1 px" margin.

Every outcome the retriever reports is kept: errors and degraded (stale) results become flags, granules that do
not overlap the tile are listed, and a fingerprint of every array that arrives is stored for P13.
"""
from __future__ import annotations

import hashlib

import numpy as np

from .context import BuildContext, iso
from .layers import request_layers
from .scene_bundle import InputRecord


def fingerprint(clip) -> str:
    h = hashlib.blake2b(digest_size=16)
    for gk in sorted(clip.grids):
        g = clip.grids[gk]
        h.update(gk.encode())
        h.update(str(g.epsg).encode())
        h.update(np.ascontiguousarray(g.x).tobytes())
        h.update(np.ascontiguousarray(g.y).tobytes())
        for ek in sorted(g.extras):
            h.update(ek.encode())
            h.update(np.ascontiguousarray(g.extras[ek]).tobytes())
        for lk in sorted(g.layers):
            a = np.ascontiguousarray(g.layers[lk])
            h.update(lk.encode())
            h.update(str(a.dtype).encode())
            h.update(str(a.shape).encode())
            h.update(a.tobytes())
    return h.hexdigest()


def run(ctx: BuildContext) -> None:
    res = ctx.resolved
    if res is None or not res.to_fetch:
        return
    cfg = ctx.cfg
    if getattr(ctx.retriever, "stored", False):      # preprocessing from a retrieval: clips come from the store, no network
        ctx.assume(f"P02 loaded clips from retrieval {getattr(ctx.retriever, 'retrieval_id', '?')} (no network call)")
    names = {"GUNW": cfg.products.gunw, "GCOV": cfg.products.gcov, "GOFF": cfg.products.goff}
    for prod, refs in res.to_fetch.items():
        name = names[prod]
        layers = request_layers(name if prod != "GOFF" else "GOFF", ctx.request.active_codes, cfg.products.frequency,
                                tuple(cfg.products.pols), cfg.products.use_noise_floor)
        if layers is None:
            ctx.flag("nothing_to_request", "info", "P02", f"{prod}: no layer is needed by the active manifests")
            continue
        try:
            fr = ctx.retriever.fetch_tile(ctx.tile, iso(ctx.request.t0), iso(ctx.request.t1), product=name,
                                          layers=layers, granules=list(refs), margin_m=cfg.clip.margin_m)
        except Exception as ex:                            # reported, never silent
            ctx.flag("fetch_failed", "error", "P02", f"{prod} fetch raised {type(ex).__name__}: {ex}", product=prod)
            continue
        ctx.fetched[prod] = fr
        if fr.degraded:
            ctx.flag("fetch_degraded", "warn", "P02", f"{prod}: served from stale cache / listing", product=prod)
        for gid, why in fr.errors.items():
            ctx.flag("fetch_error", "error", "P02", f"{gid}: {why}", granule=gid, product=prod)
        for gid, why in fr.skipped.items():
            ctx.flag("fetch_skipped", "info", "P02", f"{gid}: {why}", granule=gid, product=prod)
        for clip in fr.clips:
            gid = clip.granule.granule_id
            ctx.clips[gid] = clip
            layer_keys = tuple(sorted(k for g in clip.grids.values() for k in g.layers))
            ctx.input_records.append(InputRecord(gid, prod, clip.source, tuple(sorted(clip.grids)), layer_keys,
                                                 fingerprint(clip)))
