from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from pypdf import PdfReader

logging.getLogger("pypdf").setLevel(logging.ERROR)


ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
REPORTS_DIR = ROOT / "data" / "reports"
DB_PATH = ROOT / "data" / "library.sqlite3"
METADATA_OVERRIDES = json.loads((ROOT / "scripts" / "metadata_overrides.json").read_text(encoding="utf-8"))
MAX_UPLOAD = 80 * 1024 * 1024
ADMIN_TOKEN = os.environ.get("TERI_ADMIN_TOKEN", "")
HOST = os.environ.get("TERI_HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", os.environ.get("TERI_PORT", "8000")))
DB_LOCK = threading.RLock()

PUBLIC_URLS = {
    "Exploring_Electricity_Supply_Mix_Scenarios_to_2030.pdf": "https://www.teriin.org/sites/default/files/2019-02/Exploring%20Electricity%20Supply-Mix%20Scenarios%20to%202030.pdf",
    "Developing_Roadmap_Flexible_Low_Carbon_Indian_Electricity_System.pdf": "https://www.teriin.org/sites/default/files/2019-02/Developing%20a%20roadmap%20to%20a%20flexible-low%E2%80%90carbon%20Indian%20electricity%20system.pdf",
    "Towards_a_Low_Carbon_Steel_Sector_Report.pdf": "https://www.energy-transitions.org/wp-content/uploads/2024/01/Towards-a-Low-Carbon-Steel-Sector-Report.pdf",
    "Solar_PV_Manufacturing_in_India.pdf": "https://www.teriin.org/sites/default/files/2019-08/Solar%20PV%20Manufacturing%20in%20India.pdf",
    "A_Modal_Based_Assessment_Report.pdf": "https://www.teriin.org/sites/default/files/2021-02/A_Modal-Based_Assessment_Report_0.pdf",
    "Mapping_Impact_of_Coal_Mines_Case_of_Betul.pdf": "https://www.teriin.org/sites/default/files/2021-03/Mapping-Impact-of-Coal-Mines-Case-of-Betul.pdf",
    "Bending_the_Curve_Report.pdf": "https://www.teriin.org/sites/default/files/2020-09/Bending-the-Curve_Report.pdf",
    "Renewable_Power_Pathways_Report.pdf": "https://www.teriin.org/sites/default/files/2020-07/Renewable-Power-Pathways-Report.pdf",
    "Potential_Role_of_Hydrogen_in_India.pdf": "https://www.teriin.org/sites/default/files/2020-12/potential-role-hydrogen-india.pdf",
    "Decentralised_Solar_PV_Report.pdf": "https://www.teriin.org/sites/default/files/files/Decentralised_solar_PV_Report.pdf",
    "Roadmap_to_India_2030_Decarbonization_Target.pdf": "https://www.teriin.org/sites/default/files/files/Roadmap-to-India-2030-Decarbonization-Target.pdf",
    "Pumped_Storage_Plants_Discussion_Paper_2023.pdf": "https://www.teriin.org/sites/default/files/2024-01/Pumped_Storage%20_Plants_Discussion_Paper_2023.pdf",
    "Power_Sector_2050_Report.pdf": "https://www.teriin.org/sites/default/files/2024-02/Power%20Sector%202050-Report.pdf",
    "Just_Transition_Framework_Coal_Mining_Regions.pdf": "https://teriin.org/sites/default/files/2024-02/Just%20Transition%20Framework%20for%20a%20Sustainable%20Future%20in%20India%E2%80%99s%20Coal%20Mining%20Regions.pdf",
    "Net_Zero_Report_2024.pdf": "https://www.teriin.org/sites/default/files/2024-05/Net%20Zero%20Report%20_24-5-2024.pdf",
    "Solar_Plus_Storage_Cost_Analysis_Paper.pdf": "https://teriin.org/files/Solar_Plus_Storage_Cost_Analysis_Paper.pdf",
    "Knowledge_Paper_Energy_Transition.pdf": "https://www.teriin.org/sites/default/files/2025-05/Knowledge-Paper-Energy-Transition.pdf",
    "Solar_Potential.pdf": "https://teriin.org/sites/default/files/files/Solar_Potential.pdf",
    "Indias_Nuclear_Energy.pdf": "https://www.teriin.org/files/Indias-Nuclear-Energy.pdf",
    "CSP_Report_2024.pdf": "https://teriin.org/sites/default/files/2024-02/CSP_Report_2024_New.pdf",
    "2026. India’s PV Manufacturing and Its Strategic Inflection Points (1).pdf": "https://teriindia-my.sharepoint.com/:b:/g/personal/d_saji_teri_res_in/IQDDCK43DYGQTYBXMWiga-yuAdsI3SwmT4nQoOOkKkyg5-Y?e=Vl7dHH",
}

STOP_WORDS = {
    "about", "according", "after", "all", "also", "and", "are", "ask", "based", "between", "can", "could", "did", "does", "doesn", "for", "from", "give", "has", "have", "how", "into", "its", "library", "me", "more", "most", "only", "our", "report", "reports", "said", "say", "says", "should", "summarize", "tell", "teri", "that", "the", "their", "them", "there", "these", "they", "this", "those", "through", "under", "was", "what", "when", "where", "which", "who", "why", "with", "would", "you", "your", "recommend", "recommendations", "overall", "summary", "summaries", "full", "compare", "comparison", "versus", "vs", "between", "chapter", "section", "topic", "audience",
}


def db_connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def init_db() -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with DB_LOCK, db_connect() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS reports (
                id TEXT PRIMARY KEY, filename TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
                authors TEXT NOT NULL DEFAULT '', published TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '', pages INTEGER NOT NULL DEFAULT 0,
                warnings TEXT NOT NULL DEFAULT '[]', source_url TEXT NOT NULL DEFAULT '',
                indexed_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sections (
                report_id TEXT NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
                name TEXT NOT NULL, first_page INTEGER NOT NULL,
                PRIMARY KEY(report_id, name, first_page)
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(
                report_id UNINDEXED, page UNINDEXED, section UNINDEXED, content,
                tokenize='unicode61 remove_diacritics 2'
            );
            CREATE INDEX IF NOT EXISTS report_title_idx ON reports(title);
        """)
    # Import any source PDFs not indexed yet; existing originals are never changed.
    with db_connect() as con:
        indexed = {r[0] for r in con.execute("SELECT filename FROM reports")}
    for path in sorted(REPORTS_DIR.glob("*.pdf")):
        if path.name not in indexed:
            try:
                ingest_pdf(path)
            except Exception as exc:
                print(f"Could not index {path.name}: {exc}", flush=True)


def clean_text(value: str) -> str:
    value = value.replace("\x00", " ").replace("\u00ad", "")
    value = re.sub(r"[\t\f\v ]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def likely_heading(line: str) -> bool:
    s = line.strip(" \t•|.-")
    if not (3 <= len(s) <= 110) or len(s.split()) > 14:
        return False
    if re.match(r"^(chapter\s+\d+|\d+(?:\.\d+)*\s+[A-Z]|appendix\s+[A-Z0-9]|annexure\s*[-I0-9]+)", s, re.I):
        return True
    letters = [c for c in s if c.isalpha()]
    if len(letters) >= 5 and sum(c.isupper() for c in letters) / len(letters) > .78:
        return True
    return s.lower() in {"executive summary", "conclusion", "conclusions", "recommendations", "way forward", "preface", "introduction", "acknowledgements", "methodology", "key findings"}


def pages_text(pdf_path: Path):
    reader = PdfReader(str(pdf_path), strict=False)
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as exc:
            raise ValueError("The PDF is encrypted and could not be opened.") from exc
    page_data = []
    warnings = []
    active_section = "Report text"
    section_starts = set()
    for number, page in enumerate(reader.pages, 1):
        try:
            raw = page.extract_text(extraction_mode="layout") or page.extract_text() or ""
        except Exception:
            raw = ""
        text = clean_text(raw)
        if len(text) < 35:
            warnings.append(number)
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for line in lines[:8]:
            if likely_heading(line):
                active_section = line[:110]
                section_starts.add((active_section, number))
                break
        paras = [clean_text(p) for p in re.split(r"\n\s*\n", text) if len(clean_text(p)) > 35]
        if not paras and text:
            paras = [text]
        chunks = []
        for para in paras:
            # Preserve readable paragraph boundaries while keeping each search passage compact.
            for start in range(0, len(para), 1250):
                chunk = para[start:start + 1250].strip()
                if len(chunk) > 25:
                    chunks.append((number, active_section, chunk))
        page_data.append((number, text, active_section, chunks))
    return reader, page_data, warnings, sorted(section_starts, key=lambda x: x[1])


def publication_date(meta, first_text: str) -> str:
    if meta:
        raw = str(meta.get("/CreationDate", ""))
        match = re.search(r"D:(\d{4})(\d{2})?(\d{2})?", raw)
        if match and match.group(1) not in {"1900", "2000"}:
            return match.group(1)
    match = re.search(r"\b(19\d{2}|20[0-3]\d)\b", first_text[:1800])
    return match.group(1) if match else ""


def display_title(meta, filename: str, first_text: str) -> str:
    raw = str(meta.get("/Title", "")).strip() if meta else ""
    generic_meta = {"untitled", "document", "discussion paper", "research report", "report", "paper"}
    bad_meta = (raw.lower().strip(" .") in generic_meta or len(raw) < 22 or
                raw.lower().endswith((".indd", ".pdf")) or
                "the energy and resources institute" in raw.lower() or len(raw) > 160)
    stem = Path(filename).stem
    stem = re.sub(r"\s*\(\d+\)$", "", stem)
    stem = re.sub(r"^\d{4}[._ -]+", "", stem)
    stem = re.sub(r"^r_[a-z0-9]+_", "", stem, flags=re.I)
    filename_title = re.sub(r"[_]+", " ", stem).strip()
    if filename_title and not re.search(r"^[a-z0-9]{8,}$", filename_title, re.I):
        # Humanize acronyms while retaining the source filename's original words.
        filename_title = re.sub(r"\bPv\b", "PV", filename_title, flags=re.I)
        filename_title = re.sub(r"\bCsp\b", "CSP", filename_title, flags=re.I)
        filename_title = re.sub(r"\bSmr\b", "SMR", filename_title, flags=re.I)
        if bad_meta:
            return filename_title[:220]
    if raw and not bad_meta:
        return raw[:220]
    lines = [clean_text(x).strip(" .") for x in first_text.splitlines() if clean_text(x)]
    file_terms = set(re.findall(r"[a-z]{3,}", Path(filename).stem.lower())) - {"report", "paper", "teri", "india"}
    candidates = []
    for start in range(min(14, len(lines))):
        joined = " ".join(lines[start:start + 3]).strip()
        if not 18 <= len(joined) <= 190:
            continue
        lowered = joined.lower()
        if any(x in lowered for x in ["the energy and resources institute", "copyright", "isbn", "www.", "telephone", "email:", "all rights reserved", "table of contents"]):
            continue
        overlap = sum(1 for term in file_terms if term in set(re.findall(r"[a-z]{3,}", lowered)))
        letters = sum(c.isalpha() for c in joined)
        score = overlap * 6 + min(len(joined), 120) / 30 + min(len(joined.split()), 12) / 8
        if joined.isupper():
            score += 1
        if letters < 15:
            score -= 5
        candidates.append((score, joined))
    if candidates:
        cover_title = max(candidates, key=lambda item: item[0])[1]
        if raw and len(raw) < 50 and raw.lower() not in generic_meta and cover_title:
            return cover_title[:220]
        return cover_title[:220]
    if raw and raw.lower().strip(" .") not in generic_meta:
        return raw[:220]
    return filename_title or re.sub(r"[_]+", " ", Path(filename).stem).strip()


def extractive_summary(page_data):
    candidates = []
    for number, text, section, _ in page_data:
        if not text:
            continue
        area = f"{section} {text[:1400]}".lower()
        weight = (12 if "executive summary" in section.lower() else 0)
        weight += (10 if any(x in section.lower() for x in ["key findings", "summary of findings"]) else 0)
        weight += (8 if any(x in section.lower() for x in ["conclusion", "recommendation", "way forward"]) else 0)
        weight += (2 if any(x in area for x in ["executive summary", "summary of findings", "key findings"]) else 0)
        weight += (1 if number <= 5 else 0)
        sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", re.sub(r"\s+", " ", text[:2200]))
        for sent in sentences:
            sent = sent.strip(" -•\n")
            if 65 <= len(sent) <= 250:
                candidates.append((weight, number, sent))
    # Prefer distinct sentences from the report's summary/conclusion areas, then the opening.
    candidates.sort(key=lambda item: (-item[0], item[1]))
    chosen, normalized = [], set()
    for _, page, sentence in candidates:
        key = re.sub(r"\W", "", sentence.lower())[:100]
        if key in normalized:
            continue
        normalized.add(key)
        chosen.append(f"{sentence} (p. {page})")
        if len(chosen) == 2:
            break
    return " ".join(chosen) if chosen else "Text could not be extracted from this report. Open the PDF to read it."


def ingest_pdf(path: Path) -> dict:
    reader, page_data, low_text_pages, section_starts = pages_text(path)
    all_text = "\n".join(item[1] for item in page_data)
    metadata = reader.metadata
    report_id = hashlib.sha256(path.name.encode("utf-8") + str(path.stat().st_size).encode()).hexdigest()[:18]
    override = METADATA_OVERRIDES.get(report_id, {})
    title = override.get("title") or display_title(metadata, path.name, all_text[:3000])
    authors = override.get("authors", str(metadata.get("/Author", "")).strip()[:200] if metadata else "")
    published = publication_date(metadata, all_text[:3000])
    summary = extractive_summary(page_data)
    # Flag sparse extraction without claiming the original PDF itself is incomplete.
    warnings = []
    if low_text_pages:
        nums = ", ".join(map(str, low_text_pages[:16]))
        suffix = " …" if len(low_text_pages) > 16 else ""
        warnings.append(f"Little or no selectable text was extracted from page(s) {nums}{suffix}. Scanned pages may need OCR; diagrams and complex tables may be incomplete.")
    source_url = PUBLIC_URLS.get(path.name, "")
    with DB_LOCK, db_connect() as con:
        old = con.execute("SELECT id FROM reports WHERE filename=?", (path.name,)).fetchone()
        if old:
            con.execute("DELETE FROM chunks WHERE report_id=?", (old["id"],))
            con.execute("DELETE FROM sections WHERE report_id=?", (old["id"],))
            con.execute("DELETE FROM reports WHERE id=?", (old["id"],))
        con.execute("INSERT INTO reports VALUES(?,?,?,?,?,?,?,?,?,?)", (
            report_id, path.name, title, authors, published, summary,
            len(page_data), json.dumps(warnings, ensure_ascii=False), source_url,
            datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        ))
        con.executemany("INSERT INTO sections VALUES(?,?,?)", [(report_id, name, page) for name, page in section_starts])
        con.executemany("INSERT INTO chunks(report_id,page,section,content) VALUES(?,?,?,?)", [
            (report_id, page, section, content) for _, _, _, chunks in page_data for page, section, content in chunks
        ])
    return {"id": report_id, "title": title, "pages": len(page_data), "warnings": warnings, "summary": summary}


def public_report(row):
    report = dict(row)
    report["warnings"] = json.loads(report.get("warnings") or "[]")
    report["original_url"] = report.get("source_url") or f"/api/reports/{report['id']}/file"
    report.pop("source_url", None)
    return report


def json_response(handler, value, status=200):
    body = json.dumps(value, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def tokens(text: str):
    words = re.findall(r"[\w’-]{3,}", text.lower(), flags=re.UNICODE)
    output = []
    for word in words:
        word = word.strip("’-")
        if word and word not in STOP_WORDS and word not in output:
            output.append(word)
    return output[:14]


class Handler(BaseHTTPRequestHandler):
    server_version = "TERIReportsLibrary/1.0"

    def log_message(self, fmt, *args):
        if os.environ.get("TERI_ACCESS_LOG", "") == "1":
            super().log_message(fmt, *args)

    def send_bytes(self, body: bytes, mime: str, status=200, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("X-Frame-Options", "DENY")
        if extra:
            for key, value in extra.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length > 1_000_000:
            raise ValueError("Request body is too large.")
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        params = urllib.parse.parse_qs(parsed.query)
        if path in {"/", "/index.html"}:
            return self.serve_static("index.html", "text/html; charset=utf-8")
        if path == "/styles.css":
            return self.serve_static("styles.css", "text/css; charset=utf-8")
        if path == "/app.js":
            return self.serve_static("app.js", "text/javascript; charset=utf-8")
        if path == "/api/reports":
            query = (params.get("q") or [""])[0].strip()
            with db_connect() as con:
                if query:
                    like = f"%{query}%"
                    matched_ids = []
                    query_terms = tokens(query)
                    if query_terms:
                        try:
                            quoted = ['"' + term.replace('"', '""') + '"' for term in query_terms]
                            found = con.execute("SELECT report_id FROM chunks WHERE chunks MATCH ? ORDER BY bm25(chunks,0,0,0,1.0) LIMIT 1000", (" AND ".join(quoted),)).fetchall()
                            if not found:
                                found = con.execute("SELECT report_id FROM chunks WHERE chunks MATCH ? ORDER BY bm25(chunks,0,0,0,1.0) LIMIT 1000", (" OR ".join(quoted),)).fetchall()
                            matched_ids = list(dict.fromkeys(r[0] for r in found))[:80]
                        except sqlite3.OperationalError:
                            matched_ids = []
                    lexical = con.execute("SELECT id FROM reports WHERE title LIKE ? OR authors LIKE ? OR summary LIKE ?", (like, like, like)).fetchall()
                    ordered_ids = list(dict.fromkeys(matched_ids + [r[0] for r in lexical]))
                    if ordered_ids:
                        placeholders = ",".join("?" for _ in ordered_ids)
                        rows = con.execute(f"SELECT * FROM reports WHERE id IN ({placeholders})", ordered_ids).fetchall()
                        row_map = {r["id"]: r for r in rows}
                        rows = [row_map[report_id] for report_id in ordered_ids if report_id in row_map]
                    else:
                        rows = []
                else:
                    rows = con.execute("SELECT * FROM reports ORDER BY published DESC, title COLLATE NOCASE").fetchall()
            return json_response(self, {"reports": [public_report(r) for r in rows], "count": len(rows)})
        if path.startswith("/api/reports/") and path.endswith("/file"):
            report_id = path.split("/")[3]
            with db_connect() as con:
                row = con.execute("SELECT filename,title FROM reports WHERE id=?", (report_id,)).fetchone()
            if not row:
                return json_response(self, {"error": "Report not found."}, 404)
            filename = row["filename"]
            file_path = (REPORTS_DIR / filename).resolve()
            if not file_path.is_relative_to(REPORTS_DIR.resolve()) or not file_path.is_file():
                return json_response(self, {"error": "Report file is unavailable."}, 404)
            safe_name = re.sub(r"[^A-Za-z0-9._ -]", "_", filename)
            return self.send_bytes(file_path.read_bytes(), "application/pdf", extra={"Content-Disposition": f'inline; filename="{safe_name}"', "Accept-Ranges": "bytes"})
        if path.startswith("/api/reports/"):
            report_id = path.rsplit("/", 1)[-1]
            with db_connect() as con:
                row = con.execute("SELECT * FROM reports WHERE id=?", (report_id,)).fetchone()
                if not row:
                    return json_response(self, {"error": "Report not found."}, 404)
                sections = con.execute("SELECT name,MIN(first_page) AS first_page FROM sections WHERE report_id=? GROUP BY name ORDER BY first_page", (report_id,)).fetchall()
            data = public_report(row)
            data["sections"] = [dict(s) for s in sections]
            return json_response(self, data)
        return json_response(self, {"error": "Not found."}, 404)

    def do_POST(self):
        if self.path == "/api/ask":
            try:
                payload = self.read_json()
            except Exception as exc:
                return json_response(self, {"error": str(exc)}, 400)
            question = str(payload.get("question", "")).strip()[:1200]
            if not question:
                return json_response(self, {"error": "Enter a question about the reports."}, 400)
            return json_response(self, self.answer(question))
        if self.path == "/api/admin/check":
            provided = self.headers.get("X-Admin-Token", "")
            return json_response(self, {"authorized": bool(ADMIN_TOKEN) and secrets.compare_digest(provided, ADMIN_TOKEN)})
        if self.path == "/api/admin/upload":
            provided = self.headers.get("X-Admin-Token", "")
            if not ADMIN_TOKEN or not secrets.compare_digest(provided, ADMIN_TOKEN):
                return json_response(self, {"error": "Admin authentication failed."}, 401)
            try:
                return json_response(self, self.upload_report())
            except ValueError as exc:
                return json_response(self, {"error": str(exc)}, 400)
            except Exception as exc:
                return json_response(self, {"error": f"Could not add this report: {exc}"}, 500)
        return json_response(self, {"error": "Not found."}, 404)

    def upload_report(self):
        ctype = self.headers.get("Content-Type", "")
        boundary_match = re.search(r"boundary=(?:\"([^\"]+)\"|([^;]+))", ctype)
        if not ctype.startswith("multipart/form-data") or not boundary_match:
            raise ValueError("Select a PDF file to upload.")
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > MAX_UPLOAD:
            raise ValueError("PDF must be smaller than 80 MB.")
        raw = self.rfile.read(length)
        mime = BytesParser(policy=policy.default).parsebytes(b"Content-Type: " + ctype.encode() + b"\r\nMIME-Version: 1.0\r\n\r\n" + raw)
        file_part = next((part for part in mime.iter_parts() if part.get_param("name", header="content-disposition") == "report"), None)
        if not file_part:
            raise ValueError("Select a PDF file to upload.")
        filename = Path((file_part.get_filename() or "report.pdf").replace("\\", "/")).name
        if not filename.lower().endswith(".pdf"):
            raise ValueError("Only PDF reports are accepted.")
        payload = file_part.get_payload(decode=True) or b""
        if not payload.startswith(b"%PDF-"):
            raise ValueError("The selected file is not a valid PDF.")
        if len(payload) > MAX_UPLOAD:
            raise ValueError("PDF must be smaller than 80 MB.")
        dest = REPORTS_DIR / filename
        if dest.exists():
            stem, suffix = Path(filename).stem, Path(filename).suffix
            serial = 2
            while dest.exists():
                dest = REPORTS_DIR / f"{stem} ({serial}){suffix}"
                serial += 1
        dest.write_bytes(payload)
        try:
            indexed = ingest_pdf(dest)
        except Exception:
            dest.unlink(missing_ok=True)
            raise ValueError("This PDF could not be extracted. It may be encrypted or damaged.")
        return {"ok": True, "report": indexed, "filename": dest.name}

    def answer(self, question):
        query_tokens = tokens(question)
        if not query_tokens:
            return {"answer": "Please include a report title or topic so I can search the available documents.", "evidence": [], "mode": "search"}
        quoted_tokens = ['"' + t.replace('"', '""') + '"' for t in query_tokens]
        match_expression = " AND ".join(quoted_tokens)
        qlower = question.lower()
        wants_full = any(x in qlower for x in ["summarize", "summary of", "full report", "overall summary"])
        broad = any(word in qlower for word in ["all", "every", "each", "overall", "compare", "comparison", "versus", "recommend", "recommendation"])
        with db_connect() as con:
            reports = con.execute("SELECT * FROM reports ORDER BY title COLLATE NOCASE").fetchall()
            if wants_full:
                candidates = [r for r in reports if any(t in r["title"].lower() for t in query_tokens if len(t) > 3)]
                if candidates:
                    target = max(candidates, key=lambda r: sum(t in r["title"].lower() for t in query_tokens if len(t) > 3))
                    section_rows = con.execute("SELECT name,MIN(first_page) AS first_page FROM sections WHERE report_id=? GROUP BY name ORDER BY first_page", (target["id"],)).fetchall()
                    chunk_rows = con.execute("SELECT page,section,content FROM chunks WHERE report_id=? ORDER BY CAST(page AS INT),rowid", (target["id"],)).fetchall()
                    # Section coverage is built from the indexed text of the entire report.
                    grouped = {}
                    for row in chunk_rows:
                        grouped.setdefault(row["section"], []).append(row)
                    answer_sections = []
                    for section, items in grouped.items():
                        chosen = next((x for x in items if len(x["content"]) > 110), items[0])
                        snippet = re.sub(r"\s+", " ", chosen["content"]).strip()
                        if len(snippet) > 560:
                            snippet = snippet[:557].rsplit(" ", 1)[0] + "…"
                        answer_sections.append({"section": section, "page": chosen["page"], "text": snippet, "report_id": target["id"], "title": target["title"], "file_url": f"/api/reports/{target['id']}/file#page={chosen['page']}"})
                    if answer_sections:
                        return {"answer": f"Here is an extractive, section-by-section overview of **{target['title']}**. It uses text extracted across all {target['pages']} pages; each excerpt links to its PDF page. This local assistant does not paraphrase beyond the text it can extract.", "evidence": answer_sections, "mode": "full_summary", "warnings": json.loads(target["warnings"] or "[]")}
            try:
                limit = 2000 if broad else 100
                rows = con.execute("""
                    SELECT c.report_id,c.page,c.section,c.content,r.title,r.pages,
                           bm25(chunks,0,0,0,1.0) AS rank
                    FROM chunks c JOIN reports r ON r.id=c.report_id
                    WHERE chunks MATCH ? ORDER BY rank LIMIT ?
                """, (match_expression, limit)).fetchall()
                if not rows and len(quoted_tokens) > 1:
                    match_expression = " OR ".join(quoted_tokens)
                    rows = con.execute("""
                        SELECT c.report_id,c.page,c.section,c.content,r.title,r.pages,
                               bm25(chunks,0,0,0,1.0) AS rank
                        FROM chunks c JOIN reports r ON r.id=c.report_id
                        WHERE chunks MATCH ? ORDER BY rank LIMIT ?
                    """, (match_expression, limit)).fetchall()
                    # Avoid treating one incidental word as evidence for a multi-topic question.
                    rows = [row for row in rows if sum(
                        bool(re.search(r"\b" + re.escape(term) + r"\w*\b", row["content"], re.I))
                        for term in query_tokens
                    ) >= 2]
            except sqlite3.OperationalError:
                rows = []
        if not rows:
            return {"answer": "I couldn’t find this in the TERI reports available here.", "evidence": [], "mode": "search"}
        per_report, evidence = {}, []
        for row in rows:
            if len(evidence) >= (180 if broad else 12):
                break
            seen = per_report.setdefault(row["report_id"], 0)
            if seen >= (10 if broad else 3):
                continue
            per_report[row["report_id"]] = seen + 1
            text = re.sub(r"\s+", " ", row["content"]).strip()
            if len(text) > 780:
                text = text[:777].rsplit(" ", 1)[0] + "…"
            evidence.append({"report_id": row["report_id"], "title": row["title"], "page": row["page"], "section": row["section"], "text": text, "file_url": f"/api/reports/{row['report_id']}/file#page={row['page']}"})
        report_count = len({x["report_id"] for x in evidence})
        intro = "The reports contain these passages relevant to your question."
        if broad:
            intro = f"I searched the indexed text across all reports. Here are relevant passages from {report_count} report(s); differences are shown as published."
        return {"answer": intro, "evidence": evidence, "mode": "search"}

    def serve_static(self, name, mime):
        path = STATIC / name
        try:
            return self.send_bytes(path.read_bytes(), mime)
        except FileNotFoundError:
            return json_response(self, {"error": "App asset is missing."}, 404)


def main():
    init_db()
    if not ADMIN_TOKEN:
        print("Admin uploads are disabled until TERI_ADMIN_TOKEN is set.", flush=True)
    print(f"TERI Reports Library ready at http://{HOST}:{PORT}", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
