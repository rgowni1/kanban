-- Oura daily sleep, synced by oura.py. Granular source of truth for sleep /
-- recovery metrics. Notion Logging Journal and personal-dashboard read from here
-- (weekly rollup), not from the Oura API directly.
-- Lives in the "kanban" project alongside garmin_* / journal_entries.

create table if not exists public.oura_daily (
  user_id              uuid not null default '5be4d262-e35c-436b-86f5-0a6f24956182'
                         references auth.users (id) on delete cascade,
  calendar_date        date not null,
  sleep_score          numeric(5,2),
  total_sleep_hours    numeric(5,2),
  average_hrv          numeric(6,2),
  lowest_heart_rate    numeric(5,2),
  average_breath       numeric(5,2),
  latency_minutes      numeric(6,2),
  -- Local clock times (America/Los_Angeles). sleep_time = bedtime_start + latency.
  sleep_time           text,             -- HH:MM
  wake_time            text,             -- HH:MM
  -- Minutes after local midnight for averaging across midnight-crossing bedtimes.
  -- Sleep times before 06:00 are stored as +1440 so weekly means stay sane.
  sleep_time_mins      integer,
  wake_time_mins       integer,
  bedtime_start        timestamptz,
  bedtime_end          timestamptz,
  oura_sleep_id        text,             -- primary sleep session id when available
  raw                  jsonb,
  synced_at            timestamptz not null default now(),
  primary key (user_id, calendar_date)
);

create index if not exists oura_daily_user_date_idx
  on public.oura_daily (user_id, calendar_date desc);

alter table public.oura_daily enable row level security;

drop policy if exists "Users can read their own oura daily rows" on public.oura_daily;
create policy "Users can read their own oura daily rows"
  on public.oura_daily for select to authenticated
  using ((select auth.uid()) = user_id);

grant select on table public.oura_daily to authenticated;
grant all on table public.oura_daily to service_role;

-- Weekly rollup mirroring Notion Logging Journal sleep fields (Mon–Sun ISO weeks).
create or replace view public.oura_weekly
with (security_invoker = true) as
with days as (
  select *
  from public.oura_daily
  where total_sleep_hours is null or total_sleep_hours >= 3
),
agg as (
  select
    user_id,
    date_trunc('week', calendar_date)::date as week_start,
    round(avg(total_sleep_hours)::numeric, 2) as slp_hrs,
    round(avg(sleep_score)::numeric, 2) as slp_score,
    round(avg(average_hrv)::numeric, 2) as slp_hrv,
    round(avg(lowest_heart_rate)::numeric, 2) as rhr,
    round(avg(average_breath)::numeric, 2) as breath_rate,
    round(avg(latency_minutes)::numeric, 2) as slp_latency,
    round(avg(sleep_time_mins) filter (where sleep_time_mins is not null))::int as avg_sleep_time_mins,
    round(avg(wake_time_mins) filter (where wake_time_mins is not null))::int as avg_wake_time_mins,
    count(*)::int as nights
  from days
  group by 1, 2
)
select
  user_id,
  week_start,
  slp_hrs,
  slp_score,
  slp_hrv,
  rhr,
  breath_rate,
  slp_latency,
  avg_sleep_time_mins,
  avg_wake_time_mins,
  case
    when avg_sleep_time_mins is null then null
    else lpad(((avg_sleep_time_mins % 1440) / 60)::text, 2, '0')
      || ':' || lpad((avg_sleep_time_mins % 60)::text, 2, '0')
  end as avg_sleep_time,
  case
    when avg_wake_time_mins is null then null
    else lpad((avg_wake_time_mins / 60)::text, 2, '0')
      || ':' || lpad((avg_wake_time_mins % 60)::text, 2, '0')
  end as avg_wake_time,
  nights
from agg;
