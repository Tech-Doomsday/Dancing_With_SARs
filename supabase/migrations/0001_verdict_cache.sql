-- Verdict cache: every finished verdict the engine produces, readable by anyone, writable only
-- by the engine (service-role key). Idempotent: safe to re-run.
--
-- Key = (tile, pipeline, bundle_id), the same key the engine's own verdicts.db uses. bundle_id is
-- content-addressed, so "same key" means "same inputs". A redo overwrites the row in place.
--
-- NOT cached, by design (see engine/verdict/cloud_cache.py): ERROR / PENDING results (not
-- verdicts) and runs with custom parameter overrides (the cache holds engine-default verdicts).

create table if not exists public.verdicts (
  tile               text        not null,
  pipeline           text        not null,
  bundle_id          text        not null,
  epoch              text,
  status             text        not null,   -- the engine's own status string, stored verbatim
  tier               text,                   -- 4.2 calibrate tier
  raw_p              double precision
                     check (raw_p is null or (raw_p >= 0 and raw_p <= 1)),   -- rank score, NOT a probability
  summary            text,
  result             jsonb       not null,   -- the full verdict row (evidence, warnings, data_used, ...) minus layer grids
  layers             jsonb       not null default '{}'::jsonb,   -- detail.layers payloads (b64 grids); fetched on demand
  run_id             text,
  first_computed_at  timestamptz not null default now(),   -- never overwritten by a redo
  computed_at        timestamptz not null default now(),   -- set by the engine on every (re)compute
  primary key (tile, pipeline, bundle_id)
);

create index if not exists verdicts_computed_at_idx on public.verdicts (computed_at desc);
create index if not exists verdicts_bundle_idx      on public.verdicts (bundle_id);

alter table public.verdicts enable row level security;

-- Anyone may read the cache (that is the point). There is deliberately NO insert/update/delete
-- policy: the service-role key bypasses RLS, nothing else can write.
drop policy if exists verdicts_public_read on public.verdicts;
create policy verdicts_public_read on public.verdicts
  for select to anon, authenticated using (true);

revoke insert, update, delete, truncate on public.verdicts from anon, authenticated;
grant  select on public.verdicts to anon, authenticated;
