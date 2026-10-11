-- REFERENCE ONLY — retired alongside the rest of the Garmin integration
-- (removed in personalprojects 1c58f3c, Oct 2026). This is the current
-- definition of public.garmin_weekly; it supersedes the view created in
-- 20261004000000_garmin_tables.sql, so apply that file first if ever replaying.
--
-- Count breathwork sessions (e.g. "Relax and Focus") as meditation, and keep them out of training hours.
create or replace view public.garmin_weekly
with (security_invoker = true) as
with days as (
  select user_id, date_trunc('week', calendar_date)::date as week_start,
    total_kilocalories, total_steps, intensity_minutes, avg_stress_level,
    weight_lbs, body_fat_pct, is_partial, includes_wellness_data
  from public.garmin_daily
), day_agg as (
  select user_id, week_start,
    round(avg(total_kilocalories) filter (where not is_partial and total_kilocalories >= 1500)) as garmin_tdee,
    round((percentile_cont(0.5) within group (order by total_steps::double precision)
      filter (where not is_partial and total_kilocalories >= 1500))::numeric) as step_count,
    sum(intensity_minutes) filter (where includes_wellness_data) as intensity_minutes,
    round(avg(avg_stress_level)) as stress_level,
    round(avg(weight_lbs), 1) as weekly_weight,
    round(avg(body_fat_pct), 1) as body_fat_pct,
    count(*) filter (where includes_wellness_data and not is_partial) as full_days,
    count(*) filter (where is_partial) as partial_days,
    round(avg(total_kilocalories) filter (where not is_partial and total_kilocalories >= 1500) * 0.90) as adjusted_tdee
  from days
  group by user_id, week_start
), act_agg as (
  select user_id, date_trunc('week', calendar_date)::date as week_start,
    sum(duration_seconds) filter (where activity_type <> all (array['meditation', 'mindfulness', 'breathwork'])) / 3600.0 as training_hours,
    sum(duration_seconds) filter (where activity_type like '%strength%') / 60.0 as strength_minutes,
    sum(distance_meters) filter (where activity_type like '%swim%') * 1.09361 as swim_yards,
    sum(distance_meters) filter (where activity_type like '%run%') / 1609.34 as run_miles,
    sum(distance_meters) filter (where activity_type ~ '(cycling|biking|ride)') / 1609.34 as bike_miles,
    count(*) filter (where activity_type = any (array['meditation', 'mindfulness', 'breathwork'])) as meditation_sessions
  from public.garmin_activities
  group by user_id, date_trunc('week', calendar_date)::date
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
  coalesce(d.partial_days, 0) as partial_days,
  d.adjusted_tdee
from day_agg d
full join act_agg a on a.user_id = d.user_id and a.week_start = d.week_start;
