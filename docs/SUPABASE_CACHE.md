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

1. In the Supabase SQL editor run `supabase/migrations/0001_verdict_cache.sql` (idempotent).
2. Engine `.env`: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` (service-role key; server-side only).
3. `app-ui/.env.local`: `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY` (see `app-ui/.env.example`).
4. Push verdicts you already computed: `python tools/backfill_supabase.py --dry-run`, then without `--dry-run`.

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
