#!/usr/bin/env python3
"""
eow_digest.py — End-of-week Slack nudge: tasks completed THIS week (Work) with no
effort level tagged. Mirrors the in-app Stats "Done this week · no effort" list:
Monday-anchored week, projects excluded (their effort rolls up from subtasks).

Reads SUPABASE_URL / SUPABASE_SECRET_KEY / KANBAN_USER_ID / SLACK_WEBHOOK_URL from
.env (same dir). Queries Supabase REST directly with the service key (no browser
session needed — this is why the kanban can run headless). Posts to a Slack
Incoming Webhook. If SLACK_WEBHOOK_URL is unset it prints the digest and exits 0,
so a scheduled run is harmless before the webhook is configured.

Run:  python3 eow_digest.py            # send (or print if no webhook)
      python3 eow_digest.py --dry-run  # always print, never send
"""
import json, os, sys, urllib.request, urllib.error
from datetime import datetime, timedelta
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parent / ".env"


def load_env():
    env = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def monday_start(now):
    d = now.replace(hour=0, minute=0, second=0, microsecond=0)
    diff = -6 if d.weekday() == 6 else (0 - d.weekday())  # Python: Mon=0..Sun=6
    return d + timedelta(days=diff)


def fetch_tasks(env):
    url = env["SUPABASE_URL"].rstrip("/") + "/rest/v1/tasks?select=*"
    req = urllib.request.Request(url, headers={
        "apikey": env["SUPABASE_SECRET_KEY"],
        "Authorization": "Bearer " + env["SUPABASE_SECRET_KEY"],
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def main():
    dry = "--dry-run" in sys.argv
    env = load_env()
    for key in ("SUPABASE_URL", "SUPABASE_SECRET_KEY"):
        if not env.get(key):
            print(f"[eow_digest] missing {key} in .env — abort", file=sys.stderr)
            return 1

    now = datetime.now()
    ws = monday_start(now)
    we = ws + timedelta(days=7)
    uid = env.get("KANBAN_USER_ID")

    tasks = fetch_tasks(env)
    # parent ids that have children → those parents are "projects" (effort rolls up)
    parents = {t.get("parent_id") for t in tasks if t.get("parent_id")}

    def done_in_week(t):
        ca = t.get("completed_at")
        if not ca:
            return False
        try:
            dt = datetime.fromisoformat(ca.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return False
        return ws <= dt < we

    flagged = [
        t for t in tasks
        if (not uid or t.get("user_id") == uid)
        and t.get("context") == "Work"
        and t.get("status") == "done"
        and not t.get("effort")
        and not t.get("archived")
        and t.get("id") not in parents          # exclude project containers
        and done_in_week(t)
    ]

    week_label = f"{ws.strftime('%b %-d')}–{(we - timedelta(days=1)).strftime('%-d')}"
    if not flagged:
        text = f":white_check_mark: EOW ({week_label}): every Work task finished this week is effort-tagged. Nice."
    else:
        lines = "\n".join(f"• {t.get('title') or 'Untitled'}" for t in flagged)
        text = (f":arrow_down: *EOW effort check ({week_label})* — "
                f"{len(flagged)} Work task{'s' if len(flagged) != 1 else ''} finished this week "
                f"with no effort tagged:\n{lines}\n_Tag them in the Stats page so weekly points stay honest._")

    hook = env.get("SLACK_WEBHOOK_URL")
    if dry or not hook:
        why = "dry-run" if dry else "no SLACK_WEBHOOK_URL set"
        print(f"[eow_digest] ({why}) would post:\n{text}")
        return 0

    body = json.dumps({"text": text}).encode()
    req = urllib.request.Request(hook, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            r.read()
        print(f"[eow_digest] posted to Slack ({len(flagged)} flagged).")
        return 0
    except urllib.error.HTTPError as e:
        print(f"[eow_digest] Slack post failed: {e.code} {e.read().decode(errors='replace')}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
