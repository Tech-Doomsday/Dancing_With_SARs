"""find_granule: a pair granule is found whichever date the provider lists it under, and a capped search is never
reported as 'does not exist'. No network: a fake provider that applies asf_search's rules (time OVERLAP, box, maxResults)."""
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest

from engine.api.background import BackgroundError, find_granule

GID = ("NISAR_L2_PR_GUNW_025_091_D_077_029_2000_SV_20260714T123456_20260714T123531_"
       "20260831T123454_20260831T123529_P05023_N_F_J_001")
REF = datetime(2026, 7, 14, 12, 34, 56, tzinfo=timezone.utc)
SEC = datetime(2026, 8, 31, 12, 34, 54, tzinfo=timezone.utc)
BOX = (-119.0, 33.5, -117.0, 35.0)


def _z(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _g(gid, start, stop, inside=True):
    return NS(granule_id=gid, start=start, stop=stop, inside=inside)


class Provider:
    """Granules overlapping [t0, t1] (like CMR), optionally inside the box, cut at max_results."""

    def __init__(self, granules, cap_hit_reports=True):
        self.granules = granules
        self.client = NS(last_search_truncated=False)
        self.calls = []

    def search(self, bbox, t0, t1, product, fresh=False, max_results=50):
        self.calls.append((bbox, t0, t1, max_results))
        a, b = _z(t0), _z(t1)
        hit = [g for g in self.granules if g.start <= b and g.stop >= a and (bbox is None or g.inside)]
        self.client.last_search_truncated = len(hit) >= max_results
        return hit[:max_results], False


def _crowd(n, span_start=REF, span_end=SEC):
    """n unrelated GUNWs, each listed over its own ref..sec span that covers the target's reference date."""
    return [_g(f"other_{i}", span_start, span_end, inside=False) for i in range(n)]


def test_found_when_the_provider_lists_the_pair_under_its_secondary_date():
    p = Provider([_g(GID, SEC, SEC)])
    ref, dec = find_granule(p, GID)
    assert ref.granule_id == GID and dec["ref_time"] == "2026-07-14T12:34:56Z"
    assert len(p.calls) == 2                       # the reference window found nothing, the secondary window did


def test_found_when_the_provider_lists_the_pair_over_its_whole_span():
    p = Provider([_g(GID, REF, SEC)])
    assert find_granule(p, GID)[0].granule_id == GID


def test_a_crowded_world_window_is_beaten_by_the_box():
    # 400 other pairs overlap the reference date world-wide; the target sits after the 250-result cap
    p = Provider(_crowd(400) + [_g(GID, REF, SEC)])
    with pytest.raises(BackgroundError) as e:
        find_granule(p, GID)                       # no box: honest miss ...
    assert e.value.status == 404 and "result limit" in str(e.value) and "NOT proof" in str(e.value)
    ref, _ = find_granule(p, GID, BOX)             # ... with the box it is found on the first call
    assert ref.granule_id == GID and p.calls[-1][0] == BOX


def test_a_clean_miss_does_not_claim_a_cap_and_says_what_was_tried():
    p = Provider([])
    with pytest.raises(BackgroundError) as e:
        find_granule(p, GID)
    msg = str(e.value)
    assert "result limit" not in msg and "2026-07-14T10:34:56Z" in msg and "2026-08-31T10:34:54Z" in msg


def test_by_name_lookup_is_the_last_resort():
    target = _g(GID, REF, REF)
    p = Provider([])
    p.find_by_id = lambda gid: target if gid == GID else None
    assert find_granule(p, GID)[0] is target
    p.find_by_id = lambda gid: (_ for _ in ()).throw(RuntimeError("provider down"))
    with pytest.raises(BackgroundError):
        find_granule(p, GID)                       # a failing by-name call changes nothing: still the honest 404


def test_an_undecodable_id_is_refused_before_any_search():
    p = Provider([])
    with pytest.raises(BackgroundError) as e:
        find_granule(p, "not_a_nisar_name")
    assert e.value.status == 404 and p.calls == []
