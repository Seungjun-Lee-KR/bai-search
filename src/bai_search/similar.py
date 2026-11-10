"""유사사례 추천.

점수 = 내용 유사도(임베딩) 0.6 + 위반유형 겹침 0.25 + 같은 처분 0.1 + 같은 기관유형 0.05
- 내용 유사도: 기준 사건 조각들의 평균 벡터와 다른 사건 조각 사이의 가장 가까운 거리(코사인)
- 임베딩이 없으면 제목·본문 핵심어 겹침으로 대신합니다.
- 같은 문서이거나 제목이 같은 사건(같은 지적을 다른 기관에 처분한 경우)은 '관련 처분'으로 따로
  보여 주므로 추천에서 뺍니다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .search import has_embeddings, terms
from .taxonomy import VIOLATION_BY_CODE

W_TEXT, W_TYPE, W_DISP, W_AGENCY = 0.6, 0.25, 0.1, 0.05
CANDIDATES = 60


@dataclass
class Similar:
    id: int
    title: str
    agency: str | None
    dispositions: list[str]
    violation_types: list[str]
    amount_krw: int | None
    published_on: str | None
    score: float
    text_similarity: float | None
    reasons: list[str] = field(default_factory=list)


def _jaccard(a: list, b: list) -> float:
    a, b = set(a or []), set(b or [])
    return len(a & b) / len(a | b) if a and b else 0.0


def _text_candidates(conn, case_id: int) -> dict[int, float]:
    """사건 id -> 내용 유사도(0~1)."""
    if not has_embeddings(conn):
        return {}
    rows = conn.execute(
        """WITH me AS (SELECT avg(embedding) AS v FROM case_chunks WHERE case_id = %(id)s),
                src AS (SELECT document_id, title FROM cases WHERE id = %(id)s),
                near AS (
                  SELECT k.case_id, k.embedding <=> (SELECT v FROM me) AS dist
                  FROM case_chunks k
                  WHERE (SELECT v FROM me) IS NOT NULL AND k.case_id <> %(id)s
                  ORDER BY dist LIMIT %(lim)s)
           SELECT n.case_id, min(n.dist) AS dist
           FROM near n JOIN cases c ON c.id = n.case_id, src
           WHERE c.document_id <> src.document_id AND c.title <> src.title
           GROUP BY n.case_id""",
        {"id": case_id, "lim": CANDIDATES * 6},
    ).fetchall()
    return {r["case_id"]: max(0.0, 1.0 - float(r["dist"])) for r in rows}


def _keyword_candidates(conn, src: dict) -> dict[int, float]:
    """임베딩이 없을 때: 제목 핵심어가 본문/제목에 나오는 비율."""
    ts = terms(src["title"])[:8]
    if not ts:
        return {}
    score = " + ".join("(CASE WHEN c.title ILIKE %s OR c.body ILIKE %s THEN 1 ELSE 0 END)" for _ in ts)
    params = [p for t in ts for p in (f"%{t}%", f"%{t}%")]
    rows = conn.execute(
        f"""SELECT c.id, ({score})::float / %s AS s FROM cases c
            WHERE c.id <> %s AND c.document_id <> %s AND c.title <> %s
            ORDER BY s DESC LIMIT %s""",
        params + [len(ts), src["id"], src["document_id"], src["title"], CANDIDATES],
    ).fetchall()
    return {r["id"]: float(r["s"]) for r in rows if r["s"] > 0}


def _structured_candidates(conn, src: dict) -> set[int]:
    """같은 위반유형을 가진 사건은 내용이 덜 비슷해도 후보에 넣음."""
    if not src["violation_types"]:
        return set()
    rows = conn.execute(
        """SELECT id FROM cases WHERE violation_types && %s::text[] AND id <> %s
               AND document_id <> %s AND title <> %s LIMIT %s""",
        (src["violation_types"], src["id"], src["document_id"], src["title"], CANDIDATES),
    ).fetchall()
    return {r["id"] for r in rows}


def find_similar(conn, case_id: int, limit: int = 5) -> list[Similar]:
    src = conn.execute(
        """SELECT id, document_id, title, violation_types, dispositions, agency_type
           FROM cases WHERE id = %s""", (case_id,)).fetchone()
    if not src:
        return []
    text = _text_candidates(conn, case_id)
    text_kind = "내용" if text else "핵심어"
    if not text:
        text = _keyword_candidates(conn, src)
    ids = set(text) | _structured_candidates(conn, src)
    if not ids:
        return []
    rows = conn.execute(
        """SELECT id, title, coalesce(action_agency, agency) AS agency, dispositions, violation_types,
                  agency_type, amount_krw, published_on
           FROM cases WHERE id = ANY(%s)""", (list(ids),)).fetchall()

    out: list[Similar] = []
    for r in rows:
        t = text.get(r["id"], 0.0)
        ty = _jaccard(src["violation_types"], r["violation_types"])
        same_disp = sorted(set(src["dispositions"] or []) & set(r["dispositions"] or []))
        same_agency = bool(src["agency_type"]) and src["agency_type"] == r["agency_type"]
        score = W_TEXT * t + W_TYPE * ty + W_DISP * bool(same_disp) + W_AGENCY * same_agency

        reasons = []
        if t:
            reasons.append(f"{text_kind} 유사도 {t:.0%}")
        shared = [c for c in (src["violation_types"] or []) if c in (r["violation_types"] or [])]
        if shared:
            reasons.append("같은 위반유형: " + ", ".join(
                VIOLATION_BY_CODE[c].label if c in VIOLATION_BY_CODE else c for c in shared))
        if same_disp:
            reasons.append("같은 처분: " + ", ".join(same_disp))
        if same_agency:
            reasons.append(f"같은 기관유형: {r['agency_type']}")
        out.append(Similar(
            id=r["id"], title=r["title"], agency=r["agency"], dispositions=r["dispositions"] or [],
            violation_types=r["violation_types"] or [], amount_krw=r["amount_krw"],
            published_on=str(r["published_on"]) if r["published_on"] else None,
            score=round(score, 4), text_similarity=round(t, 4) if t else None, reasons=reasons))
    out.sort(key=lambda s: (-s.score, s.id))
    return out[:limit]
