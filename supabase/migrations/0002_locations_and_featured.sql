-- Locations + featured areas for the globe UI (globe task Phase 2). Idempotent: safe to re-run.
--
-- 1) The verdict cache gains the tile's geographic position so a globe can pin cached areas
--    without resolving anything client-side. The engine writes the three columns; the values
--    come from the bundle's own TileInfo (center_lonlat / bbox_lonlat), never from the client.
-- 2) featured_areas is the OWNER's curated list (DEC4): seeded EMPTY, no invented entries.
--    Add real entries with tools/feature_area.py (service key, server-side). Reads are public
--    (RLS select), writes service-role only — mirroring 0001 exactly.

alter table public.verdicts
  add column if not exists center_lon double precision,
  add column if not exists center_lat double precision,
  add column if not exists bbox_lonlat jsonb;

-- Range checks (best-effort: Postgres keeps existing rows untouched; new writes are validated)
do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'verdicts_center_lon_range'
  ) then
    alter table public.verdicts
      add constraint verdicts_center_lon_range check (center_lon is null or center_lon between -180 and 180);
  end if;
  if not exists (
    select 1 from pg_constraint where conname = 'verdicts_center_lat_range'
  ) then
    alter table public.verdicts
      add constraint verdicts_center_lat_range check (center_lat is null or center_lat between -90 and 90);
  end if;
end $$;

create index if not exists verdicts_tile_idx on public.verdicts (tile);

create table if not exists public.featured_areas (
  slug          text primary key,
  title         text not null,
  blurb         text,
  tile          text not null,              -- MGRS tile id, resolved by the engine (never typed by hand)
  center_lon    double precision not null check (center_lon between -180 and 180),
  center_lat    double precision not null check (center_lat between -90 and 90),
  zoom          real,
  sort_order    int not null default 0,
  created_at    timestamptz not null default now()
);

create index if not exists featured_areas_sort_idx on public.featured_areas (sort_order, slug);

alter table public.featured_areas enable row level security;

-- Anyone may read the featured list (that is the point). There is deliberately NO insert/update/
-- delete policy: the service-role key bypasses RLS, nothing else can write.
drop policy if exists featured_areas_public_read on public.featured_areas;
create policy featured_areas_public_read on public.featured_areas
  for select to anon, authenticated using (true);

revoke insert, update, delete, truncate on public.featured_areas from anon, authenticated;
grant  select on public.featured_areas to anon, authenticated;
