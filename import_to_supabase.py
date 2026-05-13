#!/usr/bin/env python3
"""One-time import: Notion DB → Supabase tasks table. Python stdlib only."""
import json
import os
import sys
from urllib import error as urlerror
from urllib import request as urlrequest

NOTION_VERSION = "2022-06-28"
TITLE_PROP = "Task name`"
STATUS_PROP = "Status - New"
NOTION_TO_COL = {
    "Inbox": "inbox",
    "Not started": "not-started",
    "In Progress": "in-progress",
    "Focus": "focus",
    "Done": "done",
}


def load_env(path=".env"):
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def http(url, *, method="GET", headers=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urlrequest.Request(url, data=data, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urlrequest.urlopen(req) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urlerror.HTTPError as e:
        try:
            payload = json.loads(e.read() or b"{}")
        except Exception:
            payload = {"error": str(e)}
        return e.code, payload


def select_name(props, key):
    sel = (props.get(key) or {}).get("select")
    return sel.get("name") if sel else None


def page_to_row(page, user_id):
    props = page.get("properties", {})
    title_arr = (props.get(TITLE_PROP) or {}).get("title", [])
    title = "".join(t.get("plain_text", "") for t in title_arr) or "(untitled)"
    notion_status = ((props.get(STATUS_PROP) or {}).get("status") or {}).get("name") or "Not started"
    return {
        "user_id": user_id,
        "title": title,
        "status": NOTION_TO_COL.get(notion_status, "not-started"),
        "context": select_name(props, "Context"),
        "source": select_name(props, "Source"),
        "source_link": (props.get("Source Link") or {}).get("url"),
        "priority": select_name(props, "Priority"),
        "notion_id": page["id"],
        "created_at": page.get("created_time"),
        "updated_at": page.get("last_edited_time"),
    }


def fetch_notion_pages(token, db_id):
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    pages = []
    cursor = None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        status, data = http(
            f"https://api.notion.com/v1/databases/{db_id}/query",
            method="POST", headers=headers, body=body,
        )
        if status >= 400:
            sys.exit(f"Notion error {status}: {data}")
        pages.extend(data.get("results", []))
        if not data.get("has_more"):
            return pages
        cursor = data.get("next_cursor")
        if not cursor:
            return pages


def post_to_supabase(supabase_url, secret_key, rows):
    if not rows:
        return 0
    headers = {
        "apikey": secret_key,
        "Authorization": f"Bearer {secret_key}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }
    inserted = 0
    for i in range(0, len(rows), 500):
        chunk = rows[i:i + 500]
        status, data = http(
            f"{supabase_url}/rest/v1/tasks",
            method="POST", headers=headers, body=chunk,
        )
        if status >= 400:
            sys.exit(f"Supabase error {status}: {data}")
        inserted += len(chunk)
    return inserted


def main():
    load_env()
    required = {
        "NOTION_TOKEN": os.environ.get("NOTION_TOKEN"),
        "NOTION_DB_ID": os.environ.get("NOTION_DB_ID"),
        "SUPABASE_URL": os.environ.get("SUPABASE_URL"),
        "SUPABASE_SECRET_KEY": os.environ.get("SUPABASE_SECRET_KEY"),
        "KANBAN_USER_ID": os.environ.get("KANBAN_USER_ID"),
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        sys.exit(f"Missing env vars: {', '.join(missing)}")

    print("Fetching from Notion...", flush=True)
    pages = fetch_notion_pages(required["NOTION_TOKEN"], required["NOTION_DB_ID"])
    print(f"  fetched {len(pages)} pages", flush=True)

    rows = [page_to_row(p, required["KANBAN_USER_ID"]) for p in pages]
    skipped_done = sum(1 for r in rows if r["status"] == "done")
    rows = [r for r in rows if r["status"] != "done"]
    by_status = {}
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    print(f"  by status: {by_status} (skipped {skipped_done} done)", flush=True)
    if not rows:
        print("Nothing to import. Done.")
        return

    print("\nFirst row preview:", flush=True)
    print(f"  {json.dumps(rows[0], indent=2)}", flush=True)

    confirm = input(f"\nInsert {len(rows)} rows into Supabase? [y/N] ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        return

    n = post_to_supabase(required["SUPABASE_URL"], required["SUPABASE_SECRET_KEY"], rows)
    print(f"\nInserted {n} rows.")


if __name__ == "__main__":
    main()
