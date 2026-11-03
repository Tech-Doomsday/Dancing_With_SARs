"""PIPELINES tab: run the featured pipelines through the REAL registry path and publish input + output.

Featured today: 3.01 earthquake_coseismic ("earthquake") and 3.13 tropical_cyclone ("hurricane").
Nothing here re-implements the engine: routing, gating, launch and result normalisation are the
registry's own (`Registry.plan` / `Registry.run_tile` / `registry.coerce_result`), and the pipeline
sees exactly what it always sees — the bundle. The only seam the hub touches is the documented
`providers` argument of `run(bundle, providers=None)`:

    live      providers=None  -> the pipeline's own fetch.py -> REAL adapters (R1 ComCat, ...).
    selftest  earthquake's event fetch is pointed at the repo's calibrated fixture recipe
              (tests/fixtures_eq.py truth-bowl event) so the synthetic source stays hermetic —
              no network call is mixed into a run that is already stamped SELF-TEST. Every other
              pipeline runs exactly as in live mode.

Authenticity: a pipeline that routing/gating does not launch is shown with the REGISTRY's own
synthetic result (NOT_APPLICABLE / NOT_RUN / INSUFFICIENT_EVIDENCE / PENDING and its reasons); a
folder without run() shows the registry's NOT_MODELLED; a raised exception shows ERROR with the
trimmed traceback. `detail.layers` payloads (base64 grids) are decoded into the job's ArrayStore so
the UI renders them like every other array (hover probe, enlarge) and are stripped from the JSON.
"""
from __future__ import annotations

import base64
import time
import traceback

import numpy as np

from .runner import raw_stats
from .store import Entry
from .util import short_exc

# (pipeline name, human label, bundle array groups this pipeline's declared inputs come from)
FEATURED = (
    ("earthquake_coseismic", "earthquake", ("ifg", "geometry", "stable")),
    ("tropical_cyclone", "hurricane", ("cov",)),
)

_LayerCmap = {"sequential": "viridis", "binary": "categorical", "categorical": "categorical"}
_MAX_STORED_PX = 4096            # keep the pixel probe honest; beyond this, decimate + set stride


# ------------------------------------------------------------------ selftest event provider
def selftest_event_provider(tile):
    """Dict events provider shaped like the R1 adapter output (fetch._normalize_provider_out accepts it).

    The event is the repo's fixture recipe (engine/pipelines/earthquake_coseismic/tests/fixtures_eq.py:
    Mw 5.6 normal faulting, strike 90 / dip 40 / rake -90, depth 5 km), placed exactly like the
    acceptance tests place it (test_pipeline._event_lonlat): 3 km west / 3 km south of the
    deformation truth-bowl centre — which the selftest scene bakes into the newest GUNW pair at
    (e0 + 4 km, n0 + 5.5 km). Time is derived from the fetch window the pipeline itself passes in,
    so the event is always inside the selected pair's strict ranking window.
    """
    epicentre = None
    try:
        from pyproj import Transformer
        lon, lat = Transformer.from_crs(int(tile.epsg), 4326, always_xy=True).transform(
            float(tile.e0) + 4000.0 - 3000.0, float(tile.n0) + 5500.0 - 3000.0)
        epicentre = (round(float(lat), 6), round(float(lon), 6))
    except Exception:
        epicentre = None                                       # bbox-centre fallback below

    def provider(bbox, t0, t1):                                  # bbox = (W, S, E, N); t0/t1 = window
        import datetime as _dt
        W, S, E, N = bbox
        mid = _dt.datetime.fromisoformat(t0.replace("Z", "+00:00")) + \
            (_dt.datetime.fromisoformat(t1.replace("Z", "+00:00")) -
             _dt.datetime.fromisoformat(t0.replace("Z", "+00:00"))) / 2
        lat, lon = epicentre if epicentre else ((S + N) / 2 - 0.027, (W + E) / 2 - 0.033)
        return {
            "source": "hub-selftest-fixture",
            "degraded": False,
            "events": [{
                "id": "hub-selftest-event",
                "magnitude": 5.6,
                "place": "selftest fixture event (calibrated against the synthetic truth bowl)",
                "time": mid.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "lat": lat,
                "lon": lon,
                "depth_km": 5.0,
                "url": None,
                "nodal_planes": [[90.0, 40.0, -90.0]],
            }],
        }
    return provider


# ------------------------------------------------------------------ execution
def run_featured(job, tile, bundle, as_of: str, source_kind: str) -> dict:
    """Run the featured pipelines the way the registry would and build the UI payload.

    Returns {"names", "label", "tile", "bundle_id", "as_of", "seconds", "source", "providers_used",
             "pipelines": {name: payload}} — payload shape documented in `_payload`.
    """
    from engine.registry import Registry

    t0 = time.perf_counter()
    reg = Registry()
    manifests = [reg.manifest(name) for name, _, _ in FEATURED if name in reg.names()]
    missing = [name for name, _, _ in FEATURED if name not in reg.names()]
    out: dict = {"names": [m.name for m in manifests], "missing": missing, "tile": tile.id,
                 "bundle_id": bundle.bundle_id, "as_of": as_of, "source": source_kind,
                 "providers_used": {}, "pipelines": {}}

    plan_results, todo = reg.plan(bundle, manifests)             # the registry's own routing + gating
    todo_names = [m.name for m in todo]

    if todo_names and source_kind == "live":
        run = reg.run_tile(tile, None, as_of=as_of, bundle=bundle, workers=0, names=todo_names)
        for name in todo_names:
            out["pipelines"][name] = _payload(job, name, run.results[name], bundle,
                                              launched=True, lane_note="registry run_tile(workers=0)")
    elif todo_names:
        # selftest: identical registry path, except earthquake's event fetch uses the fixture provider
        for m in todo:
            name = m.name
            if name == "earthquake_coseismic":
                out["providers_used"]["earthquake_coseismic"] = \
                    "providers={'events': hub selftest fixture} (hermetic; live mode uses the real R1 ComCat adapter)"
                out["pipelines"][name] = _selftest_run(job, reg, m, bundle, tile)
            else:
                run = reg.run_tile(tile, None, as_of=as_of, bundle=bundle, workers=0, names=[name])
                out["pipelines"][name] = _payload(job, name, run.results[name], bundle,
                                                  launched=True, lane_note="registry run_tile(workers=0)")
    for m in manifests:                                          # everything plan() refused to launch
        if m.name not in out["pipelines"]:
            out["pipelines"][m.name] = _payload(job, m.name, plan_results.get(m.name), bundle,
                                                launched=False, lane_note="registry plan(): not launched")
    out["seconds"] = round(time.perf_counter() - t0, 3)
    out["launched"] = todo_names
    return out


def _selftest_run(job, reg, manifest, bundle, tile) -> dict:
    """verdict.run(bundle, providers={'events': fixture}) + the registry's own result normalisation."""
    t0 = time.perf_counter()
    import importlib
    mod = importlib.import_module(f"engine.pipelines.{manifest.name}.verdict")
    try:
        raw = mod.run(bundle, providers={"events": selftest_event_provider(tile)})
        from engine.registry import coerce_result
        r = coerce_result(raw, manifest, bundle)
        r.runtime_s = time.perf_counter() - t0
        return _payload(job, manifest.name, r, bundle, launched=True,
                        lane_note="verdict.run(bundle, providers=fixture) + registry.coerce_result")
    except Exception as ex:                                      # same contract as registry ERROR
        tb = traceback.format_exc(limit=8)
        r = _error_result(manifest, bundle, short_exc(ex), tb, time.perf_counter() - t0)
        return _payload(job, manifest.name, r, bundle, launched=True, lane_note="raised")


def _error_result(manifest, bundle, error: str, tb: str, runtime: float):
    from engine.common.result import PipelineResult
    return PipelineResult(pipeline=manifest.name, tile=bundle.tile.id, epoch=bundle.epoch,
                          status="ERROR", summary=f"pipeline raised {error}",
                          evidence=[{"error": error}], detail={"trace": tb}, runtime_s=runtime,
                          bundle_id=bundle.bundle_id)


# ------------------------------------------------------------------ payload
def _core_extent(bundle) -> tuple | None:
    """(xmin, xmax, ymin, ymax) pixel edges of the G80 CORE grid — the grid every evidence layer lives on."""
    try:
        g = bundle.grids.g80
        r0, r1, c0, c1 = g.core
        xs, ys = g.x[c0:c1], g.y[r0:r1]
        return (float(xs[0] - g.dx / 2), float(xs[-1] + g.dx / 2),
                float(ys[-1] - g.dy / 2), float(ys[0] + g.dy / 2))
    except Exception:
        return None


def _decode_layer(job, name: str, lname: str, lp: dict, extent, epsg: int) -> dict:
    """One encode_layer payload -> ArrayStore entry + a small descriptor (b64 never leaves the server)."""
    dt = np.dtype(lp.get("dtype", "<f4"))
    a = np.frombuffer(base64.b64decode(lp.get("data_b64", "")), dtype=dt)
    shape = lp.get("shape") or []
    a = a.reshape(shape) if shape and a.size == int(np.prod(shape)) else a.reshape(1, -1)
    kind = "int" if dt.kind in "iub" else "float"
    stride = 1
    if max(a.shape) > _MAX_STORED_PX:
        step = int(np.ceil(max(a.shape) / _MAX_STORED_PX))
        a, stride = a[::step, ::step], step
    cmap = _LayerCmap.get(str(lp.get("colormap", "")), "diverging" if str(lp.get("colormap", "")).startswith("diverging") else None)
    aid = f"pl_{name}_{lname}"
    job.store.put(Entry(aid, a, lname, extent, epsg if extent else None, stride, kind,
                        (lp.get("note") or "") + (f" · units {lp['units']}" if lp.get("units") else "")))
    return {"aid": aid, "name": lname, "shape": list(a.shape), "dtype": str(dt), "stride": stride,
            "colormap": cmap, "vmin": lp.get("vmin"), "vmax": lp.get("vmax"),
            "units": lp.get("units"), "note": lp.get("note"), "stats": raw_stats(a)}


def _payload(job, name: str, r, bundle, *, launched: bool, lane_note: str) -> dict:
    """PipelineResult -> UI payload. detail.layers is decoded into the ArrayStore and stripped."""
    if r is None:
        return {"name": name, "launched": launched, "lane_note": lane_note, "status": "UNKNOWN",
                "summary": "the registry returned no result for this pipeline", "note": "see HEALTH"}
    d = r.to_dict() if hasattr(r, "to_dict") else dict(r)
    detail = dict(d.get("detail") or {})
    layers_raw = detail.pop("layers", None) or {}
    extent, epsg = _core_extent(bundle), int(bundle.tile.epsg)
    layers = [_decode_layer(job, name, str(lname), lp, extent, epsg)
              for lname, lp in layers_raw.items() if isinstance(lp, dict) and lp.get("data_b64")]
    for scr in detail.get("forward_screens") or []:              # engine arrays never reach the wire
        if isinstance(scr, dict):
            scr.pop("_pred_sub", None)
    d["detail"] = detail
    d["layers_store"] = layers
    d["launched"] = launched
    d["lane_note"] = lane_note
    d["input_groups"] = next((groups for n, _, groups in FEATURED if n == name), ())
    return d
