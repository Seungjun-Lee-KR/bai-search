from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse

from . import db
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
    limit: int = Query(20, le=100),
    offset: int = 0,
):
    with db.connect() as conn:
        rows = db.search(conn, q=q, violation_type=violation_type, disposition=disposition,
                         year_from=year_from, year_to=year_to, agency=agency,
                         min_amount=min_amount, limit=limit, offset=offset)
    return {"results": rows}


@app.get("/api/cases/{case_id}")
def case(case_id: int):
    with db.connect() as conn:
        row = db.get_case(conn, case_id)
    if not row:
        raise HTTPException(404, "사건을 찾을 수 없습니다.")
    row.pop("embedding", None)
    return row
