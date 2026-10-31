#!/usr/bin/env python3
"""Featured areas CLI (DEC4) — the owner curates the globe UI's featured list. Seeded EMPTY.

    python tools/feature_area.py add --slug rayleigh-island --title "Rayleigh Island subsidence" \
        --blurb "..." --tile 11SLT66 [--zoom 12] [--sort 10]
    python tools/feature_area.py add --slug ... --lon -118.25 --lat 34.05   # tile resolved by the engine
    python tools/feature_area.py list
    python tools/feature_area.py remove --slug rayleigh-island

Rules: the tile is resolved by the ENGINE (engine.tiling.mgrs) — either from --tile (which must
already exist as an MGRS id, checked, never accepted blind) or from --lon/--lat. Unknown tiles are
refused; nothing is auto-created; no verdicts are fabricated. Needs SUPABASE_URL +
SUPABASE_SERVICE_KEY (service-role; writes bypass RLS). Stdlib urllib only, like CloudCache.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.common.config import data_root  # noqa: E402  (consistent with backfill_supabase)
from engine.retrieval.settings import load_settings  # noqa: E402
from engine.tiling.mgrs import tile_at, tile_from_id  # noqa: E402

TABLE = "featured_areas"


def _env() -> tuple[str, str]:
    load_settings()
    url = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or ""
    if not url or not key:
        raise SystemExit("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set (service-role, server-side only)")
    return url, key


def _request(url: str, key: str, method: str, query: str = "", body: list | None = None) -> tuple[int, bytes]:
    req = urllib.request.Request(
        f"{url}/rest/v1/{TABLE}{('?' + query) if query else ''}",
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        method=method,
        headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "Prefer": "return=representation"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as ex:
        return ex.code, ex.read()


def resolve_tile(args) -> tuple[str, float, float]:
    """-> (tile_id, center_lon, center_lat) — resolved/validated by the ENGINE, never by hand."""
    if args.tile:
        try:
            tile = tile_from_id(args.tile)
        except ValueError as ex:
            raise SystemExit(f"refused: {ex}") from ex
        try:
            existing = tile_at(*tile.center, size=tile.size)
        except ValueError as ex:
            raise SystemExit(f"refused: {ex}") from ex
        if existing.id != tile.id:
            raise SystemExit(f"refused: tile {tile.id} is not in the engine's grid for its centre "
                             f"(engine resolves {existing.id}) — check for a typo")
        lon, lat = tile.center
        return tile.id, float(lon), float(lat)
    if args.lon is None or args.lat is None:
        raise SystemExit("give either --tile ID or --lon LON --lat LAT")
    if not (-180.0 <= args.lon <= 180.0 and -90.0 <= args.lat <= 90.0):
        raise SystemExit("refused: lon must be in [-180, 180] and lat in [-90, 90]")
    try:
        size = int(load_settings().tiling.get("cell_size_m", 10_000))
        tile = tile_at(args.lon, args.lat, size=size)
    except ValueError as ex:
        raise SystemExit(f"refused: {ex}") from ex
    lon, lat = tile.center
    return tile.id, float(lon), float(lat)


def cmd_add(args) -> int:
    url, key = _env()
    tile_id, lon, lat = resolve_tile(args)
    record = {"slug": args.slug, "title": args.title, "blurb": args.blurb, "tile": tile_id,
              "center_lon": lon, "center_lat": lat, "zoom": args.zoom, "sort_order": args.sort}
    status, body = _request(url, key, "POST", body=[record])
    if status in (200, 201):
        print(f"added {args.slug}: tile {tile_id} at ({lon:.5f}, {lat:.5f})")
        return 0
    print(f"Supabase refused the write (HTTP {status}): {body.decode('utf-8', 'replace')[:300]}")
    return 1


def cmd_list(_args) -> int:
    url, key = _env()
    status, body = _request(url, key, "GET", query="select=slug,title,blurb,tile,center_lon,center_lat,zoom,sort_order&order=sort_order.asc,slug.asc")
    if status != 200:
        print(f"Supabase refused the read (HTTP {status}): {body.decode('utf-8', 'replace')[:300]}")
        return 1
    rows = json.loads(body or b"[]")
    if not rows:
        print("no featured areas yet — the list is seeded empty by design (DEC4); add real ones with 'add'")
        return 0
    for r in rows:
        print(f"{r['slug']:32s} {r['tile']:10s} ({r['center_lon']:.5f}, {r['center_lat']:.5f}) "
              f"sort={r['sort_order']:3d}  {r['title']}" + (f" — {r['blurb']}" if r.get("blurb") else ""))
    return 0


def cmd_remove(args) -> int:
    url, key = _env()
    query = urllib.parse.quote(f"slug=eq.{args.slug}", safe="=&._-")
    status, body = _request(url, key, "DELETE", query=query)
    if status in (200, 204):
        print(f"removed {args.slug} (if it existed)")
        return 0
    print(f"Supabase refused the delete (HTTP {status}): {body.decode('utf-8', 'replace')[:300]}")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    add = sub.add_parser("add", help="add one featured area (engine resolves the tile)")
    add.add_argument("--slug", required=True, help="stable id, e.g. 'los-angeles-basin'")
    add.add_argument("--title", required=True)
    add.add_argument("--blurb", default=None)
    group = add.add_mutually_exclusive_group(required=True)
    group.add_argument("--tile", help="MGRS tile id (validated against the engine's grid)")
    group.add_argument("--lon", type=float, help="longitude; the engine resolves the tile containing it")
    add.add_argument("--lat", type=float, help="latitude (with --lon); the engine resolves the tile")
    add.add_argument("--zoom", type=float, default=None)
    add.add_argument("--sort", type=int, default=0)
    add.set_defaults(func=cmd_add)

    lst = sub.add_parser("list", help="list featured areas")
    lst.set_defaults(func=cmd_list)

    rm = sub.add_parser("remove", help="remove one featured area by slug")
    rm.add_argument("--slug", required=True)
    rm.set_defaults(func=cmd_remove)

    args = ap.parse_args()
    if not getattr(args, "tile", None):
        if args.lon is None or getattr(args, "lat", None) is None:
            raise SystemExit("give either --tile ID or --lon LON --lat LAT")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
