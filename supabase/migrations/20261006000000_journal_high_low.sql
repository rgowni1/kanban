-- Weekly journal narrative fields for Personal Intelligence word clouds.
-- Sourced from Notion Logging Journal "High" / "Low" rich_text properties.

alter table public.journal_entries
  add column if not exists journal_high text,
  add column if not exists journal_low text;

comment on column public.journal_entries.journal_high is
  'Notion High — weekly energizers / positives for word clouds';
comment on column public.journal_entries.journal_low is
  'Notion Low — weekly stressors / lows for word clouds';
