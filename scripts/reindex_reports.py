"""Rebuild the local searchable index from the original PDFs."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import server  # noqa: E402


def main() -> None:
    server.init_db()
    pdfs = sorted(server.REPORTS_DIR.glob("*.pdf"))
    indexed = 0
    for number, path in enumerate(pdfs, 1):
        result = server.ingest_pdf(path)
        print(f"[{number}/{len(pdfs)}] {result['title']}", flush=True)
        indexed += 1
    print(f"Rebuilt {indexed} report records and source passages.", flush=True)


if __name__ == "__main__":
    main()
