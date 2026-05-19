#!/usr/bin/env python3
"""Set the kanban user's password via Supabase admin API. Run once."""
import getpass
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
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SECRET_KEY")
    user_id = os.environ.get("KANBAN_USER_ID")
    missing = [k for k, v in [("SUPABASE_URL", url), ("SUPABASE_SECRET_KEY", key), ("KANBAN_USER_ID", user_id)] if not v]
    if missing:
        sys.exit(f"Missing in .env: {', '.join(missing)}")

    pw = getpass.getpass("New password: ")
    if not pw:
        sys.exit("aborted")
    if getpass.getpass("Confirm:       ") != pw:
        sys.exit("passwords don't match")

    req = urlrequest.Request(
        f"{url}/auth/v1/admin/users/{user_id}",
        data=json.dumps({"password": pw}).encode(),
        method="PUT",
    )
    req.add_header("apikey", key)
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Content-Type", "application/json")
    try:
        with urlrequest.urlopen(req) as r:
            data = json.loads(r.read() or b"{}")
            print(f"OK — password set for {data.get('email', user_id)}")
    except urlerror.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        sys.exit(f"Error {e.code}: {body}")


if __name__ == "__main__":
    main()
