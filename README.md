# WO tracking table automation

Drag a work order PDF onto the Notion Kanban board → within ~10 minutes the card gets the
tracking table (P/N | MACHINING | FA CHECKED | QTY IN QC | S/T | IN DELIVERY CABINET | FOR STOCK | REMARKS),
with one row per item read from the PDF, e.g. `1. GRIPPER BASE 4 PCS` / `1580CA014`, plus the legend row.

Cards that already have a table are never touched, so existing cards are safe.

## 1. Create the Notion integration (one time)
1. Go to https://www.notion.so/profile/integrations → **New integration** → type *Internal*, pick your workspace.
2. Capabilities: Read content, Update content, Insert content. Copy the **Internal Integration Secret**.
3. Open the **Kanban board** in Notion → `•••` (top right) → **Connections** → add your integration.

## 2a. Run it in the cloud (recommended — works even when your PC is off)
1. Create a **private** GitHub repo and upload `wo_table.py`, `requirements.txt` and `.github/workflows/wo-table.yml`.
2. Repo → Settings → Secrets and variables → Actions → **New repository secret**: name `NOTION_TOKEN`, value = the secret.
3. Actions tab → *WO tracking tables* → **Run workflow** to test. After that it runs every 10 minutes.

## 2b. Or run it on this PC
```
pip install -r requirements.txt
set NOTION_TOKEN=secret_xxx
set DRY_RUN=1
python wo_table.py
```
`DRY_RUN=1` only prints the items it found. Remove it to actually add tables, then schedule
`python wo_table.py` every 10 min with Windows Task Scheduler.

## Notes
- Only cards created in the last 48 h are checked (`LOOKBACK_HOURS` to change).
- If the PDF can't be read (e.g. a scanned image), the table is still added with a "fill in manually" row.
- To change columns or the legend, edit `HEADER_ROW` / `LEGEND_ROW` at the top of `wo_table.py`.
