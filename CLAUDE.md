# Kanban

Personal kanban for Rocky. Supabase-backed, static frontend on GitHub Pages, local MCP server for Claude to read/write tasks.

## Architecture

```
index.html  ──HTTP──>  Supabase REST  ──>  Postgres (tasks + journal_entries, RLS)
                            ^
                            │
mcp_server.py (stdio)  ─────┘  ← Claude Code / Claude.ai (local)

Notion Logging Journal ──> sync_journal_to_supabase.py ──> journal_entries
```

Auth: Supabase email+password (single user). Anon publishable key is committed to `index.html`; RLS enforces per-user access.

## Files

| File | Role |
|---|---|
| `index.html` | Static SPA. Supabase JS client, 5-column board, detail drawer, filter toolbar. Open directly or serve with `python3 -m http.server`. |
| `mcp_server.py` | stdio MCP server. Tools: `create_task`, `list_tasks`, `update_task`, `move_task`, `delete_task`, `weekly_stats`. Calls Supabase REST with the secret key from `.env`. |
| `.mcp.json` | Wires `mcp_server.py` as the `kanban` MCP server for Claude Code in this directory. |
| `run_sql.py` | Runs arbitrary SQL via Supabase Management API. Uses `SUPABASE_ACCESS_TOKEN` from `.env`. |
| `set_password.py` | One-shot admin-API call to set the kanban user's password. |
| `import_to_supabase.py` | One-time Notion → Supabase importer. Already run; preserved for reference. |
| `sync_journal_to_supabase.py` | Idempotent Notion Logging Journal → Supabase sync. Also syncs individual meals and derives weekly meal timing. Used locally and by GitHub Actions. |
| `scriptable/agentos-food.js` | iOS Scriptable home-screen widget. Reads the current week's meals straight from Supabase (password grant, credentials in the iOS keychain) and frames them as budget remaining. Read-only. |
| `supabase/migrations/` | Checked-in database migrations, including the authenticated `journal_entries` table and RLS policy. |
| `server.py` | **Legacy.** Notion-backed REST server on port 5173. Personal Intelligence no longer depends on it after the journal migration. |
| `.env` | Secrets (gitignored): `SUPABASE_URL`, `SUPABASE_SECRET_KEY`, `KANBAN_USER_ID`, `SUPABASE_ACCESS_TOKEN`, plus legacy Notion vars. |

## Schema (`tasks`)

```
id           uuid          PK
user_id      uuid          FK auth.users, RLS gate
title        text          NOT NULL
status       text          NOT NULL  -- inbox | not-started | in-progress | focus | done
context      text                    -- Personal | Work
source       text                    -- e.g. "Manual", "Notion"
source_link  text
notion_id    text                    -- import dedup key (legacy)
description  text                    -- markdown
timing       text                    -- this-week | next-week | next-30-days ("Later") | ongoing (no fixed week); planning horizon only
effort       text                    -- S | M | L | XL
parent_id    uuid          FK tasks(id) ON DELETE CASCADE  -- 2-level hierarchy: a child task's project
subtasks     jsonb         NOT NULL  -- LEGACY. Migrated to child tasks; kept at '[]'. Do not write new data here.
created_at   timestamptz   NOT NULL
updated_at   timestamptz   NOT NULL
completed_at timestamptz             -- auto-set by `set_completed_at` trigger on status → done; cleared on status → not-done
```

## Projects (2-level hierarchy)

`parent_id` makes any task nestable. A **project** is a top-level task (`parent_id IS NULL`) that has ≥1 child; a **child task** has `parent_id` set. Only 2 levels are allowed — a child can't have its own children, and a project can't be nested (enforced in `mcp_server.py:validate_parent` and the drawer UI). "Subtasks" in the drawer are now real child tasks (the old jsonb `subtasks` checklist was migrated into child rows and cleared).

- **Board scope** (`state.project` in `index.html`): `null` = "All Tasks" shows top-level items only (children hidden, projects show a 📁 done/total chip). Selecting a project in the sidebar scopes the board to that project's children. New tasks added inside a project view inherit `parent_id` + context.
- **MCP**: `create_task`/`update_task` take `parent_id` (update with empty string detaches to no project). `list_tasks` takes `parent` (a project id, or `'none'` for top-level only) and annotates children with `↳ in: <project>` and projects with `[project done/total]`.

`timing` and `effort` are CHECK-constrained enums. `status` is too (enforced in app code; constraint may or may not exist server-side). `description` is rendered as markdown in the drawer via `marked` + `DOMPurify` (CDN-loaded).

## Food log & nutrition

Two tables, two grains. `journal_entries` holds the **weekly** rollup; `food_log_entries` holds the **individual meals** behind it so the dashboard can drill from "3 non-profile meals" into which three.

Some weekly food columns are Notion rollups copied verbatim (`FL Non Profile` → `non_profile_meals`, `FL DCP` → `dcp_meals`). `FL Cooked` is deliberately **not** — see below. Notion still has `FL Drinks`, `FL Milk Drinks`, `FL Total Cals` and `FL Total Protein` that nothing reads yet.

**Meal timing and the cooked count are derived, not synced.** `weekly_meal_stats()` computes `first_meal_mins`, `last_meal_mins`, `eating_window_hrs`, `meal_days`, `total_meals` and `cooked_meals` from the meal rows. Stored as minutes after local midnight so the numeric chart machinery can draw them; the UI formats back to a clock.

- **An eating day runs 04:00 → 03:59.** A 00:30 snack closes the night before rather than opening the next day. `EATING_DAY_START_MIN` in `sync_journal_to_supabase.py` and the same constant in `index.html` and `scriptable/agentos-food.js` **must agree** — if they drift, the charts and the drill-down will disagree about which day a late meal belongs to.
- Times are resolved to `FOOD_LOG_TZ` (default `America/Los_Angeles`). Notion returns a real UTC offset on every timed entry; date-only entries are skipped rather than guessed at as midnight.
- Weeks with fewer than `MIN_TIMING_DAYS` (3) logged days report coverage but no averages — one 20:00 meal would otherwise read as a 20:00 first meal and a 0-hour window. Same spirit as the `MIN_PLAUSIBLE_DAILY_CALS` floor on calories.

**`is_cooked` is just `Source == "Home"`** in Notion — nothing about cooking. Leftovers, trail mix and a hot chocolate all count. Nothing derivable from the row separates those from a real cooked meal, so it is treated as a *default* rather than an answer: `cooked_meals` is derived in the sync as `coalesce(is_cooked_override, is_cooked)`, **not** copied from the `FL Cooked` rollup. Mark a meal "not cooked" in the drill-down and the weekly count follows on the next sync.

### Corrected meals

The frontend is static on GitHub Pages, so it cannot hold the Notion token and write a fix upstream. Instead:

- Two override columns, both owned by the browser and **never** written by the sync: `eaten_at_override` (timestamptz) and `is_cooked_override` (boolean, tri-state — `null` defers to Notion, `false` demotes, `true` promotes).
- Everything that reads either value uses `coalesce(override, notion_value)` — in the drill-downs, in the widget, and in the sync's own derivation (`fetch_meal_overrides` → `weekly_meal_stats`), so a correction is not undone by the next run.
- `food_log_entries` has an update policy for `authenticated` plus a `before update` trigger (`food_log_entries_guard_client_update`) that pins every other column to its old value for browser sessions, and a column-level `grant update (eaten_at_override, is_cooked_override)`. `service_role` (the sync) keeps full write access. The policy alone cannot scope an update to one column — **a new override column needs adding to that grant**, or the browser write fails silently.

An edit shows immediately in the drill-down (computed client-side) and reaches the weekly charts on the next sync.

## Views & filters

The toolbar exposes both **filters** (Context / Due / Age — multi-pill filter pattern) and a **group-by toggle** (Status / Due / Age / Context). They're independent: filters always AND-narrow the visible set, group-by chooses which dimension drives the columns.

Drag-drop reassigns the grouping field where it makes sense (status / timing / context); in Age grouping it's a no-op since staleness is derived from `created_at`.

## Automations (on-open rollovers)

Two weekly rollovers run client-side in `loadTasks()` — they fire once per week on the first app open after the boundary (tracked in `localStorage`, skip their first-ever run so nothing is swept retroactively; optimistic local update + background persist):

- **Focus → In Progress** after Sunday 6pm (`maybeWeeklyFocusReset`, key `kanban.lastFocusReset`).
- **Next Week → This Week** on/after Monday 00:00 (`maybeNextWeekRollover`, key `kanban.lastNextWeekRoll`).

There is no server-side scheduler; whichever machine opens the app first that week runs the rollover, and it's idempotent across devices.

## Scheduled sync (GitHub Actions)

`.github/workflows/sync-journal.yml` runs `sync_journal_to_supabase.py` daily at 13:17 UTC. It needs five repo secrets — `NOTION_TOKEN`, `NOTION_JOURNAL_DB_ID`, `SUPABASE_URL`, `SUPABASE_SECRET_KEY`, `KANBAN_USER_ID` — mirroring `.env`. Missing any of them makes the script exit on its own `Missing environment variables:` guard, which reads as a generic step failure in the Actions UI.

Check it is actually working before trusting the dashboard's freshness:

```bash
gh run list --workflow sync-journal.yml --limit 5
```

## Related repos

- **This repo on GitHub:** `git@github.com:rgowni1/kanban.git` (`origin`). Production is GitHub Pages serving `index.html`.
- **Sonar MCP** (for the planned `sync_inbox` feature): `~/tm_workspace/tidemark-backend/tidemark_backend/sonar_mcp/`
  - Email tool definition: `tools/mail.py` (`get_emails`). To expose Outlook flag status, add `flag` to the Graph `$select` and add a `flagged_only` arg.
- **Tidemark backend (broader)**: `~/tm_workspace/tidemark-backend/`

## Not yet implemented

- **File attachments on tasks.** No Supabase Storage bucket, no `attachments` table/column, no upload UI. Only `source_link` (URL string) exists.
- **`sync_inbox` MCP tool** to pull Outlook-flagged emails. Blocked on Sonar exposing flag status (see `tools/mail.py` above).
- **Remote MCP** for browser Claude / Cowork / phone. Current `mcp_server.py` is stdio-only; would need an HTTP/SSE transport and somewhere to host it for non-local clients to use it.

## Common commands

```bash
# Serve the frontend locally
python3 -m http.server 8000              # then open http://localhost:8000

# Refresh Personal Intelligence data from Notion
python3 sync_journal_to_supabase.py
python3 sync_journal_to_supabase.py --dry-run   # fetch and derive, write nothing

# Run a SQL migration / one-off query
python3 run_sql.py "SELECT count(*) FROM tasks;"

# Set the kanban user's password (one-time)
python3 set_password.py

# MCP server runs as a subprocess of Claude Code; no command needed.
# Restart it by reloading Claude Code in this directory.
```

## Conventions

- **Never extend `server.py`.** New backend logic goes into `mcp_server.py` (for Claude write-paths) or `index.html` (for UI).
- **MCP tool changes need a Claude Code reload** to take effect — the server is spawned at session start.
- **Don't echo `SUPABASE_SECRET_KEY` or `SUPABASE_ACCESS_TOKEN`** in chat or commits. They live only in `.env`.
- **Frontend is fully static** — no build step, no bundler. Edit `index.html` and refresh the browser.
- **Schema changes**: write the SQL **into `supabase/migrations/`**, apply it with `python3 run_sql.py < supabase/migrations/<file>.sql`, update `mcp_server.py` field lists, update `index.html` (rowToTask / patchToRow / drawer / badges / filters) in the same commit. `non_profile_meals` and `dcp_meals` were added with an ad-hoc `run_sql.py` call and exist in the database but in no migration file — don't add to that drift.
