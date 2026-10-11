-- REFERENCE ONLY — retired alongside the rest of the Garmin integration
-- (removed in personalprojects 1c58f3c, Oct 2026). These columns still exist on
-- public.garmin_activities but nothing writes them.
--
-- Per-session respiration and stress (Garmin populates these for meditation activities).
alter table public.garmin_activities
  add column if not exists min_hr integer,
  add column if not exists avg_respiration_rate numeric(5,2),
  add column if not exists min_respiration_rate numeric(5,2),
  add column if not exists max_respiration_rate numeric(5,2),
  add column if not exists start_stress integer,
  add column if not exists end_stress integer,
  add column if not exists avg_stress integer,
  add column if not exists max_stress integer,
  add column if not exists stress_change integer;  -- end_stress - start_stress (negative = calmer)
