#!/usr/bin/env python3
"""Live-engine seed for the Playwright LIVE config (§8.2) — SYNTHETIC data, labelled as such.

Builds a temp NISAR_DATA_DIR with a bundle produced through tests/common_fixtures.py (the same
fixtures the offline pytest suite uses — no invented fields), then serves engine.api.main from
that dir with:
  - the retriever factory patched to FakeRetriever over a multi-stack granule set
    (A005 with a real 26-day gap + a flipped pair + an unresolved pair, D013 descending)
    so /api/granules and scans run offline, deterministically;
  - the EQ event provider patched to the suite's calibrated fixture event
    (epicentre/nodal plane grid-searched in tests/test_api.py so the Okada forward prediction
    matches the truth bowl);
  - NISAR_ENABLE_SCANS=1 and a present EARTHDATA_TOKEN (so the UI enables Scan / Data).

All fixture dates are RELATIVE to today (UTC) so a scan at as_of=now always sees them inside
the engine's default lookback window (config/common.yaml window.lookback_days = 120).

Usage: python tests-e2e-live/seed_engine.py --port 8907
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))


def build_specs(base: datetime):
    """Granule set, relative to `base` (today, UTC midnight):
    A005 GCOV epochs at -100/-88/-76/-64 d, GUNW pairs (0,1) (1,2) (2,3) over them,
    a 5th pair at -38..-26 d (a 26-day break; median spacing 12 d -> threshold 18 d),
    a flipped pair (id carries later-first; P01 corrects to -100..-76 d, baseline 24 d),
    an unresolved pair (no parseable dates; kept, resolved=False), and a D013 stack."""
    from tests.common_fixtures import GranuleSpec, gunw_id, make_scene, ref_for

    def d(days_ago: int) -> datetime:
        return base - timedelta(days=days_ago)

    # the acceptance scene: +50 mm LOS bowl on the newest pair (EQ SUPPORTED under the
    # calibrated event) — epochs -100..-64, pairs spanning each 12-day step
    a = make_scene(n_epochs=4, pairs=((0, 1), (1, 2), (2, 3)), dlos=0.05,
                   start=d(100).strftime("%Y-%m-%d"))
    # the gap pair: 26 days after the last epoch (-64 d) -> exact "26 d gap" connector
    gap = GranuleSpec(ref_for(gunw_id(d(38), d(26)), "GUNW", d(38)),
                      "GUNW", ref_time=d(38), sec_time=d(26))
    # the flipped pair: the id carries the times in reversed order -> P01 marks flipped + corrects
    flipped_id = gunw_id(d(76), d(100))                    # "ref" slot = later, "sec" = earlier
    flipped = GranuleSpec(ref_for(flipped_id, "GUNW", d(100)), "GUNW",
                          ref_time=d(76), sec_time=d(100))
    # an unresolved pair: a GUNW id with no parseable timestamps -> kept, resolved=False
    unresolved_id = "NISAR_L2_PR_GUNW_001_005_A_219_4005_SHNA_notimes_X05010_N_F_J_001"
    unresolved = GranuleSpec(ref_for(unresolved_id, "GUNW", d(100)), "GUNW", ref_time=d(100))
    # a descending stack so the timeline has a second row (D013)
    desc = make_scene(n_epochs=3, pairs=((0, 1),), track=13, direction="D",
                      start=d(98).strftime("%Y-%m-%d"))
    return a + [gap, flipped, unresolved] + desc


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8907)
    args = ap.parse_args()

    from tests.common_fixtures import FakeRetriever, default_tile, make_scene, write_ancillary
    from engine.common import DirectoryAncillary
    from engine.common.chain import build_scene_bundle
    from engine.common.bundle_io import save_bundle

    base = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    data_dir = Path(tempfile.mkdtemp(prefix="nisar-live-e2e-"))
    specs = build_specs(base)
    tile = default_tile()
    # bundle from the CLEAN acceptance scene (no unresolved pairs in the persisted bundle);
    # its last pair spans -76..-64 d, so the event sits at -70 d, inside that pair's window
    bundle_specs = make_scene(n_epochs=4, pairs=((0, 1), (1, 2), (2, 3)), dlos=0.05,
                              start=(base - timedelta(days=100)).strftime("%Y-%m-%d"))
    window = (base - timedelta(days=75), base - timedelta(days=65))
    bundle = build_scene_bundle(
        tile, FakeRetriever(bundle_specs),
        as_of=base.strftime("%Y-%m-%dT%H:%M:%SZ"),
        ancillary=DirectoryAncillary(write_ancillary(data_dir / "ancillary", tile)),
        event_window=tuple(t.strftime("%Y-%m-%dT%H:%M:%SZ") for t in window),
        triggers={"quake": {"id": "us6000-live"}})
    save_bundle(bundle, data_dir / "bundles")
    print(f"[seed] synthetic bundle {bundle.bundle_id} in {data_dir}", flush=True)

    # patch the process the way tests/test_api.py's client fixture does, then serve
    import engine.common.config as engine_config
    import engine.registry as engine_registry
    import engine.api.state as api_state
    import engine.api.jobs as api_jobs
    from engine.api.main import create_app

    for mod in (engine_config, engine_registry, api_state):
        mod.data_root = lambda: data_dir  # type: ignore[assignment]
    api_jobs._default_retriever_factory = lambda: (lambda: FakeRetriever(specs))  # type: ignore[assignment]

    from engine.pipelines.earthquake_coseismic import fetch as eq_fetch
    from pyproj import Transformer

    def provider(bbox, t0, t1):
        tr = Transformer.from_crs(tile.epsg, 4326, always_xy=True)
        lon, lat = tr.transform(tile.e0 + 1000.0, tile.n0 + 2500.0)
        ev = {"id": "us6000-live", "magnitude": 5.6, "place": "synthetic fixture event",
              "time": (base - timedelta(days=70)).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "lat": float(lat), "lon": float(lon),
              "depth_km": 5.0, "url": None, "nodal_planes": ((90.0, 40.0, -90.0),)}
        return {"source": "fixture", "events": [ev], "degraded": False, "error": None}

    eq_fetch.set_event_provider(provider)

    os.environ["EARTHDATA_TOKEN"] = "synthetic-e2e-token-not-a-secret"
    os.environ["NISAR_ENABLE_SCANS"] = "1"

    api_state.get_runner.cache_clear()
    app = create_app()

    import uvicorn

    print(f"[seed] serving engine on http://127.0.0.1:{args.port} (SYNTHETIC data)", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
