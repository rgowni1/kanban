#!/usr/bin/env python3
"""Kanban backend — Notion-backed REST API. Python stdlib only."""
import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import error as urlerror
from urllib import request as urlrequest

PORT = 5173
NOTION_VERSION = "2022-06-28"
TITLE_PROP = "Task name`"
STATUS_PROP = "Status - New"

COL_TO_NOTION = {
    "inbox": "Inbox",
    "not-started": "Not started",
    "in-progress": "In Progress",
    "focus": "Focus",
    "done": "Done",
}
NOTION_TO_COL = {v: k for k, v in COL_TO_NOTION.items()}


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


load_env()
NOTION_TOKEN = os.environ.get("NOTION_TOKEN")
NOTION_DB_ID = os.environ.get("NOTION_DB_ID")
if not NOTION_TOKEN or not NOTION_DB_ID:
    sys.exit("Missing NOTION_TOKEN or NOTION_DB_ID in .env")


def notion(path, method="GET", body=None):
    url = f"https://api.notion.com/v1{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urlrequest.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {NOTION_TOKEN}")
    req.add_header("Notion-Version", NOTION_VERSION)
    req.add_header("Content-Type", "application/json")
    try:
        with urlrequest.urlopen(req) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urlerror.HTTPError as e:
        try:
            payload = json.loads(e.read() or b"{}")
        except Exception:
            payload = {"error": str(e)}
        return e.code, payload


def _select_name(props, key):
    sel = (props.get(key) or {}).get("select")
    return sel.get("name") if sel else None


def page_to_task(page):
    props = page.get("properties", {})
    title_arr = (props.get(TITLE_PROP) or {}).get("title", [])
    title = "".join(t.get("plain_text", "") for t in title_arr) or "(untitled)"
    notion_status = ((props.get(STATUS_PROP) or {}).get("status") or {}).get("name") or "Not started"
    aging = (props.get("Aging Automated") or {}).get("formula") or {}
    staleness = aging.get("string") if aging.get("type") == "string" else None
    return {
        "id": page["id"],
        "title": title,
        "status": NOTION_TO_COL.get(notion_status, "not-started"),
        "notionStatus": notion_status,
        "context": _select_name(props, "Context"),
        "source": _select_name(props, "Source"),
        "sourceLink": (props.get("Source Link") or {}).get("url"),
        "priority": _select_name(props, "Priority"),
        "staleness": staleness,
        "createdAt": page.get("created_time"),
        "updatedAt": page.get("last_edited_time"),
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write(f"{self.command} {self.path} - {fmt % args}\n")

    def _send(self, status, body=None, ctype="application/json"):
        if isinstance(body, (dict, list)):
            payload = json.dumps(body).encode()
        elif isinstance(body, bytes):
            payload = body
        elif body is None:
            payload = b""
        else:
            payload = str(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PATCH,DELETE,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    def _read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return {}

    def do_OPTIONS(self):
        self._send(204)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            try:
                with open("index.html", "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except FileNotFoundError:
                self._send(404, "not found", "text/plain")
            return
        if self.path == "/api/tasks":
            tasks = []
            cursor = None
            while True:
                body = {
                    "page_size": 100,
                    "sorts": [{"timestamp": "last_edited_time", "direction": "descending"}],
                }
                if cursor:
                    body["start_cursor"] = cursor
                status, data = notion(
                    f"/databases/{NOTION_DB_ID}/query",
                    method="POST",
                    body=body,
                )
                if status >= 400:
                    self._send(status, data)
                    return
                tasks.extend(page_to_task(p) for p in data.get("results", []))
                if not data.get("has_more"):
                    break
                cursor = data.get("next_cursor")
                if not cursor:
                    break
            self._send(200, {"tasks": tasks})
            return
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/api/tasks":
            body = self._read_json()
            title = (body.get("title") or "").strip()
            col = body.get("status") or "inbox"
            if not title:
                self._send(400, {"error": "title required"})
                return
            notion_status = COL_TO_NOTION.get(col, "Inbox")
            properties = {
                TITLE_PROP: {"title": [{"text": {"content": title}}]},
                STATUS_PROP: {"status": {"name": notion_status}},
                "Context": {"select": {"name": body.get("context") or "Personal"}},
                "Source": {"select": {"name": body.get("source") or "Manual"}},
            }
            if body.get("sourceLink"):
                properties["Source Link"] = {"url": body["sourceLink"]}
            status, data = notion(
                "/pages",
                method="POST",
                body={"parent": {"database_id": NOTION_DB_ID}, "properties": properties},
            )
            if status >= 400:
                self._send(status, data)
                return
            self._send(200, page_to_task(data))
            return
        self._send(404, {"error": "not found"})

    def do_PATCH(self):
        m = re.match(r"^/api/tasks/([^/]+)$", self.path)
        if not m:
            self._send(404, {"error": "not found"})
            return
        page_id = m.group(1)
        body = self._read_json()
        properties = {}
        if "title" in body:
            properties[TITLE_PROP] = {"title": [{"text": {"content": body["title"]}}]}
        if "status" in body:
            ns = COL_TO_NOTION.get(body["status"])
            if not ns:
                self._send(400, {"error": "bad status"})
                return
            properties[STATUS_PROP] = {"status": {"name": ns}}
        if "context" in body:
            properties["Context"] = {"select": {"name": body["context"]} if body["context"] else None}
        if "source" in body:
            properties["Source"] = {"select": {"name": body["source"]} if body["source"] else None}
        if "sourceLink" in body:
            properties["Source Link"] = {"url": body["sourceLink"] or None}
        if not properties:
            self._send(400, {"error": "nothing to update"})
            return
        status, data = notion(f"/pages/{page_id}", method="PATCH", body={"properties": properties})
        if status >= 400:
            self._send(status, data)
            return
        self._send(200, page_to_task(data))

    def do_DELETE(self):
        m = re.match(r"^/api/tasks/([^/]+)$", self.path)
        if not m:
            self._send(404, {"error": "not found"})
            return
        page_id = m.group(1)
        status, data = notion(f"/pages/{page_id}", method="PATCH", body={"archived": True})
        if status >= 400:
            self._send(status, data)
            return
        self._send(200, {"ok": True})


if __name__ == "__main__":
    print(f"Kanban: http://localhost:{PORT}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
