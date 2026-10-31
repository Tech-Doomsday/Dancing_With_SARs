# Supabase verdict cache

Every verdict the engine finishes is also saved in Supabase, so the UI can open it instantly (even
with the engine offline) and a visitor never waits for a recompute. **Redo** re-runs the engine and
replaces the cached verdict. Nothing about how verdicts are computed changed.

```
engine run ─► verdicts.db (as before) ─► Supabase `verdicts`  ─► app-ui reads it directly (anon, read-only)
                                              ▲
UI "Redo verdict" ─► POST /api/runs ─► engine recomputes ─► overwrites the row
```

## Set up (once)

1. In the Supabase SQL editor run `supabase/migrations/0001_verdict_cache.sql` (idempotent), then
   `supabase/migrations/0002_locations_and_featured.sql` (idempotent) — 0002 adds the tile's
   location columns (center_lon/center_lat/bbox_lonlat) to `verdicts` and creates the owner-curated
   `featured_areas` table (public read, service-role write).
2. Engine `.env`: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` (service-role key; server-side only).
3. `app-ui/.env.local`: `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY` (see `app-ui/.env.example`).
4. Push verdicts you already computed: `python tools/backfill_supabase.py --dry-run`, then without `--dry-run`.
5. Backfill the location columns of rows pushed before 0002:
   `python tools/backfill_supabase.py --fill-locations --dry-run`, then without `--dry-run`
   (PATCH-only, by primary key; needs the bundles on disk — rows without a local bundle.json are
   skipped and counted, never guessed).
6. If rows were cached before the D15 fix, re-tier them from their stored status:
   `python tools/backfill_supabase.py --retier --dry-run`, then without `--dry-run`
   (PATCH-only; adds the "quiet" tier for NO_SIGNAL).

## Featured areas (the globe UI's curated list)

`featured_areas` is seeded EMPTY — nothing is invented. The owner adds real entries with the CLI:

    python tools/feature_area.py add --slug los-angeles-basin --title "Los Angeles basin" \
        --blurb "…" --lon -118.25 --lat 34.05 --zoom 11 --sort 10
    python tools/feature_area.py list
    python tools/feature_area.py remove --slug los-angeles-basin

The tile is always resolved by the ENGINE (`engine.tiling.mgrs`): pass either `--tile` (validated
against the engine's grid — unknown tiles are refused) or `--lon/--lat` (the engine resolves the
containing cell). No verdicts are created; an entry whose tile has no cached result shows
"not scanned yet" in the UI until a real scan fills it.

Unset keys are fine: the engine reports "cache off" on each run and the UI works as before.

## What is cached (and what is not)

* Key = `(tile, pipeline, bundle_id)` — the engine's own verdict key. `bundle_id` is content-addressed,
  so the same key means the same inputs. A redo overwrites in place; `first_computed_at` is kept.
* Stored verbatim: status, tier, rank score, summary, the whole verdict row (evidence, warnings,
  data used, method). Layer grids live in their own column and are fetched only when the map needs them.
* **Not cached:** `ERROR` / `PENDING` results (not verdicts — a failed redo can never replace a good
  verdict) and runs with **custom parameters** (the public cache holds engine-default verdicts only).
  Every run says which of these happened (`cache` in the run state, shown on the run page).

## Security model

* Reads are public (RLS `select` policy). There is no insert/update/delete policy: only the
  service-role key can write, and only the engine holds it.
* The Redo button calls the engine API. If you expose the engine publicly, put authentication in
  front of `POST /api/runs`; the cache itself does not make that safe.

## Failure behaviour

A Supabase outage never fails or blocks a run (best-effort, like `verdicts.db`). The run state
carries `cache.failed`, and the UI says "computed but NOT cached" rather than implying success.
