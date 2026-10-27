"""Copy the existing local SQLite report index and PDFs into Supabase.

Requires SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in the environment.
The service role key is only used by this one-time local migration script;
never put it in static/config.js or commit it to GitHub.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "library.sqlite3"
REPORTS = ROOT / "data" / "reports"
BASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
BATCH_SIZE = 200


def request(path: str, *, method: str = "GET", body=None, content_type="application/json", prefer=None, extra_headers=None):
    headers = {"apikey": KEY, "Authorization": f"Bearer {KEY}"}
    if content_type:
        headers["Content-Type"] = content_type
    if prefer:
        headers["Prefer"] = prefer
    headers.update(extra_headers or {})
    data = body
    if isinstance(body, (dict, list)):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE_URL + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=180) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"Supabase returned HTTP {exc.code}: {detail}") from exc


def rest(table: str, rows: list[dict], conflict: str | None = None):
    if not rows:
        return
    query = f"/rest/v1/{table}"
    if conflict:
        query += "?on_conflict=" + urllib.parse.quote(conflict, safe=",")
    request(query, method="POST", body=rows, prefer="resolution=merge-duplicates,return=minimal")


def batched(rows):
    for start in range(0, len(rows), BATCH_SIZE):
        yield rows[start:start + BATCH_SIZE]


def main():
    if not BASE_URL or not KEY:
        sys.exit("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY first.")
    if not DB.exists():
        sys.exit(f"Local index not found: {DB}. Start the local app once to build it.")
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    reports = con.execute("select * from reports order by title").fetchall()
    print(f"Syncing {len(reports)} reports from {DB}")
    for number, report in enumerate(reports, 1):
        source = REPORTS / report["filename"]
        if not source.is_file():
            print(f"[{number}/{len(reports)}] Skipping missing PDF: {source.name}")
            continue
        object_path = f"{report['id']}/{report['filename']}"
        quoted_path = "/".join(urllib.parse.quote(part, safe="") for part in object_path.split("/"))
        with source.open("rb") as pdf:
            request(f"/storage/v1/object/reports/{quoted_path}", method="POST", body=pdf.read(),
                    content_type="application/pdf", extra_headers={"x-upsert": "true"})
        row = dict(report)
        row["warnings"] = json.loads(row.get("warnings") or "[]")
        row["object_path"] = object_path
        row["indexed_at"] = datetime.now(timezone.utc).isoformat()
        rest("reports", [row], "id")

        filter_id = urllib.parse.quote(report["id"], safe="")
        request(f"/rest/v1/report_chunks?report_id=eq.{filter_id}", method="DELETE")
        request(f"/rest/v1/report_sections?report_id=eq.{filter_id}", method="DELETE")
        sections = [dict(r) for r in con.execute("select report_id,name,first_page from sections where report_id=?", (report["id"],))]
        for batch in batched(sections):
            rest("report_sections", batch, "report_id,name,first_page")

        chunks = [dict(r) for r in con.execute("select report_id,page,section,content from chunks where report_id=?", (report["id"],))]
        for batch in batched(chunks):
            rest("report_chunks", batch)
        print(f"[{number}/{len(reports)}] {report['title']} — {len(chunks)} passages")
    con.close()
    print("Done. Reports, PDFs and searchable page passages are in Supabase.")


if __name__ == "__main__":
    main()
