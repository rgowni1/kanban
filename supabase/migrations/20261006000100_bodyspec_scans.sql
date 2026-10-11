-- REFERENCE ONLY. No code currently writes public.bodyspec_scans — the table
-- exists in the Supabase "kanban" project and is populated ad hoc, so this file
-- is kept as the record of its schema. Idempotent if ever replayed.
-- Renamed from version 20261006000000 to avoid colliding with
-- 20261006000000_journal_high_low.sql.
--
-- BodySpec DEXA scans, pulled from the BodySpec MCP (list_scan_results).
-- One row per scan. `raw` holds the full scan payload; the typed columns are
-- generated from it, so a sync only needs to upsert (result_id, scan_date, location, raw).
-- Masses are in lb to match garmin_daily.weight_lbs.
-- Lives in the "kanban" project alongside garmin_* / oura_daily.

create table if not exists public.bodyspec_scans (
  result_id            text primary key,
  user_id              uuid not null default '5be4d262-e35c-436b-86f5-0a6f24956182'
                         references auth.users (id) on delete cascade,
  scan_date            date not null,
  location             text,
  raw                  jsonb not null,

  -- Whole body
  total_mass_lb        numeric(6,1) generated always as ((raw #>> '{composition,total,total_mass,lb}')::numeric) stored,
  fat_mass_lb          numeric(6,1) generated always as ((raw #>> '{composition,total,fat_mass,lb}')::numeric) stored,
  lean_mass_lb         numeric(6,1) generated always as ((raw #>> '{composition,total,lean_mass,lb}')::numeric) stored,
  bone_mass_lb         numeric(5,1) generated always as ((raw #>> '{composition,total,bone_mass,lb}')::numeric) stored,
  body_fat_pct         numeric(4,1) generated always as ((raw #>> '{composition,total,region_fat_pct}')::numeric) stored,

  -- Regional lean / fat
  trunk_lean_lb        numeric(5,1) generated always as ((raw #>> '{composition,trunk,lean_mass,lb}')::numeric) stored,
  trunk_fat_lb         numeric(5,1) generated always as ((raw #>> '{composition,trunk,fat_mass,lb}')::numeric) stored,
  arms_lean_lb         numeric(5,1) generated always as ((raw #>> '{composition,arms,lean_mass,lb}')::numeric) stored,
  legs_lean_lb         numeric(5,1) generated always as ((raw #>> '{composition,legs,lean_mass,lb}')::numeric) stored,
  l_arm_lean_lb        numeric(5,1) generated always as ((raw #>> '{composition,l_arm,lean_mass,lb}')::numeric) stored,
  r_arm_lean_lb        numeric(5,1) generated always as ((raw #>> '{composition,r_arm,lean_mass,lb}')::numeric) stored,
  l_leg_lean_lb        numeric(5,1) generated always as ((raw #>> '{composition,l_leg,lean_mass,lb}')::numeric) stored,
  r_leg_lean_lb        numeric(5,1) generated always as ((raw #>> '{composition,r_leg,lean_mass,lb}')::numeric) stored,
  android_fat_pct      numeric(4,1) generated always as ((raw #>> '{composition,android,region_fat_pct}')::numeric) stored,
  gynoid_fat_pct       numeric(4,1) generated always as ((raw #>> '{composition,gynoid,region_fat_pct}')::numeric) stored,

  -- Visceral fat
  vat_mass_lb          numeric(5,2) generated always as ((raw #>> '{visceral_fat,vat_mass,lb}')::numeric) stored,
  vat_volume_in3       numeric(6,2) generated always as ((raw #>> '{visceral_fat,vat_volume,in3}')::numeric) stored,

  -- Bone density
  bmd_total_g_cm2      numeric(5,3) generated always as ((raw #>> '{bone_density,total,bmd_g_cm2}')::numeric) stored,

  -- Percentiles vs. BodySpec's age/sex reference group
  body_fat_percentile  integer generated always as ((raw #>> '{percentiles,metrics,total_body_fat_pct,percentile}')::integer) stored,
  vat_percentile       integer generated always as ((raw #>> '{percentiles,metrics,vat_mass_kg,percentile}')::integer) stored,
  lmi_kg_m2            numeric(5,2) generated always as ((raw #>> '{percentiles,metrics,total_lmi_kg_m2,value}')::numeric) stored,
  lmi_percentile       integer generated always as ((raw #>> '{percentiles,metrics,total_lmi_kg_m2,percentile}')::integer) stored,
  limb_lmi_percentile  integer generated always as ((raw #>> '{percentiles,metrics,limb_lmi_kg_m2,percentile}')::integer) stored,
  bmd_percentile       integer generated always as ((raw #>> '{percentiles,metrics,bone_density_g_cm2,percentile}')::integer) stored,

  synced_at            timestamptz not null default now()
);

create index if not exists bodyspec_scans_user_date_idx
  on public.bodyspec_scans (user_id, scan_date desc);

alter table public.bodyspec_scans enable row level security;

drop policy if exists "Users can read their own bodyspec scans" on public.bodyspec_scans;
create policy "Users can read their own bodyspec scans"
  on public.bodyspec_scans for select to authenticated
  using ((select auth.uid()) = user_id);

grant select on table public.bodyspec_scans to authenticated;
grant all on table public.bodyspec_scans to service_role;

-- Scan-over-scan changes (each row vs. the previous scan).
create or replace view public.bodyspec_scan_deltas
with (security_invoker = true) as
select
  user_id,
  scan_date,
  lag(scan_date) over w as prev_scan_date,
  total_mass_lb,
  fat_mass_lb,
  lean_mass_lb,
  body_fat_pct,
  vat_mass_lb,
  total_mass_lb - lag(total_mass_lb) over w as total_mass_change_lb,
  fat_mass_lb   - lag(fat_mass_lb)   over w as fat_mass_change_lb,
  lean_mass_lb  - lag(lean_mass_lb)  over w as lean_mass_change_lb,
  body_fat_pct  - lag(body_fat_pct)  over w as body_fat_pct_change,
  vat_mass_lb   - lag(vat_mass_lb)   over w as vat_mass_change_lb,
  r_leg_lean_lb - l_leg_lean_lb as leg_lean_asymmetry_lb   -- R minus L
from public.bodyspec_scans
window w as (partition by user_id order by scan_date);

grant select on public.bodyspec_scan_deltas to authenticated;
