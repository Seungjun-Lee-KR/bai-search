from __future__ import annotations

from pathlib import Path

from dataclasses import asdict

import os
import shutil
from datetime import date

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from . import db
from .search import Filters, search_with_conditions
from .taxonomy import DISPOSITIONS, VIOLATION_TYPES

app = FastAPI(title="감사원 결정례 검색")
INDEX_HTML = Path(__file__).with_name("index.html")


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


@app.get("/api/taxonomy")
def taxonomy():
    return {
        "violation_types": [
            {"code": v.code, "group": v.group, "label": v.label} for v in VIOLATION_TYPES
        ],
        "dispositions": list(DISPOSITIONS),
    }


@app.get("/api/search")
def search(
    q: str | None = None,
    violation_type: str | None = None,
    disposition: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    agency: str | None = None,
    min_amount: int | None = None,
    auto: bool = True,
    limit: int = Query(20, le=100),
    offset: int = 0,
):
    f = Filters(violation_type, disposition, year_from, year_to, agency, min_amount)
    with db.connect() as conn:
        if q:  # 검색어가 있으면 키워드 + 의미 혼합 검색
            out = search_with_conditions(conn, q, f, limit=offset + limit, auto=auto)
            return {"results": db.rows_by_ids(conn, out.ids[offset:]), "mode": "hybrid",
                    "conditions": out.conditions, "relaxed": out.relaxed}
        rows = db.search(conn, q=None, violation_type=violation_type, disposition=disposition,
                         year_from=year_from, year_to=year_to, agency=agency,
                         min_amount=min_amount, limit=limit, offset=offset)
    return {"results": rows, "mode": "list"}


class AskRequest(BaseModel):
    question: str
    violation_type: str | None = None
    disposition: str | None = None
    year_from: int | None = None
    year_to: int | None = None
    agency: str | None = None
    min_amount: int | None = None
    auto: bool = True


@app.post("/api/ask")
def ask_endpoint(req: AskRequest):
    from .ask import ask

    if not req.question.strip():
        raise HTTPException(400, "질문을 입력하세요.")
    f = Filters(req.violation_type, req.disposition, req.year_from, req.year_to,
                req.agency, req.min_amount)
    with db.connect() as conn:
        ans = ask(conn, req.question, f, auto=req.auto)
    return asdict(ans)


@app.get("/api/cases/{case_id}")
def case(case_id: int):
    with db.connect() as conn:
        row = db.get_case(conn, case_id)
        if not row:
            raise HTTPException(404, "사건을 찾을 수 없습니다.")
        row["related"] = db.related_cases(conn, case_id)
    row.pop("embedding", None)
    return row


DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
MAX_UPLOAD = 200 * 2**20


@app.post("/api/upload")
def upload(
    files: list[UploadFile] = File(...),
    published_on: str | None = Form(None),
    audit_name: str | None = Form(None),
    source_url: str | None = Form(None),
    classify: bool = Form(False),
):
    """웹 화면에서 올린 파일을 받은 파일 폴더에 저장하고 바로 처리합니다."""
    from .collect import (SUPPORTED, handle, meta_from_filename, nfc, override_meta,
                          parse_date, unique_path, write_log)

    pub = parse_date(published_on) if published_on else None
    if published_on and not pub:
        raise HTTPException(400, "공개일 형식을 알 수 없습니다. 예: 2026-05-12")
    inbox, processed, failed = DATA_DIR / "inbox", DATA_DIR / "processed", DATA_DIR / "failed"
    inbox.mkdir(parents=True, exist_ok=True)
    results = []
    with db.connect() as conn:
        for up in files:
            name = nfc(Path(up.filename or "upload").name)
            if Path(name).suffix.lower() not in SUPPORTED | {".zip"}:
                results.append({"file": name, "status": "failed",
                                "message": "PDF·HWP·HWPX·ZIP 파일만 올릴 수 있습니다."})
                continue
            dest = unique_path(inbox / name)
            with dest.open("wb") as f:
                shutil.copyfileobj(up.file, f, length=1 << 20)
            if dest.stat().st_size > MAX_UPLOAD:
                dest.unlink()
                results.append({"file": name, "status": "failed", "message": "200MB를 넘는 파일입니다."})
                continue
            meta_for = lambda t: override_meta(meta_from_filename(t.name), pub,
                                               audit_name or None, source_url or None)
            outcomes = handle(conn, dest, meta_for, processed=processed, failed=failed,
                              classify=classify)
            write_log(processed / "수집기록.csv", outcomes)
            results += [asdict(o) for o in outcomes]
    return {"results": results}


@app.get("/api/recent")
def recent(limit: int = Query(20, le=100)):
    """최근 저장된 문서 목록 (자료 추가 화면용)."""
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT d.id, d.title, d.published_on, d.audit_name, d.collected_at,
                      count(c.id) AS cases,
                      count(c.id) FILTER (WHERE c.summary IS NULL) AS unclassified,
                      count(c.id) FILTER (WHERE NOT EXISTS (
                          SELECT 1 FROM case_chunks k WHERE k.case_id = c.id)) AS unembedded
               FROM documents d LEFT JOIN cases c ON c.document_id = d.id
               GROUP BY d.id ORDER BY d.collected_at DESC, d.id DESC LIMIT %s""",
            (limit,),
        ).fetchall()
    return {"documents": rows}


@app.get("/api/stats")
def stats(year_from: int | None = None, year_to: int | None = None,
          agency_type: str | None = None, disposition: str | None = None):
    from .stats import collect_stats

    f = Filters(year_from=year_from, year_to=year_to, disposition=disposition)
    f.agency_type = agency_type
    with db.connect() as conn:
        return collect_stats(conn, f)


@app.get("/api/cases/{case_id}/similar")
def similar(case_id: int, limit: int = Query(5, le=20)):
    from .similar import find_similar

    with db.connect() as conn:
        if not conn.execute("SELECT 1 FROM cases WHERE id=%s", (case_id,)).fetchone():
            raise HTTPException(404, "사건을 찾을 수 없습니다.")
        return {"results": [asdict(s) for s in find_similar(conn, case_id, limit)]}
