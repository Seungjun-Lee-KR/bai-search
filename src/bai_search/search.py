"""키워드 검색과 의미 검색을 합친 혼합 검색 (Reciprocal Rank Fusion)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .embed import get_embedder, to_pgvector

RRF_K = 60
CANDIDATES = 50

# 질문에 흔히 붙는 말. 키워드 검색에서 뺍니다.
_STOP = {"사례", "경우", "관련", "있는", "어떤", "알려줘", "찾아줘", "보여줘", "무엇", "있나요",
         "있어", "대한", "대해", "통해", "어떻게", "건", "등", "및"}
_JOSA = re.compile(r"(은|는|이|가|을|를|의|에|에서|으로|로|와|과|도|만|에게|한|된|하는|했던|인)$")


@dataclass
class Filters:
    violation_type: str | None = None
    disposition: str | None = None
    year_from: int | None = None
    year_to: int | None = None
    agency: str | None = None
    min_amount: int | None = None
    dispositions: list[str] = field(default_factory=list)   # 하나라도 해당하면
    agency_type: str | None = None

    def sql(self, alias: str = "c") -> tuple[str, list]:
        where, params = [], []
        if self.violation_type:
            where.append(f"%s = ANY({alias}.violation_types)")
            params.append(self.violation_type)
        if self.disposition:
            where.append(f"%s = ANY({alias}.dispositions)")
            params.append(self.disposition)
        if self.dispositions:
            where.append(f"{alias}.dispositions && %s::text[]")
            params.append(list(self.dispositions))
        if self.agency_type:
            where.append(f"{alias}.agency_type = %s")
            params.append(self.agency_type)
        if self.year_from:
            where.append(f"COALESCE({alias}.audit_year, EXTRACT(YEAR FROM {alias}.published_on)::int) >= %s")
            params.append(self.year_from)
        if self.year_to:
            where.append(f"COALESCE({alias}.audit_year, EXTRACT(YEAR FROM {alias}.published_on)::int) <= %s")
            params.append(self.year_to)
        if self.agency:
            where.append(f"({alias}.agency ILIKE %s OR {alias}.action_agency ILIKE %s)")
            params += [f"%{self.agency}%"] * 2
        if self.min_amount:
            where.append(f"{alias}.amount_krw >= %s")
            params.append(self.min_amount)
        return (" AND ".join(where) or "TRUE"), params


def terms(q: str) -> list[str]:
    out = []
    for w in re.findall(r"[0-9A-Za-z가-힣]+", q):
        w = _JOSA.sub("", w) if len(w) > 2 else w
        if len(w) >= 2 and w not in _STOP:
            out.append(w)
    return list(dict.fromkeys(out))


def keyword_ranking(conn, q: str, f: Filters) -> list[int]:
    ts = terms(q)
    if not ts:
        return []
    fsql, fparams = f.sql()
    score = " + ".join(
        "(CASE WHEN c.title ILIKE %s THEN 3 ELSE 0 END + CASE WHEN c.body ILIKE %s THEN 1 ELSE 0 END)"
        for _ in ts
    )
    sparams = [p for t in ts for p in (f"%{t}%", f"%{t}%")]
    rows = conn.execute(
        f"""SELECT c.id, ({score}) AS s FROM cases c
            WHERE {fsql} AND ({score}) > 0 ORDER BY s DESC, c.id DESC LIMIT %s""",
        sparams + fparams + sparams + [CANDIDATES],
    ).fetchall()
    return [r["id"] for r in rows]


def vector_ranking(conn, q: str, f: Filters) -> list[int]:
    qv = to_pgvector(get_embedder().embed([q], "query")[0])
    fsql, fparams = f.sql()
    rows = conn.execute(
        f"""SELECT case_id, min(dist) AS d FROM (
                SELECT k.case_id, k.embedding <=> %s::vector AS dist
                FROM case_chunks k JOIN cases c ON c.id = k.case_id
                WHERE {fsql}
                ORDER BY dist LIMIT %s) t
            GROUP BY case_id ORDER BY d LIMIT %s""",
        [qv] + fparams + [CANDIDATES * 4, CANDIDATES],
    ).fetchall()
    return [r["case_id"] for r in rows]


def rrf(*rankings: list[int]) -> list[int]:
    score: dict[int, float] = {}
    for ranking in rankings:
        for rank, cid in enumerate(ranking):
            score[cid] = score.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
    return sorted(score, key=lambda c: -score[c])


def hybrid_search(conn, q: str, f: Filters | None = None, limit: int = 20,
                  use_vector: bool = True) -> list[int]:
    f = f or Filters()
    if not terms(q):  # 조건만 있는 질문(예: "2025년 징계 사례"): 조건에 맞는 목록
        fsql, fparams = f.sql()
        rows = conn.execute(
            f"SELECT c.id FROM cases c WHERE {fsql} ORDER BY COALESCE(c.audit_year, EXTRACT(YEAR FROM c.published_on)::int) DESC NULLS LAST, c.id DESC LIMIT %s",
            fparams + [limit],
        ).fetchall()
        return [r["id"] for r in rows]
    kw = keyword_ranking(conn, q, f)
    vec = vector_ranking(conn, q, f) if use_vector and has_embeddings(conn) else []
    return rrf(kw, vec)[:limit]


def has_embeddings(conn) -> bool:
    return conn.execute("SELECT EXISTS (SELECT 1 FROM case_chunks)").fetchone()["exists"]


def best_chunks(conn, q: str, case_ids: list[int], per_case: int = 3) -> dict[int, list[str]]:
    """사건별로 질문과 가장 가까운 조각들."""
    if not case_ids or not has_embeddings(conn):
        return {}
    qv = to_pgvector(get_embedder().embed([q], "query")[0])
    rows = conn.execute(
        """SELECT case_id, text FROM (
               SELECT case_id, text, row_number() OVER (
                   PARTITION BY case_id ORDER BY embedding <=> %s::vector) AS rn
               FROM case_chunks WHERE case_id = ANY(%s)) t
           WHERE rn <= %s ORDER BY case_id, rn""",
        (qv, case_ids, per_case),
    ).fetchall()
    out: dict[int, list[str]] = {}
    for r in rows:
        out.setdefault(r["case_id"], []).append(r["text"])
    return out


@dataclass
class SearchOutcome:
    ids: list[int]
    conditions: list[str]      # 질문에서 뽑아 적용한 조건 (화면 표시용)
    relaxed: bool = False      # 조건 때문에 0건이라 조건 없이 다시 찾았는지


def search_with_conditions(conn, q: str, explicit: Filters | None = None, limit: int = 20,
                           auto: bool = True) -> SearchOutcome:
    """질문에서 조건을 뽑아 적용해 검색하고, 0건이면 자동 조건을 풀고 다시 찾습니다."""
    from .conditions import extract, known_agencies, merge

    explicit = explicit or Filters()
    if not auto:
        return SearchOutcome(hybrid_search(conn, q, explicit, limit), [])
    ex = extract(q, known_agencies(conn))
    if not ex.any():
        return SearchOutcome(hybrid_search(conn, q, explicit, limit), [])
    ids = hybrid_search(conn, ex.query, merge(explicit, ex.filters), limit)
    if ids:
        return SearchOutcome(ids, ex.labels)
    return SearchOutcome(hybrid_search(conn, q, explicit, limit), ex.labels, relaxed=True)
