# Kanban

Personal kanban for Rocky. Supabase-backed, static frontend on GitHub Pages, local MCP server for Claude to read/write tasks.

## Architecture

```
index.html  ──HTTP──>  Supabase REST  ──>  Postgres (tasks table, RLS)
                            ^
                            │
mcp_server.py (stdio)  ─────┘  ← Claude Code / Claude.ai (local)
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
| `server.py` | **Legacy.** Notion-backed REST server on port 5173. Do not extend. Kept until everything is verified post-migration. |
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
timing       text                    -- this-week | next-week | next-30-days (UI label "Later"); planning horizon only
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

## Views & filters

The toolbar exposes both **filters** (Context / Due / Age — multi-pill filter pattern) and a **group-by toggle** (Status / Due / Age / Context). They're independent: filters always AND-narrow the visible set, group-by chooses which dimension drives the columns.

Drag-drop reassigns the grouping field where it makes sense (status / timing / context); in Age grouping it's a no-op since staleness is derived from `created_at`.

## Automations (on-open rollovers)

Two weekly rollovers run client-side in `loadTasks()` — they fire once per week on the first app open after the boundary (tracked in `localStorage`, skip their first-ever run so nothing is swept retroactively; optimistic local update + background persist):

- **Focus → In Progress** after Sunday 6pm (`maybeWeeklyFocusReset`, key `kanban.lastFocusReset`).
- **Next Week → This Week** on/after Monday 00:00 (`maybeNextWeekRollover`, key `kanban.lastNextWeekRoll`).

There is no server-side scheduler; whichever machine opens the app first that week runs the rollover, and it's idempotent across devices.

## Related repos

- **This repo on GitHub:** `git@github.com:rgowni1/kanban.git` (`origin`). Target deploy: GitHub Pages serving `index.html`.
- **Sonar MCP** (for the planned `sync_inbox` feature): `~/tm_workspace/tidemark-backend/tidemark_backend/sonar_mcp/`
  - Email tool definition: `tools/mail.py` (`get_emails`). To expose Outlook flag status, add `flag` to the Graph `$select` and add a `flagged_only` arg.
- **Tidemark backend (broader)**: `~/tm_workspace/tidemark-backend/`

## Not yet implemented

- **File attachments on tasks.** No Supabase Storage bucket, no `attachments` table/column, no upload UI. Only `source_link` (URL string) exists.
- **`sync_inbox` MCP tool** to pull Outlook-flagged emails. Blocked on Sonar exposing flag status (see `tools/mail.py` above).
- **GitHub Pages deploy.** Repo exists (`git@github.com:rgowni1/kanban.git`) but Pages isn't configured yet.
- **Remote MCP** for browser Claude / Cowork / phone. Current `mcp_server.py` is stdio-only; would need an HTTP/SSE transport and somewhere to host it for non-local clients to use it.

## Common commands

```bash
# Serve the frontend locally
python3 -m http.server 8000              # then open http://localhost:8000

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
- **Schema changes**: write the SQL, run via `run_sql.py`, update `mcp_server.py` field lists, update `index.html` (rowToTask / patchToRow / drawer / badges / filters) in the same commit.
