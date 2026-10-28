"""4.6 cloud cache — mirror every finished verdict into Supabase (write side).

The engine stays the only thing that computes. After a run has been calibrated (4.2) and persisted
to verdicts.db (4.5), the same rows are upserted into the Supabase `verdicts` table
(supabase/migrations/0001_verdict_cache.sql) so the UI can show them instantly, with the engine
offline, and so a visitor never has to wait for a recompute.

Rules (they keep the public cache honest):
  * Best-effort, like persist: a Supabase failure never blocks or fails a run. It is reported in
    the returned dict (and surfaced in the run state) — never swallowed, never raised.
  * Not configured (no SUPABASE_URL / key) -> disabled, with the reason in the report. Never a
    silent success.
  * ERROR and PENDING results are NOT cached: they are not verdicts, and a failed redo must never
    overwrite a good cached verdict.
  * Runs with custom parameter overrides are NOT cached: the cache holds engine-default verdicts,
    so experimenting with thresholds cannot change what other people see.
  * Non-finite numbers become null (Postgres jsonb rejects NaN/Infinity); numpy values are
    converted to plain Python. Nothing else is altered: statuses and numbers are stored verbatim.
  * The service key is only ever sent as a request header; it is never logged or put in a report.

Stdlib only (urllib): no new dependency.
"""
from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

TABLE = "verdicts"
CONFLICT_KEY = "tile,pipeline,bundle_id"

# status -> why it is not cached
NOT_CACHED = {
    "ERROR": "ERROR is a failed run, not a verdict — never cached (a redo must not overwrite a good verdict)",
    "PENDING": "PENDING means the heavy lane has not finished — not a verdict yet",
}
CUSTOM_PARAMS = "custom parameters — the cache holds engine-default verdicts only"


class CacheWriteError(RuntimeError):
    """A write that Supabase refused or that could not reach it (message is safe to show)."""


def _plain(x):
    """JSON-safe copy: numpy -> python, NaN/±Inf -> None, keys -> str. Values are otherwise untouched."""
    if isinstance(x, dict):
        return {str(k): _plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_plain(v) for v in x]
    if hasattr(x, "tolist"):            # numpy scalars and arrays
        return _plain(x.tolist())
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    return x


def to_record(row: dict, *, run_id: str | None, bundle_id: str, tile: str, now: str) -> dict:
    """Verdict row (engine.verdict.collect + calibrate) -> one `verdicts` table record.

    The layer grids (detail.layers, base64) go in their own column so a board listing never has
    to download them; everything else of the row is kept whole in `result`."""
    detail = dict(row.get("detail") or {})
    layers = detail.pop("layers", None) or {}
    result = {**row, "detail": detail}
    return {
        "tile": row.get("tile") or tile,
        "pipeline": row["pipeline"],
        "bundle_id": row.get("bundle_id") or bundle_id,
        "epoch": row.get("epoch"),
        "status": row["status"],
        "tier": row.get("tier"),
        "raw_p": row.get("raw_p"),
        "summary": row.get("summary") or "",
        "result": result,
        "layers": layers,
        "run_id": run_id,
        "computed_at": now,       # first_computed_at is omitted on purpose: the DB default sets it
    }                             # once, and merge-duplicates never touches a column it is not sent


class CloudCache:
    def __init__(self, url: str | None = None, key: str | None = None, *, timeout_s: float = 20.0):
        self.url = (url or "").rstrip("/")
        self._key = key or ""
        self.timeout_s = timeout_s
        self.disabled_reason: str | None = None
        if not self.url or not self._key:
            self.disabled_reason = "not configured (set SUPABASE_URL and SUPABASE_SERVICE_KEY)"
        else:
            p = urllib.parse.urlparse(self.url)
            if p.scheme != "https" and not (p.scheme == "http" and p.hostname in ("localhost", "127.0.0.1", "::1")):
                self.disabled_reason = "SUPABASE_URL must be https (plain http is accepted for localhost only)"

    @classmethod
    def from_env(cls) -> "CloudCache":
        key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        return cls(os.environ.get("SUPABASE_URL"), key)

    @property
    def enabled(self) -> bool:
        return self.disabled_reason is None

    def __repr__(self) -> str:      # the key must never reach a log line
        return f"CloudCache(url={self.url!r}, enabled={self.enabled})"

    # ------------------------------------------------------------------ write
    def write_run(self, rows: list, *, run_id: str | None, bundle_id: str, tile: str,
                  params: dict | None = None, computed_at: str | None = None) -> dict:
        """Upsert one run's verdict rows. Returns {enabled, written, skipped[], failed[]} — never raises.
        `computed_at` (ISO-8601 UTC) is only for the backfill tool, which keeps the original run time."""
        report: dict = {"enabled": self.enabled, "written": 0, "skipped": [], "failed": []}
        if not self.enabled:
            report["reason"] = self.disabled_reason
            return report
        now = computed_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
        records = []
        for row in rows:
            name = row.get("pipeline")
            if params:
                report["skipped"].append({"pipeline": name, "reason": CUSTOM_PARAMS})
            elif row.get("status") in NOT_CACHED:
                report["skipped"].append({"pipeline": name, "reason": NOT_CACHED[row["status"]]})
            elif not name or not row.get("status"):
                report["skipped"].append({"pipeline": name, "reason": "row has no pipeline/status"})
            else:
                records.append(to_record(row, run_id=run_id, bundle_id=bundle_id, tile=tile, now=now))
        if not records:
            return report
        try:
            self._upsert(records)
            report["written"] = len(records)
        except CacheWriteError as ex:
            report["failed"].append(str(ex))
        except Exception as ex:  # noqa: BLE001 — best-effort mirror: reported, never raised
            report["failed"].append(f"{type(ex).__name__}: {ex}")
        return report

    def _upsert(self, records: list) -> None:
        url = f"{self.url}/rest/v1/{TABLE}?on_conflict={CONFLICT_KEY}"
        body = json.dumps(_plain(records), allow_nan=False, default=str).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST", headers={
            "apikey": self._key,
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
            # one statement for the whole run: either every verdict lands or none does
            "Prefer": "resolution=merge-duplicates,return=minimal",
        })
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                resp.read()
        except urllib.error.HTTPError as ex:
            detail = ex.read().decode("utf-8", "replace")[:300]
            raise CacheWriteError(f"Supabase rejected the write (HTTP {ex.code}): {detail}") from ex
        except urllib.error.URLError as ex:
            raise CacheWriteError(f"Supabase unreachable: {ex.reason}") from ex


__all__ = ["CloudCache", "CacheWriteError", "to_record", "NOT_CACHED", "CUSTOM_PARAMS"]
