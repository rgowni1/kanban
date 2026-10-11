-- REFERENCE ONLY — retired. The Garmin integration was removed from
-- rgowni1/personalprojects in 1c58f3c (Oct 2026); nothing populates these
-- objects now. They still exist in the Supabase "kanban" project, so this file
-- is kept as the record of their schema. Idempotent if ever replayed.
--
-- Garmin daily wellness + activities, synced by the garmin-sync scheduled task.
-- Rows are upserted every run over a trailing window, so late watch syncs backfill.
-- Lives in the "kanban" project alongside tasks / journal_entries / food_log_entries,
-- following the same user_id + auth.uid() RLS pattern.

create table if not exists public.garmin_daily (
  user_id              uuid not null default '5be4d262-e35c-436b-86f5-0a6f24956182'
                         references auth.users (id) on delete cascade,
  calendar_date        date not null,
  total_kilocalories   integer,
  total_steps          integer,
  moderate_intensity_minutes integer,
  vigorous_intensity_minutes integer,
  intensity_minutes    integer generated always as
                         (coalesce(moderate_intensity_minutes, 0) + 2 * coalesce(vigorous_intensity_minutes, 0)) stored,
  avg_stress_level     integer,          -- overallStressLevel from stats/stress/daily
  high_stress_seconds  integer,
  medium_stress_seconds integer,
  low_stress_seconds   integer,
  weight_lbs           numeric(6,2),
  body_fat_pct         numeric(5,2),
  includes_wellness_data boolean,
  wellness_end_time_local timestamp,     -- partial-day detection
  is_partial           boolean not null default false,
  raw                  jsonb,            -- full usersummary payload for later re-derivation
  synced_at            timestamptz not null default now(),
  primary key (user_id, calendar_date)
);

create table if not exists public.garmin_activities (
  activity_id          bigint primary key,
  user_id              uuid not null default '5be4d262-e35c-436b-86f5-0a6f24956182'
                         references auth.users (id) on delete cascade,
  activity_name        text,
  activity_type        text not null,    -- activityType.typeKey
  start_time_local     timestamp not null,
  calendar_date        date generated always as (start_time_local::date) stored,
  duration_seconds     numeric(10,2),
  distance_meters      numeric(12,2),
  calories             numeric(8,2),
  avg_hr               integer,
  max_hr               integer,
  raw                  jsonb,
  synced_at            timestamptz not null default now()
);

create index if not exists garmin_activities_user_date_idx
  on public.garmin_activities (user_id, calendar_date);

alter table public.garmin_daily enable row level security;
alter table public.garmin_activities enable row level security;

create policy "Users can read their own garmin daily rows"
  on public.garmin_daily for select to authenticated
  using ((select auth.uid()) = user_id);

create policy "Users can read their own garmin activities"
  on public.garmin_activities for select to authenticated
  using ((select auth.uid()) = user_id);

-- Weekly rollup that mirrors the Notion Log fields (Mon-Sun weeks).
-- Partial days and <1500 cal outage days are excluded from TDEE/steps.
create or replace view public.garmin_weekly
with (security_invoker = true) as
with days as (
  select user_id, date_trunc('week', calendar_date)::date as week_start,
    total_kilocalories, total_steps, intensity_minutes, avg_stress_level,
    weight_lbs, body_fat_pct, is_partial, includes_wellness_data
  from public.garmin_daily
),
day_agg as (
  select user_id, week_start,
    round(avg(total_kilocalories) filter (where not is_partial and total_kilocalories >= 1500)) as garmin_tdee,
    round((percentile_cont(0.5) within group (order by total_steps)
      filter (where not is_partial and total_kilocalories >= 1500))::numeric) as step_count,
    sum(intensity_minutes) filter (where includes_wellness_data) as intensity_minutes,
    round(avg(avg_stress_level)) as stress_level,
    round(avg(weight_lbs), 1) as weekly_weight,
    round(avg(body_fat_pct), 1) as body_fat_pct,
    count(*) filter (where includes_wellness_data and not is_partial) as full_days,
    count(*) filter (where is_partial) as partial_days
  from days
  group by 1, 2
),
act_agg as (
  select user_id, date_trunc('week', calendar_date)::date as week_start,
    sum(duration_seconds) filter (where activity_type not in ('meditation','mindfulness')) / 3600.0 as training_hours,
    sum(duration_seconds) filter (where activity_type like '%strength%') / 60.0 as strength_minutes,
    sum(distance_meters) filter (where activity_type like '%swim%') * 1.09361 as swim_yards,
    sum(distance_meters) filter (where activity_type like '%run%') / 1609.34 as run_miles,
    sum(distance_meters) filter (where activity_type ~ '(cycling|biking|ride)') / 1609.34 as bike_miles,
    count(*) filter (where activity_type in ('meditation','mindfulness')) as meditation_sessions
  from public.garmin_activities
  group by 1, 2
)
select coalesce(d.user_id, a.user_id) as user_id,
  coalesce(d.week_start, a.week_start) as week_start,
  d.garmin_tdee, d.step_count, d.intensity_minutes, d.stress_level,
  d.weekly_weight, d.body_fat_pct,
  round(coalesce(a.training_hours, 0), 2) as training_hours,
  round(coalesce(a.strength_minutes, 0)) as strength_minutes,
  round(coalesce(a.swim_yards, 0)) as swim_yards,
  round(coalesce(a.run_miles, 0), 2) as run_miles,
  round(coalesce(a.bike_miles, 0), 2) as bike_miles,
  coalesce(a.meditation_sessions, 0) as meditation_sessions,
  coalesce(d.full_days, 0) as full_days,
  coalesce(d.partial_days, 0) as partial_days
from day_agg d
full join act_agg a on a.user_id = d.user_id and a.week_start = d.week_start;
