-- "Cooked" is not "eaten at home".
--
-- Notion's `Is Cooked` is `Source == "Home"` and nothing more, so a hot
-- chocolate, a handful of trail mix and a bowl of last night's takeaway all
-- count as cooked meals. There is no rule that can separate those from a real
-- cooked meal -- the difference lives in the meal name and in what actually
-- happened -- so this is a judgement call the dashboard has to accept, not
-- derive.
--
-- Tri-state on purpose: null defers to Notion's flag (the common case), false
-- demotes a home-sourced meal that was not cooked, true promotes a meal Notion
-- did not mark. Everything that counts cooked meals reads
-- coalesce(is_cooked_override, is_cooked).
alter table public.food_log_entries
  add column if not exists is_cooked_override boolean;

-- The existing guard trigger pins every column it names to its old value for
-- browser sessions. It does not name this one, so it is already mutable there;
-- what it still needs is the column-level grant, since the earlier migration
-- granted update on `eaten_at_override` alone.
grant update (eaten_at_override, is_cooked_override) on table public.food_log_entries to authenticated;

comment on column public.food_log_entries.is_cooked_override is
  'Manual correction to is_cooked. null = use Notion''s flag. Never written by the sync.';
