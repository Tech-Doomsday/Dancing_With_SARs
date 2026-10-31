"""5 — GET /api/tiles, GET /api/tiles/{id}/verdicts (canonical verdicts from 4.5)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .state import get_runner

router = APIRouter()


@router.get("/api/tiles")
def list_tiles(limit: int = 100):
    """Tiles known to the verdict store, with their latest verdict summary."""
    runner = get_runner()
    rows = runner.verdicts.latest_verdicts(limit=limit * 10)
    by_tile: dict = {}
    for r in rows:
        by_tile.setdefault(r.get("tile") or "?", []).append(r)
    out = []
    for tile in sorted(by_tile):
        verdicts = by_tile[tile]
        out.append({
            "tile": tile,
            "n_verdicts": len(verdicts),
            "latest": [{"pipeline": v["pipeline"], "status": v["status"], "tier": v.get("tier"),
                        "raw_p": v.get("raw_p"), "written_at": v.get("written_at")}
                       for v in verdicts[:5]],
        })
    return {"ok": True, "tiles": out[:limit], "note": "canonical verdicts live in verdicts.db (4.5)"}


@router.get("/api/tiles/{tile_id}/verdicts")
def tile_verdicts(tile_id: str):
    """Every stored verdict for one tile (all pipelines, latest write per pipeline/bundle)."""
    runner = get_runner()
    verdicts = runner.verdicts.verdicts_for_tile(tile_id)
    if not verdicts:
        raise HTTPException(status_code=404, detail=f"no verdicts stored for tile {tile_id!r}")
    return {"ok": True, "tile": tile_id, "verdicts": verdicts}
