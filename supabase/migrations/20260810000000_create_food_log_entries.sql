-- Individual Food Log rows from Notion, so the dashboard can drill from a
-- weekly rollup (e.g. "9 non-profile meals") into the meals behind it.
-- The journal_entries table stays the weekly aggregate; this is the detail.
create table if not exists public.food_log_entries (
  id               uuid primary key default gen_random_uuid(),
  user_id          uuid not null references auth.users (id) on delete cascade,
  notion_page_id   text not null,
  name             text not null,
  eaten_at         timestamptz,
  meal_type        text,           -- Breakfast | Lunch | Dinner | Snack
  source           text,           -- Home | Restaurant | Other
  calories         numeric,
  protein_g        numeric,
  carbs_g          numeric,
  fat_g            numeric,
  is_non_profile   boolean not null default false,  -- carbs >= 60g AND fat >= 25g
  is_dcp           boolean not null default false,  -- dessert / cake / pastry by name
  is_cooked        boolean not null default false,  -- Source = Home
  source_updated_at timestamptz,
  synced_at        timestamptz not null default now(),
  unique (user_id, notion_page_id)
);

create index if not exists food_log_entries_user_eaten_idx
  on public.food_log_entries (user_id, eaten_at desc);

alter table public.food_log_entries enable row level security;

drop policy if exists "Users can read their own food log entries" on public.food_log_entries;
create policy "Users can read their own food log entries"
  on public.food_log_entries for select
  using ((select auth.uid()) = user_id);
