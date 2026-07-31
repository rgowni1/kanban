create table if not exists public.journal_entries (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  week date not null,
  notion_page_id text,
  source_updated_at timestamptz,
  synced_at timestamptz not null default now(),
  slp_hrs numeric,
  slp_score numeric,
  slp_hrv numeric,
  rhr numeric,
  avg_sleep_time text,
  avg_wake_time text,
  run_miles numeric,
  bike_miles numeric,
  swim_yards numeric,
  strength_mins numeric,
  intensity_mins numeric,
  step_count numeric,
  body_fat numeric,
  weekly_weight numeric,
  stress numeric,
  meditation numeric,
  articles numeric,
  podcasts numeric,
  phone_pickups numeric,
  avg_daily_pickups numeric,
  training_hrs numeric,
  consumed_cals numeric,
  avg_protein numeric,
  calorie_deficit numeric,
  garmin_tdee numeric,
  constraint journal_entries_user_week_key unique (user_id, week)
);

create index if not exists journal_entries_user_week_idx
  on public.journal_entries (user_id, week);

alter table public.journal_entries enable row level security;

drop policy if exists "Users can read their own journal entries"
  on public.journal_entries;

create policy "Users can read their own journal entries"
  on public.journal_entries
  for select
  to authenticated
  using ((select auth.uid()) = user_id);

grant select on table public.journal_entries to authenticated;
grant all on table public.journal_entries to service_role;
