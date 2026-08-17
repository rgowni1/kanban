-- Cooked meals + meal timing.
--
-- Two additions to the weekly food-log picture:
--   1. `cooked_meals` mirrors the existing `FL Cooked` rollup in Notion, which
--      nothing read until now. Same shape as non_profile_meals / dcp_meals.
--   2. First/last meal timing, derived in the sync from food_log_entries rather
--      than from Notion, because Notion has no rollup for it.
--
-- Timing is stored as minutes after local midnight so the existing numeric
-- chart machinery can draw it; the UI formats back to a clock time. An eating
-- day runs 04:00 -> 03:59, so a 00:30 snack lands on the prior day at 1470
-- minutes rather than opening the next day at 30.

alter table public.journal_entries
  add column if not exists cooked_meals      numeric,  -- Notion "FL Cooked" rollup (Source = Home)
  add column if not exists total_meals       numeric,  -- food-log rows in the week; denominator for cooked share
  add column if not exists first_meal_mins   numeric,  -- avg minutes after local midnight of the day's first meal
  add column if not exists last_meal_mins    numeric,  -- avg for the day's last meal; may exceed 1440 (past midnight)
  add column if not exists eating_window_hrs numeric,  -- avg hours between first and last meal
  add column if not exists meal_days         numeric;  -- days in the week with >=1 logged meal (coverage)

-- Corrected meal times, entered in the dashboard.
--
-- The frontend is static and served from GitHub Pages, so it has no way to hold
-- the Notion token and write a fix back upstream. Instead the sync owns
-- `eaten_at` and never touches `eaten_at_override`; everything that reads a
-- meal time uses coalesce(override, eaten_at). An edit therefore survives the
-- next sync instead of being silently reverted by it.
alter table public.food_log_entries
  add column if not exists eaten_at_override timestamptz;

-- Editing a time is the first write the browser makes to this table, so it
-- needs an update policy to go with the existing select policy. Scoped to the
-- override column by a trigger below -- the policy alone cannot restrict which
-- columns an update touches, and a client that could rewrite `eaten_at` would
-- have its edits erased by the next sync anyway.
drop policy if exists "Users can correct their own meal times" on public.food_log_entries;
create policy "Users can correct their own meal times"
  on public.food_log_entries for update
  to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);

create or replace function public.food_log_entries_guard_client_update()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  -- service_role runs the sync and may write anything. An authenticated browser
  -- session may only move eaten_at_override; every other column is pinned to
  -- its old value so a stray client patch cannot corrupt synced data.
  if current_setting('request.jwt.claims', true) is null
     or coalesce(current_setting('request.jwt.claims', true)::jsonb ->> 'role', '') <> 'authenticated' then
    return new;
  end if;
  new.id                := old.id;
  new.user_id           := old.user_id;
  new.notion_page_id    := old.notion_page_id;
  new.name              := old.name;
  new.eaten_at          := old.eaten_at;
  new.meal_type         := old.meal_type;
  new.source            := old.source;
  new.calories          := old.calories;
  new.protein_g         := old.protein_g;
  new.carbs_g           := old.carbs_g;
  new.fat_g             := old.fat_g;
  new.is_non_profile    := old.is_non_profile;
  new.is_dcp            := old.is_dcp;
  new.is_cooked         := old.is_cooked;
  new.source_updated_at := old.source_updated_at;
  new.synced_at         := old.synced_at;
  return new;
end;
$$;

drop trigger if exists food_log_entries_guard_client_update on public.food_log_entries;
create trigger food_log_entries_guard_client_update
  before update on public.food_log_entries
  for each row execute function public.food_log_entries_guard_client_update();

grant update (eaten_at_override) on table public.food_log_entries to authenticated;
