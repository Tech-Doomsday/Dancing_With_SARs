"""Committed fixture generator for the Granule Explorer's Playwright specs (§8.2).

Builds SYNTHETIC GranuleRefs (clearly labelled: every id starts with NISAR_L2_PR_… and the
timestamps are fixed values) and runs them through the REAL engine enrichment code
(engine.api.granules_bbox.search_bbox_sync) with a minimal fake retriever — exactly the seam
the engine itself uses. The result is written to app-ui/tests-e2e/fixtures/granules-bbox.json
and committed; the e2e spec intercepts /api/granules* and serves it (labelled as fixtures).
Test-side interception of third parties is allowed by the task (§0); production paths stay real.

Run: python tools/gen_explorer_fixture.py   (offline; never touches the network)
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.api.granules_bbox import search_bbox_sync  # noqa: E402
from engine.retrieval.clients.types import GranuleRef  # noqa: E402

AS_OF = "2026-10-30T00:00:00Z"
T0 = "2026-06-01T00:00:00Z"
T1 = "2026-10-30T00:00:00Z"
WEST, SOUTH, EAST, NORTH = -118.45, 33.7, -118.05, 34.3

# The TASK's test vector, verbatim (the owner's screenshot granule)
TEST_VECTOR = ("NISAR_L2_PR_GUNW_031_034_A_019_033_4000_SH_20260920T130823_20260920T130857_"
               "20261014T130823_20261014T130857_P05023_N_F_J_001")


def gunw_id(cycle: int, track: int, frame: int, sec_cycle: int, ref: datetime, sec: datetime) -> str:
    stamp = lambda d: d.strftime("%Y%m%dT%H%M%S")  # noqa: E731
    return (f"NISAR_L2_PR_GUNW_{cycle:03d}_{track:03d}_A_{frame:03d}_{sec_cycle:03d}_4000_SH_"
            f"{stamp(ref)}_{stamp(ref + timedelta(seconds=34))}_{stamp(sec)}_"
            f"{stamp(sec + timedelta(seconds=34))}_P05023_N_F_J_001")


def gcov_id(cycle: int, track: int, frame: int, t: datetime) -> str:
    return (f"NISAR_L2_PR_GCOV_{cycle:03d}_{track:03d}_A_{frame:03d}_4005_SHNA_A_"
            f"{t.strftime('%Y%m%dT%H%M%S')}_{(t + timedelta(seconds=30)).strftime('%Y%m%dT%H%M%S')}"
            f"_P05023_N_F_J_001")


def ring(lon: float, lat: float, half_km: float = 35.0) -> list:
    dlon = half_km / (111.0 * max(0.2, __import__("math").cos(__import__("math").radians(lat))))
    dlat = half_km / 111.0
    return [[lon - dlon, lat - dlat], [lon + dlon, lat - dlat], [lon + dlon, lat + dlat], [lon - dlon, lat + dlat]]


def make_refs() -> list[GranuleRef]:
    """A believable stack family around Los Angeles: two ascending tracks, one descending,
    GUNW pairs on the 12-day repeat plus GCOV epochs — SYNTHETIC ids and footprints."""
    refs: list[GranuleRef] = []
    t0 = datetime(2026, 6, 20, 13, 8, 23, tzinfo=timezone.utc)
    # ascending track 34, frame 19: GUNW pairs every 24 days + the TASK vector among them
    for i, (c, sc) in enumerate(((27, 29), (29, 31), (31, 33))):
        ref = t0 + timedelta(days=24 * i)
        sec = ref + timedelta(days=24)
        gid = TEST_VECTOR if i == 2 else gunw_id(c, 34, 19, sc, ref, sec)
        refs.append(GranuleRef(gid, f"https://example.invalid/{gid}.h5", "GUNW",
                               ref.strftime("%Y-%m-%dT%H:%M:%SZ"),
                               (ref + timedelta(seconds=34)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                               34, 19, "ASCENDING", 231_000_000, ring(-118.25, 34.05),
                               {"collectionName": "NISAR_L2_GUNW_PROVISIONAL_V1", "orbitType": "MOE",
                                "mainBandPolarization": "HH", "rangeBandwidth": 40, "crid": "P05023"}))
    # ascending track 34 GCOV epochs (12-day repeat)
    for i in range(8):
        t = t0 + timedelta(days=12 * i)
        gid = gcov_id(27 + 2 * i, 34, 19, t)
        refs.append(GranuleRef(gid, f"https://example.invalid/{gid}.h5", "GCOV",
                               t.strftime("%Y-%m-%dT%H:%M:%SZ"),
                               (t + timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                               34, 19, "ASCENDING", 118_000_000, ring(-118.22, 34.02),
                               {"collectionName": "NISAR_L2_GCOV_PROVISIONAL_V1", "orbitType": "MOE",
                                "mainBandPolarization": "HH", "rangeBandwidth": 40}))
    # descending track 71: GCOV epochs (a second lane family)
    t0d = datetime(2026, 6, 26, 1, 10, 0, tzinfo=timezone.utc)
    for i in range(6):
        t = t0d + timedelta(days=12 * i)
        gid = gcov_id(27 + 2 * i, 71, 45, t)
        refs.append(GranuleRef(gid.replace("_A_", "_D_"), f"https://example.invalid/{gid}.h5", "GCOV",
                               t.strftime("%Y-%m-%dT%H:%M:%SZ"),
                               (t + timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                               71, 45, "DESCENDING", 121_000_000, ring(-118.31, 34.11),
                               {"collectionName": "NISAR_L2_GCOV_PROVISIONAL_V1", "orbitType": "MOE"}))
    return refs


class FakeRetriever:
    """The one method the enrichment uses; product-filtered like the real NisarClient."""

    def __init__(self, refs: list[GranuleRef]):
        self.refs = refs

    def search(self, bbox, t0=None, t1=None, product=None, maturity=None, fresh=False, max_results=None):
        return [r for r in self.refs if r.product == product], False

    def close(self) -> None:
        pass


def main() -> None:
    out_dir = ROOT / "app-ui" / "tests-e2e" / "fixtures"
    out_dir.mkdir(parents=True, exist_ok=True)
    retriever = FakeRetriever(make_refs())
    body = search_bbox_sync(bbox=(WEST, SOUTH, EAST, NORTH), levels=["GUNW", "GCOV"],
                            t0=T0, t1=T1,
                            product_names={"GUNW": "GUNW", "GCOV": "GCOV"},
                            max_results=250, fresh=False, retriever=retriever)
    assert body["n_total"] >= 10, body["n_total"]
    assert any(g["id"] == TEST_VECTOR for g in body["granules"])
    header = {
        "_comment": ("FIXTURE — synthetic GranuleRefs run through the REAL engine enrichment "
                     "(engine/api/granules_bbox.py) by tools/gen_explorer_fixture.py; served by "
                     "Playwright page.route. Not live data."),
    }
    (out_dir / "granules-bbox.json").write_text(json.dumps({**header, **body}, indent=1))
    print(f"fixture written: {out_dir / 'granules-bbox.json'} ({body['n_total']} granules, "
          f"{len(body['stacks'])} stacks)")


if __name__ == "__main__":
    main()
