"""Apply manually verified PDF cover metadata to the local report index."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "library.sqlite3"
OVERRIDES = ROOT / "scripts" / "metadata_overrides.json"


def main() -> None:
    corrections = json.loads(OVERRIDES.read_text(encoding="utf-8"))
    with sqlite3.connect(DB) as connection:
        rows = connection.executemany(
            "UPDATE reports SET title = ?, authors = ? WHERE id = ?",
            [(entry["title"], entry["authors"], report_id) for report_id, entry in corrections.items()],
        )
        changed = connection.total_changes
    if changed != len(corrections):
        raise SystemExit(f"Expected to update {len(corrections)} reports, updated {changed}.")
    print(f"Applied verified title and author metadata to {changed} reports.")


if __name__ == "__main__":
    main()
