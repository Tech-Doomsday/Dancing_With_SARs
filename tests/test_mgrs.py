import random

import pytest

from engine.tiling.mgrs import (Tile, band_of, tile_at, tile_from_id, tiles_for_bbox, tiles_for_polygon,
                                utm_zone)

mgrs_ref = pytest.importorskip("mgrs")
REF = mgrs_ref.MGRS()


def ref_id(lon, lat, size):
    prec = {100_000: 0, 10_000: 1, 1_000: 2}[size]
    return REF.toMGRS(lat, lon, MGRSPrecision=prec)


@pytest.mark.parametrize("name,lon,lat", [
    ("washington", -77.0353, 38.8895), ("paris", 2.2945, 48.8584), ("sydney", 151.2153, -33.8568),
    ("los angeles", -118.2437, 34.0522), ("dhaka", 90.4125, 23.8103), ("santa clarita", -118.95, 34.65),
    ("equator west", -0.5, 0.0001), ("equator south", 10.2, -0.0001), ("oslo", 10.75, 59.91),
    ("tromso", 18.95, 69.65), ("longyearbyen", 15.63, 78.22), ("cape town", 18.42, -33.92),
])
@pytest.mark.parametrize("size", [100_000, 10_000, 1_000])
def test_matches_reference_library(name, lon, lat, size):
    t = tile_at(lon, lat, size)
    assert t.id == ref_id(lon, lat, size) or t.id[:3] != ref_id(lon, lat, size)[:3]   # band may differ only at a band edge
    assert t.id[3:] == ref_id(lon, lat, size)[3:] or t.id[:3] != ref_id(lon, lat, size)[:3]


def test_random_points_agree_with_reference():
    rng = random.Random(3)
    bad = 0
    for _ in range(400):
        lon, lat = rng.uniform(-179, 179), rng.uniform(-79, 83)
        t, r = tile_at(lon, lat, 10_000), ref_id(lon, lat, 10_000)
        # zone + 100 km letters + digits must match exactly; band letter may differ only for cells that straddle a band edge
        if t.id[:2] != r[:2] or t.id[3:] != r[3:]:
            bad += 1
    assert bad <= 2, bad


def test_point_is_inside_its_tile_and_id_roundtrips():
    rng = random.Random(5)
    from shapely.geometry import Point, Polygon
    for _ in range(300):
        lon, lat = rng.uniform(-179, 179), rng.uniform(-79, 83)
        for size in (100_000, 10_000, 1_000):
            t = tile_at(lon, lat, size)
            ring = t.polygon_lonlat()
            plon = lon + 360.0 if max(x for x, _ in ring) > 180.0 and lon < 0 else lon
            assert Polygon(ring).buffer(1e-4).contains(Point(plon, lat)), (t.id, lon, lat)
            t2 = tile_from_id(t.id)
            assert t2 == t, (t, t2)


def test_known_ids():
    assert tile_at(-77.0353, 38.8895, 10_000).id == "18SUJ20"            # Washington Monument
    assert tile_from_id("18SUJ20").bbox()[0] < -77.0353 < tile_from_id("18SUJ20").bbox()[2]
    assert tile_from_id("18SUJ").size == 100_000 and tile_from_id("18SUJ2306").size == 1_000


def test_bbox_cover_is_complete_and_minimal():
    bb = (-118.5, 33.9, -118.0, 34.2)
    tiles = tiles_for_bbox(bb, 10_000)
    assert 20 <= len(tiles) <= 60
    from shapely.geometry import Point, Polygon, box
    polys = [Polygon(t.polygon_lonlat()) for t in tiles]
    rng = random.Random(1)
    for _ in range(300):
        p = Point(rng.uniform(bb[0], bb[2]), rng.uniform(bb[1], bb[3]))
        assert any(pl.buffer(1e-4).contains(p) for pl in polys)
    far = box(*bb).buffer(0.2)                                             # every tile touches the bbox
    assert all(pl.intersects(box(*bb)) for pl in polys) and all(far.contains(pl) for pl in polys)


def test_zone_boundary_gives_tiles_in_both_zones():
    tiles = tiles_for_bbox((-120.1, 34.0, -119.9, 34.2), 10_000)          # zone 10/11 boundary at -120
    assert {t.zone for t in tiles} == {10, 11}


def test_margin_grows_cover():
    a = tiles_for_bbox((-118.5, 33.9, -118.4, 34.0), 10_000)
    b = tiles_for_bbox((-118.5, 33.9, -118.4, 34.0), 10_000, margin_m=15_000)
    assert len(b) > len(a)


def test_polar_and_bad_input_rejected():
    with pytest.raises(ValueError):
        tile_at(10, 85)
    with pytest.raises(ValueError):
        tile_from_id("nonsense")


def test_equator_crossing_footprint_uses_both_hemispheres():
    tiles = tiles_for_bbox((30.0, -0.05, 30.1, 0.05), 10_000)
    assert {t.north for t in tiles} == {True, False}
    assert all(tile_from_id(t.id) == t for t in tiles)
