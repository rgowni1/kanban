#!/usr/bin/env python3
"""MCP stdio server for the Supabase-backed kanban. Python stdlib only."""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.parse import quote

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "kanban"
SERVER_VERSION = "0.1.0"

STATUSES = ["inbox", "not-started", "in-progress", "focus", "done"]
TIMINGS = ["this-week", "next-week", "next-30-days"]
EDITABLE_FIELDS = ("title", "description", "status", "context", "source", "source_link", "timing", "effort", "subtasks", "archived", "created_at", "parent_id")
EFFORT_POINTS = {"S": 1, "M": 2, "L": 5, "XL": 10}


def load_env(path):
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_env(os.path.join(SCRIPT_DIR, ".env"))

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SECRET_KEY = os.environ.get("SUPABASE_SECRET_KEY", "")
KANBAN_USER_ID = os.environ.get("KANBAN_USER_ID", "")


def log(msg):
    sys.stderr.write(f"[kanban-mcp] {msg}\n")
    sys.stderr.flush()


# ---------- Supabase REST ----------

def sb_request(method, body=None, params=None):
    url = f"{SUPABASE_URL}/rest/v1/tasks"
    if params:
        url += "?" + "&".join(f"{quote(k)}={quote(str(v))}" for k, v in params.items())
    headers = {
        "apikey": SUPABASE_SECRET_KEY,
        "Authorization": f"Bearer {SUPABASE_SECRET_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    data = json.dumps(body).encode() if body is not None else None
    req = urlrequest.Request(url, data=data, method=method)
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urlrequest.urlopen(req) as r:
            return r.status, json.loads(r.read() or b"null")
    except urlerror.HTTPError as e:
        try:
            payload = json.loads(e.read() or b"{}")
        except Exception:
            payload = {"error": str(e)}
        return e.code, payload
    except urlerror.URLError as e:
        return 0, {"error": str(e)}


# ---------- Tool implementations ----------

def fmt_task(row):
    bits = [f"[{row.get('status')}]", row.get("title", "(untitled)")]
    extras = []
    if row.get("context"):
        extras.append(row["context"])
    if row.get("timing"):
        extras.append(f"due:{row['timing']}")
    if row.get("effort"):
        extras.append(f"effort:{row['effort']}")
    subtasks = row.get("subtasks") or []
    if subtasks:
        done = sum(1 for s in subtasks if s.get("done"))
        extras.append(f"subtasks:{done}/{len(subtasks)}")
    if row.get("archived"):
        extras.append("archived")
    if extras:
        bits.append("(" + ", ".join(extras) + ")")
    bits.append(f"id={row.get('id')}")
    return " ".join(bits)


def normalize_subtasks(value):
    if not isinstance(value, list):
        raise ValueError("subtasks must be an array")
    out = []
    for s in value:
        if isinstance(s, str):
            out.append({"text": s, "done": False})
        elif isinstance(s, dict):
            out.append({"text": str(s.get("text", "")), "done": bool(s.get("done", False))})
        else:
            raise ValueError("each subtask must be a string or {text, done}")
    return out


# ---------- Project / parent hierarchy (2-level: project -> task) ----------

def fetch_task(task_id, select="*"):
    status, data = sb_request("GET", params={
        "id": f"eq.{task_id}", "user_id": f"eq.{KANBAN_USER_ID}",
        "select": select, "limit": "1",
    })
    if status >= 400 or not data:
        return None
    return data[0]


def count_children(task_id):
    status, data = sb_request("GET", params={
        "parent_id": f"eq.{task_id}", "user_id": f"eq.{KANBAN_USER_ID}",
        "select": "id", "limit": "1000",
    })
    return len(data) if status < 400 and isinstance(data, list) else 0


def validate_parent(child_id, parent_id):
    """Enforce the 2-level (project -> task) hierarchy. Returns an error string or None."""
    if not parent_id:
        return None
    if child_id and parent_id == child_id:
        return "a task cannot be its own parent"
    parent = fetch_task(parent_id, select="id,parent_id")
    if not parent:
        return f"no task with id {parent_id} to use as parent"
    if parent.get("parent_id"):
        return "parent must be a top-level task — only 2 levels (project -> task) are allowed"
    if child_id and count_children(child_id) > 0:
        return "this task already has children, so it can't also become a child (2-level hierarchy only)"
    return None


def build_hierarchy_maps():
    """One lightweight fetch -> (title_by_id, children_by_parent) for annotating lists."""
    status, data = sb_request("GET", params={
        "user_id": f"eq.{KANBAN_USER_ID}",
        "select": "id,title,parent_id,status", "limit": "2000",
    })
    title_by_id, children_by_parent = {}, {}
    if status < 400 and isinstance(data, list):
        for r in data:
            title_by_id[r["id"]] = r.get("title")
            pid = r.get("parent_id")
            if pid:
                children_by_parent.setdefault(pid, []).append(r)
    return title_by_id, children_by_parent


def tool_create_task(args):
    if not args.get("title"):
        return error_content("title is required")
    row = {"user_id": KANBAN_USER_ID, "title": args["title"], "status": args.get("status", "inbox")}
    if row["status"] not in STATUSES:
        return error_content(f"status must be one of {STATUSES}")
    if args.get("timing") and args["timing"] not in TIMINGS:
        return error_content(f"timing must be one of {TIMINGS}")
    for k in ("description", "context", "source", "source_link", "timing", "effort"):
        if args.get(k) is not None:
            row[k] = args[k]
    if args.get("subtasks") is not None:
        try:
            row["subtasks"] = normalize_subtasks(args["subtasks"])
        except ValueError as e:
            return error_content(str(e))
    if args.get("parent_id") is not None:
        err = validate_parent(None, args["parent_id"])
        if err:
            return error_content(err)
        row["parent_id"] = args["parent_id"]
    status, data = sb_request("POST", body=row)
    if status >= 400:
        return error_content(f"Supabase {status}: {data}")
    created = data[0] if isinstance(data, list) else data
    return text_content(f"Created: {fmt_task(created)}")


def tool_list_tasks(args):
    params = {
        "user_id": f"eq.{KANBAN_USER_ID}",
        "order": "updated_at.desc",
        "limit": str(args.get("limit", 50)),
    }
    if args.get("status"):
        if args["status"] not in STATUSES:
            return error_content(f"status must be one of {STATUSES}")
        params["status"] = f"eq.{args['status']}"
    if args.get("context"):
        params["context"] = f"eq.{args['context']}"
    if args.get("timing"):
        if args["timing"] not in TIMINGS:
            return error_content(f"timing must be one of {TIMINGS}")
        params["timing"] = f"eq.{args['timing']}"
    if args.get("search"):
        params["title"] = f"ilike.*{args['search']}*"
    if not args.get("include_archived"):
        params["archived"] = "eq.false"
    if args.get("parent"):
        params["parent_id"] = "is.null" if args["parent"] in ("none", "top") else f"eq.{args['parent']}"
    status, data = sb_request("GET", params=params)
    if status >= 400:
        return error_content(f"Supabase {status}: {data}")
    if not data:
        return text_content("No tasks.")
    title_by_id, children_by_parent = build_hierarchy_maps()
    lines = []
    for r in data:
        line = f"- {fmt_task(r)}"
        pid = r.get("parent_id")
        if pid and title_by_id.get(pid):
            line += f"  ↳ in: {title_by_id[pid]}"
        kids = children_by_parent.get(r["id"])
        if kids:
            done = sum(1 for k in kids if k.get("status") == "done")
            line += f"  [project {done}/{len(kids)}]"
        lines.append(line)
    return text_content(f"{len(data)} task(s):\n" + "\n".join(lines))


def tool_update_task(args):
    task_id = args.get("id")
    if not task_id:
        return error_content("id is required")
    patch = {k: args[k] for k in EDITABLE_FIELDS if k in args and args[k] is not None and k != "parent_id"}
    # parent_id handled explicitly so an empty value can DETACH a task to "no project"
    if "parent_id" in args:
        pid = args["parent_id"] or None
        err = validate_parent(task_id, pid)
        if err:
            return error_content(err)
        patch["parent_id"] = pid
    if not patch:
        return error_content("no fields to update")
    if "status" in patch and patch["status"] not in STATUSES:
        return error_content(f"status must be one of {STATUSES}")
    if "timing" in patch and patch["timing"] not in TIMINGS:
        return error_content(f"timing must be one of {TIMINGS}")
    if "subtasks" in patch:
        try:
            patch["subtasks"] = normalize_subtasks(patch["subtasks"])
        except ValueError as e:
            return error_content(str(e))
    params = {"id": f"eq.{task_id}", "user_id": f"eq.{KANBAN_USER_ID}"}
    status, data = sb_request("PATCH", body=patch, params=params)
    if status >= 400:
        return error_content(f"Supabase {status}: {data}")
    if not data:
        return error_content(f"no task with id {task_id}")
    return text_content(f"Updated: {fmt_task(data[0])}")


def tool_move_task(args):
    return tool_update_task({"id": args.get("id"), "status": args.get("status")})


def tool_delete_task(args):
    task_id = args.get("id")
    if not task_id:
        return error_content("id is required")
    params = {"id": f"eq.{task_id}", "user_id": f"eq.{KANBAN_USER_ID}"}
    status, data = sb_request("DELETE", params=params)
    if status >= 400:
        return error_content(f"Supabase {status}: {data}")
    if not data:
        return error_content(f"no task with id {task_id}")
    return text_content(f"Deleted: {fmt_task(data[0])}")


def start_of_week_local(d):
    """Monday 00:00 of the week containing d (local time, naive)."""
    weekday = d.weekday()  # Mon=0..Sun=6
    monday = (d - timedelta(days=weekday)).replace(hour=0, minute=0, second=0, microsecond=0)
    return monday


def fmt_week_range(start):
    end = start + timedelta(days=6)
    if start.month == end.month:
        return f"{start.strftime('%b')} {start.day}–{end.day}"
    return f"{start.strftime('%b %-d')} – {end.strftime('%b %-d')}"


def tool_weekly_stats(args):
    weeks = int(args.get("weeks", 4))
    if weeks < 1 or weeks > 26:
        return error_content("weeks must be between 1 and 26")
    context = args.get("context", "Work")
    if context not in ("Work", "Personal", "all"):
        return error_content("context must be Work, Personal, or all")
    include_current = args.get("include_current", True)

    now_local = datetime.now()
    current_start = start_of_week_local(now_local)
    earliest = current_start - timedelta(weeks=weeks - (1 if include_current else 0))

    params = {
        "user_id": f"eq.{KANBAN_USER_ID}",
        "status": "eq.done",
        "completed_at": f"gte.{earliest.astimezone().isoformat()}",
        "select": "completed_at,context,effort,title",
        "order": "completed_at.desc",
        "limit": "1000",
    }
    if context != "all":
        params["context"] = f"eq.{context}"

    status, data = sb_request("GET", params=params)
    if status >= 400:
        return error_content(f"Supabase {status}: {data}")

    # Bucket by Monday-anchored local week
    buckets = {}
    for row in data or []:
        ts = row.get("completed_at")
        if not ts:
            continue
        # parse ISO (Postgres returns +00:00); convert to local
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            continue
        local_dt = dt.astimezone().replace(tzinfo=None)
        wk_start = start_of_week_local(local_dt)
        b = buckets.setdefault(wk_start, {"count": 0, "points": 0, "effort": {"S": 0, "M": 0, "L": 0, "XL": 0, "untagged": 0}})
        b["count"] += 1
        eff = row.get("effort")
        if eff in EFFORT_POINTS:
            b["effort"][eff] += 1
            b["points"] += EFFORT_POINTS[eff]
        else:
            b["effort"]["untagged"] += 1

    # Build ordered list of weeks (newest first)
    weeks_list = []
    for i in range(weeks):
        offset = 0 if include_current else 1
        start = current_start - timedelta(weeks=i + offset)
        weeks_list.append((start, buckets.get(start, {"count": 0, "points": 0, "effort": {"S": 0, "M": 0, "L": 0, "XL": 0, "untagged": 0}})))

    lines = [f"Weekly stats — context: {context}, weeks: {weeks}{' (excluding current)' if not include_current else ''}", ""]
    lines.append(f"{'Week':<22} {'Done':>5} {'Pts':>5}  Breakdown")
    lines.append("-" * 60)
    totals = {"count": 0, "points": 0}
    for start, b in weeks_list:
        label = fmt_week_range(start)
        if start == current_start:
            label += " (current)"
        breakdown_bits = []
        for k in ("S", "M", "L", "XL"):
            if b["effort"][k]:
                breakdown_bits.append(f"{k}:{b['effort'][k]}")
        if b["effort"]["untagged"]:
            breakdown_bits.append(f"?:{b['effort']['untagged']}")
        breakdown = " ".join(breakdown_bits) or "—"
        lines.append(f"{label:<22} {b['count']:>5} {b['points']:>5}  {breakdown}")
        totals["count"] += b["count"]
        totals["points"] += b["points"]

    lines.append("-" * 60)
    avg_count = totals["count"] / len(weeks_list)
    avg_points = totals["points"] / len(weeks_list)
    lines.append(f"{'Average':<22} {avg_count:>5.1f} {avg_points:>5.1f}")

    # Also count open work tasks missing effort (signal to nudge)
    if context in ("Work", "all"):
        params2 = {
            "user_id": f"eq.{KANBAN_USER_ID}",
            "status": "neq.done",
            "effort": "is.null",
            "select": "id",
            "limit": "1000",
        }
        if context == "Work":
            params2["context"] = "eq.Work"
        s2, d2 = sb_request("GET", params=params2)
        if s2 < 400 and isinstance(d2, list):
            n = len(d2)
            if n:
                lines.append("")
                lines.append(f"⚠ {n} open {'work ' if context == 'Work' else ''}task(s) missing effort — tag them so future weeks count fully.")

    return text_content("\n".join(lines))


TOOL_HANDLERS = {
    "create_task": tool_create_task,
    "list_tasks": tool_list_tasks,
    "update_task": tool_update_task,
    "move_task": tool_move_task,
    "delete_task": tool_delete_task,
    "weekly_stats": tool_weekly_stats,
}

TOOLS = [
    {
        "name": "create_task",
        "description": "Create a kanban task. Defaults to the inbox column.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "description": {"type": "string", "description": "longer notes / body"},
                "status": {"type": "string", "enum": STATUSES, "default": "inbox"},
                "context": {"type": "string", "description": "e.g. Personal, Work"},
                "source": {"type": "string"},
                "source_link": {"type": "string"},
                "timing": {"type": "string", "enum": TIMINGS, "description": "planning horizon: this-week / next-week / next-30-days (shown as 'Later')"},
                "effort": {"type": "string", "enum": ["S", "M", "L", "XL"], "description": "T-shirt size: S<30m, M=30m-2h, L=½ day, XL=multi-day"},
                "parent_id": {"type": "string", "description": "id of the parent task (project) this belongs under. Omit for a top-level task/project."},
                "subtasks": {
                    "type": "array",
                    "description": "Checklist items. Pass strings or {text, done} objects.",
                    "items": {
                        "oneOf": [
                            {"type": "string"},
                            {"type": "object", "properties": {"text": {"type": "string"}, "done": {"type": "boolean"}}, "required": ["text"]},
                        ],
                    },
                },
            },
            "required": ["title"],
        },
    },
    {
        "name": "list_tasks",
        "description": "List kanban tasks, most-recently-updated first. Optional filters by status, context, timing, and title substring. Archived tasks are hidden unless include_archived is true.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": STATUSES},
                "context": {"type": "string"},
                "timing": {"type": "string", "enum": TIMINGS},
                "search": {"type": "string", "description": "case-insensitive title substring match"},
                "parent": {"type": "string", "description": "filter to children of this project id; pass 'none' for top-level tasks/projects only"},
                "limit": {"type": "integer", "default": 50, "minimum": 1, "maximum": 500},
                "include_archived": {"type": "boolean", "description": "include archived (cleared) tasks; default false", "default": False},
            },
        },
    },
    {
        "name": "update_task",
        "description": "Update fields on an existing task by id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "status": {"type": "string", "enum": STATUSES},
                "context": {"type": "string"},
                "source": {"type": "string"},
                "source_link": {"type": "string"},
                "timing": {"type": "string", "enum": TIMINGS},
                "effort": {"type": "string", "enum": ["S", "M", "L", "XL"]},
                "archived": {"type": "boolean", "description": "archive (hide from board, keep in capacity stats) or unarchive"},
                "created_at": {"type": "string", "description": "ISO timestamp; set to now to reset staleness (Fresh/Stale/Very Stale is derived from this)"},
                "parent_id": {"type": "string", "description": "move this task under a project (parent task id); pass empty string to detach to no project. 2-level hierarchy only."},
                "subtasks": {
                    "type": "array",
                    "description": "Replace the full subtask list. Strings or {text, done} objects.",
                    "items": {
                        "oneOf": [
                            {"type": "string"},
                            {"type": "object", "properties": {"text": {"type": "string"}, "done": {"type": "boolean"}}, "required": ["text"]},
                        ],
                    },
                },
            },
            "required": ["id"],
        },
    },
    {
        "name": "move_task",
        "description": "Move a task to a different column. Shortcut for update_task with only the status field.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "status": {"type": "string", "enum": STATUSES},
            },
            "required": ["id", "status"],
        },
    },
    {
        "name": "delete_task",
        "description": "Delete a task by id. Irreversible.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        },
    },
    {
        "name": "weekly_stats",
        "description": (
            "Throughput report: tasks completed per week with effort points (S=1, M=2, L=5, XL=10). "
            "Defaults to last 4 weeks of Work tasks, Monday-anchored local weeks. "
            "Use weeks=1 + include_current=false for just last week, or context='all' for both Work and Personal."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "weeks": {"type": "integer", "default": 4, "minimum": 1, "maximum": 26, "description": "How many recent weeks to include."},
                "include_current": {"type": "boolean", "default": True, "description": "If false, skip the current (in-progress) week."},
                "context": {"type": "string", "enum": ["Work", "Personal", "all"], "default": "Work"},
            },
        },
    },
]


# ---------- JSON-RPC plumbing ----------

def text_content(s):
    return {"content": [{"type": "text", "text": s}]}


def error_content(s):
    return {"content": [{"type": "text", "text": s}], "isError": True}


def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def reply(req_id, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": req_id}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    send(msg)


def handle(req):
    method = req.get("method")
    req_id = req.get("id")
    is_notification = req_id is None

    if method == "initialize":
        client_version = (req.get("params") or {}).get("protocolVersion") or PROTOCOL_VERSION
        reply(req_id, {
            "protocolVersion": client_version,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })
        return
    if method in ("notifications/initialized", "notifications/cancelled"):
        return
    if method == "tools/list":
        reply(req_id, {"tools": TOOLS})
        return
    if method == "tools/call":
        params = req.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        handler = TOOL_HANDLERS.get(name)
        if not handler:
            reply(req_id, error={"code": -32601, "message": f"Unknown tool: {name}"})
            return
        try:
            result = handler(args)
        except Exception as e:
            result = error_content(f"{type(e).__name__}: {e}")
        reply(req_id, result)
        return
    if method == "ping":
        reply(req_id, {})
        return

    if not is_notification:
        reply(req_id, error={"code": -32601, "message": f"Method not found: {method}"})


def main():
    missing = [k for k, v in {
        "SUPABASE_URL": SUPABASE_URL,
        "SUPABASE_SECRET_KEY": SUPABASE_SECRET_KEY,
        "KANBAN_USER_ID": KANBAN_USER_ID,
    }.items() if not v]
    if missing:
        log(f"warning: missing env vars: {', '.join(missing)}")

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            log(f"bad json: {e}")
            continue
        try:
            handle(req)
        except Exception as e:
            log(f"handler crashed: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
