-- Drinks.
--
-- Notion has carried an `FL Drinks` rollup (a sum of the food log's `# of
-- Drinks`) since before any of this existed, and nothing read it. It is one of
-- the few food-log numbers that moves sharply week to week -- 1, 2, then 9 --
-- so it earns a chart.
--
-- Fractional on purpose: half a glass of wine is logged as 0.5, and rounding
-- that to 0 or 1 would either erase a drink or invent one.
alter table public.journal_entries
  add column if not exists drinks numeric;   -- Notion "FL Drinks" rollup, per week

-- Per-meal count, so the weekly bar can drill into which sittings the drinks
-- came from. Same relationship `non_profile_meals` has to `is_non_profile`.
alter table public.food_log_entries
  add column if not exists drinks numeric;   -- Notion "# of Drinks" formula, per meal

comment on column public.journal_entries.drinks is
  'Weekly drink count from Notion FL Drinks. Fractional: 0.5 = half a glass.';
