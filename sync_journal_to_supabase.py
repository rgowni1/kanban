#!/usr/bin/env python3
"""Synchronize the Notion Logging Journal into Supabase.

Designed for both local runs and GitHub Actions. Existing rows are updated by
(user_id, week), so rerunning the job is safe.
"""
import json
import os
import re
import sys
from datetime import datetime, timezone
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

NOTION_VERSION = "2022-06-28"
# Below this daily average the food log was abandoned mid-week, not eaten.
MIN_PLAUSIBLE_DAILY_CALS = 900


def load_env(path=".env"):
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def http(url, *, method="GET", headers=None, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    req = urlrequest.Request(url, data=data, method=method)
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urlrequest.urlopen(req, timeout=timeout) as response:
            payload = response.read()
            return response.status, json.loads(payload or b"null")
    except urlerror.HTTPError as exc:
        try:
            payload = json.loads(exc.read() or b"{}")
        except Exception:
            payload = {"error": str(exc)}
        return exc.code, payload


def page_to_row(page, user_id, synced_at):
    props = page.get("properties", {})

    def number(name):
        return (props.get(name) or {}).get("number")

    def formula_number(name):
        formula = (props.get(name) or {}).get("formula") or {}
        if formula.get("type") == "number":
            return formula.get("number")
        if formula.get("type") == "string":
            match = re.search(r"-?[\d,]+(?:\.\d+)?", formula.get("string") or "")
            return float(match.group(0).replace(",", "")) if match else None
        return None

    def positive_formula_number(name):
        value = formula_number(name)
        return value if value is not None and value > 0 else None

    def date_value(name):
        return ((props.get(name) or {}).get("date") or {}).get("start")

    def rich_text(name):
        parts = (props.get(name) or {}).get("rich_text", [])
        return "".join(part.get("plain_text", "") for part in parts) or None

    row = {
        "user_id": user_id,
        "week": date_value("Week of Journal"),
        "notion_page_id": page.get("id"),
        "source_updated_at": page.get("last_edited_time"),
        "synced_at": synced_at,
        "slp_hrs": number("Slp Hrs"),
        "slp_score": number("Slp Score"),
        "slp_hrv": number("Slp HRV"),
        "rhr": number("RHR"),
        "avg_sleep_time": rich_text("Avg Sleep Time"),
        "avg_wake_time": rich_text("Avg Wake Time"),
        "run_miles": number("Run Miles"),
        "bike_miles": number("Bike Miles"),
        "swim_yards": number("Swim Yards"),
        "strength_mins": number("Strength Minutes"),
        "intensity_mins": number("Intensity Minutes"),
        "step_count": number("Step Count"),
        "body_fat": number("Body Fat %"),
        "weekly_weight": number("Weekly Weight"),
        "stress": number("Stress Level"),
        "meditation": number("Meditation Sessions"),
        "articles": number("Articles"),
        "podcasts": number("Podcasts & Videos"),
        "phone_pickups": number("Total Ph Pick Ups"),
        "avg_daily_pickups": formula_number("Avg Daily Pick Ups"),
        "training_hrs": number("Logged Training Hrs"),
        "consumed_cals": positive_formula_number("Consumed Cals"),
        "avg_protein": positive_formula_number("Avg Protein"),
        "calorie_deficit": formula_number("Calorie Deficit"),
        "garmin_tdee": number("Garmin TDEE"),
    }

    # The Notion food-log formulas average only the days that were actually
    # logged, so a week where logging was abandoned after one meal reports a
    # real-looking-but-tiny daily average (e.g. week of 2026-04-27: 201 kcal,
    # 13 g protein) that wrecks every nutrition trend. Treat a week below a
    # subsistence floor as unlogged rather than as a genuine near-fast, and drop
    # the whole nutrition triple together since the deficit is derived from
    # intake. Every genuinely logged week to date sits above 1200 kcal/day.
    if row["consumed_cals"] is not None and row["consumed_cals"] < MIN_PLAUSIBLE_DAILY_CALS:
        row["consumed_cals"] = None
        row["avg_protein"] = None
        row["calorie_deficit"] = None

    return row


def fetch_notion_rows(token, database_id, user_id):
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    rows = []
    cursor = None
    synced_at = datetime.now(timezone.utc).isoformat()
    while True:
        body = {
            "page_size": 100,
            "sorts": [{"property": "Week of Journal", "direction": "ascending"}],
        }
        if cursor:
            body["start_cursor"] = cursor
        status, payload = http(
            f"https://api.notion.com/v1/databases/{database_id}/query",
            method="POST",
            headers=headers,
            body=body,
        )
        if status >= 400:
            sys.exit(f"Notion error {status}: {payload}")
        rows.extend(
            row for row in (
                page_to_row(page, user_id, synced_at)
                for page in payload.get("results", [])
            )
            if row.get("week")
        )
        if not payload.get("has_more"):
            return rows
        cursor = payload.get("next_cursor")
        if not cursor:
            return rows


def upsert_supabase_rows(supabase_url, secret_key, rows):
    headers = {
        "apikey": secret_key,
        "Authorization": f"Bearer {secret_key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    endpoint = (
        f"{supabase_url}/rest/v1/journal_entries?"
        + urlparse.urlencode({"on_conflict": "user_id,week"})
    )
    synced = 0
    for start in range(0, len(rows), 250):
        chunk = rows[start:start + 250]
        status, payload = http(endpoint, method="POST", headers=headers, body=chunk)
        if status >= 400:
            sys.exit(f"Supabase error {status}: {payload}")
        synced += len(chunk)
    return synced


def main():
    load_env(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
    required = {
        "NOTION_TOKEN": os.environ.get("NOTION_TOKEN"),
        "NOTION_JOURNAL_DB_ID": os.environ.get("NOTION_JOURNAL_DB_ID"),
        "SUPABASE_URL": os.environ.get("SUPABASE_URL"),
        "SUPABASE_SECRET_KEY": os.environ.get("SUPABASE_SECRET_KEY"),
        "KANBAN_USER_ID": os.environ.get("KANBAN_USER_ID"),
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        sys.exit(f"Missing environment variables: {', '.join(missing)}")

    print("Fetching journal entries from Notion...", flush=True)
    rows = fetch_notion_rows(
        required["NOTION_TOKEN"],
        required["NOTION_JOURNAL_DB_ID"],
        required["KANBAN_USER_ID"],
    )
    print(f"Fetched {len(rows)} dated journal entries.", flush=True)
    seen = {}
    for row in rows:
        seen[row["week"]] = row
    rows = list(seen.values())
    if len(rows) != len(seen):
        print(f"Deduplicated to {len(rows)} unique weeks.", flush=True)
    if "--dry-run" in sys.argv:
        print("Dry run complete; Supabase was not changed.")
        return

    synced = upsert_supabase_rows(
        required["SUPABASE_URL"],
        required["SUPABASE_SECRET_KEY"],
        rows,
    )
    print(f"Upserted {synced} journal entries into Supabase.")


if __name__ == "__main__":
    main()
