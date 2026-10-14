"""
Auto-generate the tracking table on new Work Order cards in the Notion Kanban board.

How it works (runs every few minutes via GitHub Actions or Windows Task Scheduler):
  1. Find Kanban cards created in the last LOOKBACK_HOURS.
  2. Skip any card that already has a table.
  3. Find the work order PDF on the card, download it, read the item list.
  4. Append the tracking table (same columns as WO 002180) to the card.

Env vars:
  NOTION_TOKEN     - internal integration secret (connect the integration to the Kanban board)
  DATA_SOURCE_ID   - Kanban board data source id (default below)
  LOOKBACK_HOURS   - how far back to look for new cards (default 48)
  DRY_RUN=1        - print what would be added, change nothing
"""

import io
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import requests
from pypdf import PdfReader

NOTION_TOKEN = os.environ["NOTION_TOKEN"]
DATA_SOURCE_ID = os.environ.get("DATA_SOURCE_ID", "be06bee9-f5ae-8320-8b88-074831549d92")
LOOKBACK_HOURS = int(os.environ.get("LOOKBACK_HOURS", "48"))
DRY_RUN = os.environ.get("DRY_RUN") == "1"

API = "https://api.notion.com/v1"
HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2025-09-03",
    "Content-Type": "application/json",
}

HEADER_ROW = [
    "P/N",
    "MACHINING (Incoming QTY)",
    "FA CHECKED",
    "QTY IN QC",
    "S/T (QTY), DATE",
    "IN DELIVERY CABINET",
    "FOR STOCK. KEPT AT",
    "REMARKS",
]
LEGEND_ROW = [
    "LEGEND",
    "COM - COMPLETED",
    "BLANK - NOT STARTED",
    "IP - IN PROGRESS",
    "NR - NOT REQUIRED",
    "",
    "",
    "",
]

# "1. GRIPPER BASE 4"  ->  item no, description, qty
ITEM_RE = re.compile(r"^\s*(\d{1,3})\.\s+(.+?)\s+(\d+)\s*$")


# ---------------------------------------------------------------- Notion API

def notion(method, path, **kwargs):
    r = requests.request(method, f"{API}{path}", headers=HEADERS, timeout=60, **kwargs)
    if r.status_code >= 400:
        raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text}")
    return r.json()


def new_cards():
    since = (datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)).isoformat()
    body = {
        "filter": {"timestamp": "created_time", "created_time": {"on_or_after": since}},
        "page_size": 100,
    }
    while True:
        res = notion("POST", f"/data_sources/{DATA_SOURCE_ID}/query", json=body)
        yield from res["results"]
        if not res.get("has_more"):
            break
        body["start_cursor"] = res["next_cursor"]


def children(block_id):
    cursor = None
    while True:
        q = f"?page_size=100" + (f"&start_cursor={cursor}" if cursor else "")
        res = notion("GET", f"/blocks/{block_id}/children{q}")
        yield from res["results"]
        if not res.get("has_more"):
            break
        cursor = res["next_cursor"]


def page_title(page):
    for prop in page["properties"].values():
        if prop["type"] == "title":
            return "".join(t["plain_text"] for t in prop["title"])
    return page["id"]


def find_pdf_url(blocks):
    for b in blocks:
        if b["type"] not in ("file", "pdf"):
            continue
        f = b[b["type"]]
        url = f["file"]["url"] if f["type"] == "file" else f["external"]["url"]
        name = f.get("name") or url.split("?")[0]
        if name.lower().endswith(".pdf"):
            return url
    return None


# ---------------------------------------------------------------- PDF parsing

def parse_items(pdf_bytes):
    """Return [(no, description, qty, part_no), ...] from a LE-PLUS work order PDF."""
    reader = PdfReader(io.BytesIO(pdf_bytes))
    lines = []
    for page in reader.pages:
        lines += [l.strip() for l in (page.extract_text() or "").splitlines() if l.strip()]

    items = []
    for i, line in enumerate(lines):
        m = ITEM_RE.match(line)
        if not m:
            continue
        no, desc, qty = m.groups()
        part_no = lines[i + 1] if i + 1 < len(lines) else ""
        # The P/N line is a single token like 1580CA050_A; anything else isn't a P/N
        if " " in part_no or ITEM_RE.match(part_no):
            part_no = ""
        items.append((int(no), desc.strip(), int(qty), part_no))

    # De-duplicate (in case a line is repeated) and keep item order
    seen, out = set(), []
    for it in sorted(items, key=lambda x: x[0]):
        if it[0] not in seen:
            seen.add(it[0])
            out.append(it)
    return out


# ---------------------------------------------------------------- table building

def cell(text):
    return [{"type": "text", "text": {"content": text}}] if text else []


def row(cells):
    return {"object": "block", "type": "table_row", "table_row": {"cells": [cell(c) for c in cells]}}


def build_rows(items):
    rows = [row(HEADER_ROW)]
    for no, desc, qty, pn in items:
        first = f"{no}. {desc} {qty} PCS" + (f"\n{pn}" if pn else "")
        rows.append(row([first] + [""] * 7))
    if not items:
        rows.append(row(["(Could not read items from PDF - fill in manually)"] + [""] * 7))
    rows.append(row(LEGEND_ROW))
    return rows


def append_table(page_id, rows):
    # Notion allows max 100 children per request: create the table with the
    # first batch, then append the remaining rows to the table itself.
    first, rest = rows[:100], rows[100:]
    table = {
        "object": "block",
        "type": "table",
        "table": {
            "table_width": len(HEADER_ROW),
            "has_column_header": True,
            "has_row_header": False,
            "children": first,
        },
    }
    res = notion("PATCH", f"/blocks/{page_id}/children", json={"children": [table]})
    table_id = res["results"][0]["id"]
    for i in range(0, len(rest), 100):
        notion("PATCH", f"/blocks/{table_id}/children", json={"children": rest[i:i + 100]})


# ---------------------------------------------------------------- main

def main():
    done = 0
    for page in new_cards():
        title = page_title(page)
        blocks = list(children(page["id"]))

        if any(b["type"] == "table" for b in blocks):
            continue  # already has a tracking table
        pdf_url = find_pdf_url(blocks)
        if not pdf_url:
            continue  # not a work order card (or PDF still uploading)

        pdf = requests.get(pdf_url, timeout=120).content
        items = parse_items(pdf)
        print(f"[{title}] {len(items)} items")
        for it in items:
            print(f"   {it[0]:>3}. {it[1]}  x{it[2]}  {it[3]}")

        if not DRY_RUN:
            append_table(page["id"], build_rows(items))
            done += 1

    print(f"Tables added: {done}" + (" (dry run)" if DRY_RUN else ""))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
