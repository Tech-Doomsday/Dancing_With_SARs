"""Stage 3 — Retrieve: a visible, cancellable, resumable download of the clips for ONE tile.

Stages: resolve (search) -> plan (the granule list + caps) -> retrieve (this) -> store (clip store, per
granule, written the moment a granule finishes). A manifest per retrieval id is the contract with the
later stages. It carries ONLY what the engine measured or the provider stated; a value that is not
known is null, never estimated. Per-granule bytes are not reported yet (null) — see the build log.

Runs on the retrieval worker (JobRunner._pool), never on the metadata lane.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

STATES = ("queued", "fetching", "stored", "skipped", "failed", "cancelled")


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


class RetrievalManager:
    def __init__(self, runner, root: Path):
        self.runner = runner
        self.root = Path(root) / "retrievals"
        self._lock = threading.Lock()
        self._jobs: dict[str, dict] = {}
        self._retrievers: dict[str, object] = {}
        runner._extra_busy = self.busy            # /api/health reports a retrieval as busy too

    # ------------------------------------------------------------------ public
    def busy(self) -> dict | None:
        for rid, j in list(self._jobs.items()):
            if j["status"] in ("queued", "running"):
                return {"kind": "retrieval", "run_id": rid, "step": j["stage"], "started_at": j["started_at"]}
        return None

    def start(self, *, bbox: tuple, tile: str | None, products: list[str], product_names: dict[str, str],
              lookback_days: float, layers: str | None, max_granules: int | None,
              retriever_factory) -> str:
        if self.busy():
            raise RuntimeError(f"a retrieval is already running ({self.busy()['run_id']})")
        rid = "ret-" + uuid.uuid4().hex[:10]
        now = datetime.now(timezone.utc)
        job = {"id": rid, "status": "queued", "stage": "queued", "tile": tile, "bbox": list(bbox),
               "products": products, "layers": layers, "lookback_days": lookback_days,
               "max_granules": max_granules, "t0": _iso(now - timedelta(days=lookback_days)), "t1": _iso(now),
               "started_at": time.time(), "finished_at": None, "error": None, "flags": [], "granules": []}
        with self._lock:
            self._jobs[rid] = job
        self.runner._pool.submit(self._execute, rid, product_names, retriever_factory)
        return rid

    def get(self, rid: str) -> dict | None:
        with self._lock:
            j = self._jobs.get(rid)
            if j is not None:
                return json.loads(json.dumps(j))
        p = self.root / rid / "manifest.json"          # survives an engine restart
        if p.is_file():
            return json.loads(p.read_text())
        return None

    def cancel(self, rid: str) -> bool:
        with self._lock:
            j = self._jobs.get(rid)
            if j is None or j["status"] not in ("queued", "running"):
                return False
            j["cancel_requested"] = True
            r = self._retrievers.get(rid)
        if r is not None:
            r.cancel_requested = True                  # not-yet-started granules skip; in-flight ones finish
        return True

    # ------------------------------------------------------------------ worker
    def _row(self, j, gid):
        for g in j["granules"]:
            if g["granule_id"] == gid:
                return g
        return None

    def _write_manifest(self, j) -> None:
        d = self.root / j["id"]
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / f"manifest.json.{os.getpid()}.{threading.get_ident()}.tmp"
        tmp.write_text(json.dumps(j, indent=1, default=str))
        os.replace(tmp, d / "manifest.json")

    def _execute(self, rid, product_names, retriever_factory) -> None:
        j = self._jobs[rid]
        retriever = None
        try:
            with self._lock:
                j["status"], j["stage"] = "running", "finding granules"
            retriever = retriever_factory()
            with self._lock:
                self._retrievers[rid] = retriever
            bbox = tuple(j["bbox"])
            plan: list[tuple[str, list]] = []
            for lv in j["products"]:
                refs, degraded = retriever.search(bbox, j["t0"], j["t1"], product_names[lv])
                refs = sorted(refs, key=lambda r: r.start or "", reverse=True)       # newest first
                if j["max_granules"] and len(refs) > j["max_granules"]:
                    j["flags"].append(f"{lv}: {len(refs)} granules found, retrieving the newest {j['max_granules']}")
                    refs = refs[: j["max_granules"]]
                if degraded or getattr(retriever, "last_search_incomplete", False):
                    j["flags"].append(f"{lv}: the ASF listing may be incomplete (search_incomplete / stale cache)")
                plan.append((lv, refs))
                with self._lock:
                    for g in refs:
                        j["granules"].append({
                            "granule_id": g.granule_id, "product": lv, "start": g.start, "track": g.track,
                            "direction": g.direction, "frame": g.frame, "state": "queued", "source": None,
                            "seconds": None, "error": None, "clip_key": None,
                            "bytes_done": None, "bytes_planned": None})
            self._write_manifest(j)

            def hook(gid, state, seconds, detail):          # called from the retrieval client, per granule
                with self._lock:
                    row = self._row(j, gid)
                    if row is None:
                        return
                    if state == "fetching":
                        row["state"] = "fetching"
                    elif state == "failed":
                        row.update(state="failed", seconds=seconds, error=detail)
                    elif state == "skipped":
                        row.update(state="cancelled" if "cancel" in (detail or "") else "skipped",
                                   seconds=seconds, error=detail or None)
                    else:                                    # net / clip-cache / stale…: the clip is stored
                        row.update(state="stored", source=state, seconds=seconds)
                self._write_manifest(j)

            retriever.granule_hook = hook
            for lv, refs in plan:
                if not refs or j.get("cancel_requested"):
                    continue
                with self._lock:
                    j["stage"] = f"downloading {lv}"
                res = retriever.fetch(bbox, j["t0"], j["t1"], product_names[lv], j["layers"], granules=refs)
                for g in refs:
                    row = self._row(j, g.granule_id)
                    try:
                        row["clip_key"] = retriever.clips.key(g.granule_id, bbox, [j["layers"]] if j["layers"] else None)
                    except Exception:
                        pass
                for gid, msg in res.errors.items():
                    row = self._row(j, gid)
                    if row and row["state"] != "failed":
                        row.update(state="failed", error=msg)
            with self._lock:
                j["status"] = "cancelled" if j.get("cancel_requested") else "done"
                j["stage"] = j["status"]
        except Exception as ex:                              # reported, never swallowed
            with self._lock:
                j["status"], j["stage"], j["error"] = "failed", "failed", f"{type(ex).__name__}: {ex}"
        finally:
            with self._lock:
                j["finished_at"] = time.time()
                self._retrievers.pop(rid, None)
            try:
                self._write_manifest(j)
            except Exception:
                pass
            if retriever is not None:
                try:
                    retriever.close()
                except Exception:
                    pass
