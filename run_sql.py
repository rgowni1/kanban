#!/usr/bin/env python3
"""Run arbitrary SQL against the Supabase project via the Management API.

Usage:
    python3 run_sql.py "ALTER TABLE tasks ADD COLUMN foo TEXT;"
    cat migration.sql | python3 run_sql.py
"""
import json
import os
import sys
from urllib import error as urlerror
from urllib import request as urlrequest


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


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    load_env(os.path.join(here, ".env"))
    pat = os.environ.get("SUPABASE_ACCESS_TOKEN")
    url = os.environ.get("SUPABASE_URL", "")
    if not pat:
        sys.exit("Missing SUPABASE_ACCESS_TOKEN in .env")
    ref = url.replace("https://", "").split(".", 1)[0]
    if not ref:
        sys.exit("Could not derive project ref from SUPABASE_URL")

    sql = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else sys.stdin.read()
    if not sql.strip():
        sys.exit("No SQL provided")

    print(f"Project {ref}:\n  {sql.strip()}\n")
    req = urlrequest.Request(
        f"https://api.supabase.com/v1/projects/{ref}/database/query",
        data=json.dumps({"query": sql}).encode(),
        method="POST",
    )
    req.add_header("Authorization", f"Bearer {pat}")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "kanban-migrator/1.0")
    try:
        with urlrequest.urlopen(req) as r:
            data = json.loads(r.read() or b"null")
            print("OK")
            if data:
                print(json.dumps(data, indent=2))
    except urlerror.HTTPError as e:
        sys.exit(f"Error {e.code}: {e.read().decode('utf-8', 'replace')}")


if __name__ == "__main__":
    main()
