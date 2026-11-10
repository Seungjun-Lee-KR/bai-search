from __future__ import annotations

import os
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row


def dsn() -> str:
    return os.environ.get("DATABASE_URL", "postgresql://bai:bai@localhost:5432/bai")


@contextmanager
def connect():
    with psycopg.connect(dsn(), row_factory=dict_row) as conn:
        yield conn


def insert_document(conn, *, source: str, title: str, full_text: str,
                    file_path: str | None = None, source_url: str | None = None,
                    published_on=None) -> int:
    row = conn.execute(
        """INSERT INTO documents (source, source_url, title, published_on, file_path, full_text)
           VALUES (%s, %s, %s, %s, %s, %s)
           ON CONFLICT (source_url) DO UPDATE SET full_text = EXCLUDED.full_text
           RETURNING id""",
        (source, source_url or file_path, title, published_on, file_path, full_text),
    ).fetchone()
    return row["id"]


def insert_case(conn, *, document_id: int, title: str, body: str) -> int:
    row = conn.execute(
        "INSERT INTO cases (document_id, title, body) VALUES (%s, %s, %s) RETURNING id",
        (document_id, title, body),
    ).fetchone()
    return row["id"]


def pending_cases(conn, limit: int):
    return conn.execute(
        "SELECT id, title, body FROM cases WHERE summary IS NULL ORDER BY id LIMIT %s",
        (limit,),
    ).fetchall()


def save_classification(conn, case_id: int, c) -> None:
    conn.execute(
        """UPDATE cases SET agency=%s, agency_type=%s, audit_year=%s, violation_types=%s,
               dispositions=%s, amount_krw=%s, contract_method=%s, vendor_related=%s,
               related_laws=%s, summary=%s, confidence=%s, needs_review=%s
           WHERE id=%s""",
        (c.agency, c.agency_type, c.audit_year, list(c.violation_types), list(c.dispositions),
         c.amount_krw, c.contract_method, c.vendor_related, c.related_laws, c.summary,
         c.confidence, c.confidence < 0.7, case_id),
    )


def search(conn, *, q: str | None, violation_type: str | None, disposition: str | None,
           year_from: int | None, year_to: int | None, agency: str | None,
           min_amount: int | None, limit: int = 20, offset: int = 0):
    where, params = [], []
    if q:
        where.append("(title ILIKE %s OR body ILIKE %s OR summary ILIKE %s)")
        like = f"%{q}%"
        params += [like, like, like]
    if violation_type:
        where.append("%s = ANY(violation_types)")
        params.append(violation_type)
    if disposition:
        where.append("%s = ANY(dispositions)")
        params.append(disposition)
    if year_from:
        where.append("audit_year >= %s")
        params.append(year_from)
    if year_to:
        where.append("audit_year <= %s")
        params.append(year_to)
    if agency:
        where.append("agency ILIKE %s")
        params.append(f"%{agency}%")
    if min_amount:
        where.append("amount_krw >= %s")
        params.append(min_amount)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    order = "similarity(title || ' ' || coalesce(summary,''), %s) DESC," if q else ""
    order_params = [q] if q else []
    sql = f"""SELECT id, title, agency, audit_year, violation_types, dispositions,
                     amount_krw, summary, needs_review
              FROM cases {clause}
              ORDER BY {order} audit_year DESC NULLS LAST, id DESC
              LIMIT %s OFFSET %s"""
    return conn.execute(sql, params + order_params + [limit, offset]).fetchall()


def get_case(conn, case_id: int):
    return conn.execute(
        """SELECT c.*, d.title AS document_title, d.source_url, d.source
           FROM cases c JOIN documents d ON d.id = c.document_id WHERE c.id = %s""",
        (case_id,),
    ).fetchone()
