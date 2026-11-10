from __future__ import annotations

import os
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row


# 연도 조건: AI가 뽑은 감사 연도가 없으면 공개일의 연도
YEAR_SQL = "COALESCE(audit_year, EXTRACT(YEAR FROM published_on)::int)"


def dsn() -> str:
    return os.environ.get("DATABASE_URL", "postgresql://bai:bai@localhost:5432/bai")


@contextmanager
def connect():
    with psycopg.connect(dsn(), row_factory=dict_row) as conn:
        yield conn


def insert_document(conn, *, source: str, title: str, full_text: str,
                    file_path: str | None = None, source_url: str | None = None,
                    published_on=None, sha256: str | None = None,
                    audit_name: str | None = None) -> int:
    row = conn.execute(
        """INSERT INTO documents (source, source_url, title, published_on, file_path, full_text,
                                  sha256, audit_name)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (source, source_url, title, published_on, file_path, full_text, sha256, audit_name),
    ).fetchone()
    return row["id"]


def document_by_sha(conn, sha256: str):
    return conn.execute("SELECT id, title FROM documents WHERE sha256 = %s", (sha256,)).fetchone()


def fill_missing_meta(conn, document_id: int, published_on, audit_name, source_url) -> list[str]:
    """중복 파일에 기존 문서에 없는 공개일·감사명·주소가 있으면 채웁니다. 채운 항목 이름을 돌려줌."""
    cur = conn.execute("SELECT published_on, audit_name, source_url FROM documents WHERE id=%s",
                       (document_id,)).fetchone()
    filled = [label for label, old, new in (("공개일", cur["published_on"], published_on),
                                            ("감사명", cur["audit_name"], audit_name),
                                            ("주소", cur["source_url"], source_url))
              if old is None and new]
    if filled:
        conn.execute(
            """UPDATE documents SET published_on = COALESCE(published_on, %s),
                   audit_name = COALESCE(audit_name, %s), source_url = COALESCE(source_url, %s)
               WHERE id = %s""", (published_on, audit_name, source_url, document_id))
        conn.execute("UPDATE cases SET published_on = COALESCE(published_on, %s) WHERE document_id = %s",
                     (published_on, document_id))
    return filled


def insert_case(conn, *, document_id: int, seg, published_on=None) -> int:
    row = conn.execute(
        """INSERT INTO cases (document_id, title, body, dispositions, agency,
                              responsible_agency, action_agency, action_text, published_on)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (document_id, seg.title, seg.body, seg.dispositions,
         seg.action_agency or seg.responsible_agency,
         seg.responsible_agency, seg.action_agency, seg.action_text, published_on),
    ).fetchone()
    return row["id"]


def pending_cases(conn, limit: int):
    return conn.execute(
        """SELECT id, title, body, dispositions, responsible_agency, action_agency, action_text
           FROM cases WHERE summary IS NULL ORDER BY id LIMIT %s""",
        (limit,),
    ).fetchall()


def save_classification(conn, case_id: int, c) -> None:
    conn.execute(
        # 양식에서 읽은 기관·처분이 있으면 AI 추정값으로 덮어쓰지 않음
        """UPDATE cases SET agency=COALESCE(agency, %s), agency_type=%s, audit_year=%s,
               violation_types=%s,
               dispositions=CASE WHEN cardinality(dispositions) > 0 THEN dispositions ELSE %s END,
               amount_krw=%s, contract_method=%s, vendor_related=%s,
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
        where.append(f"{YEAR_SQL} >= %s")
        params.append(year_from)
    if year_to:
        where.append(f"{YEAR_SQL} <= %s")
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
    sql = f"""SELECT id, title, agency, action_agency, audit_year, violation_types, dispositions,
                     amount_krw, summary, needs_review, published_on
              FROM cases {clause}
              ORDER BY {order} {YEAR_SQL} DESC NULLS LAST, id DESC
              LIMIT %s OFFSET %s"""
    return conn.execute(sql, params + order_params + [limit, offset]).fetchall()


def get_case(conn, case_id: int):
    return conn.execute(
        """SELECT c.*, d.title AS document_title, d.source_url, d.source
           FROM cases c JOIN documents d ON d.id = c.document_id WHERE c.id = %s""",
        (case_id,),
    ).fetchone()


def related_cases(conn, case_id: int):
    """같은 지적을 여러 기관에 처분한 경우(제목 동일)를 묶어 보여 줍니다."""
    return conn.execute(
        """SELECT c2.id, c2.title, c2.action_agency, c2.dispositions
           FROM cases c1 JOIN cases c2 ON c2.title = c1.title AND c2.id <> c1.id
           WHERE c1.id = %s ORDER BY c2.id""",
        (case_id,),
    ).fetchall()


LIST_COLUMNS = """id, title, agency, action_agency, audit_year, violation_types, dispositions,
                  amount_krw, summary, needs_review, published_on"""


def rows_by_ids(conn, ids: list[int]):
    """ids 순서를 지켜 목록용 행을 돌려줍니다."""
    if not ids:
        return []
    rows = conn.execute(f"SELECT {LIST_COLUMNS} FROM cases WHERE id = ANY(%s)", (ids,)).fetchall()
    by_id = {r["id"]: r for r in rows}
    return [by_id[i] for i in ids if i in by_id]


def cases_to_embed(conn, limit: int | None):
    return conn.execute(
        """SELECT id, title, body, action_text FROM cases c
           WHERE NOT EXISTS (SELECT 1 FROM case_chunks k WHERE k.case_id = c.id)
           ORDER BY id LIMIT %s""",
        (limit,),
    ).fetchall()


def save_chunks(conn, case_id: int, texts: list[str], vectors: list[str], model: str) -> None:
    conn.execute("DELETE FROM case_chunks WHERE case_id = %s", (case_id,))
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO case_chunks (case_id, chunk_no, text, embedding, embed_model)
               VALUES (%s, %s, %s, %s::vector, %s)""",
            [(case_id, i, t, v, model) for i, (t, v) in enumerate(zip(texts, vectors))],
        )
