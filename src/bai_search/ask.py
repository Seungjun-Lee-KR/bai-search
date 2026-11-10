"""자연어 질문 → 혼합 검색 → Claude가 원문을 인용하며 답변 (RAG).

Claude의 인용(citations) 기능을 써서, 답변의 각 문장이 어느 사건 원문의
어느 부분에 근거했는지 함께 돌려줍니다. 근거 없는 문장은 쓰지 않도록 지시합니다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from .classify import redact
from .search import Filters, best_chunks, hybrid_search

DEFAULT_MODEL = "claude-opus-5-5"
TOP_CASES = 6
FULL_BODY_LIMIT = 15_000  # 이보다 긴 사건은 질문과 가까운 조각만 보냄

SYSTEM_PROMPT = """당신은 감사원 감사관을 돕는 감사 사례 검색 보조자입니다.
제공된 감사원 처분요구서 문서들만 근거로 질문에 답합니다.

규칙
- 모든 사실 문장은 문서에서 인용해 근거를 답니다. 문서에 없는 내용은 쓰지 않습니다.
- 질문에 맞는 사례가 문서에 없으면 "제공된 사례에서는 찾지 못했습니다"라고 답하고, 가장 가까운 사례가 있으면 왜 다른지 한 줄로 설명합니다.
- 사례마다 무엇이 문제였는지, 금액이 있으면 금액, 어떤 처분이 내려졌는지를 짧게 정리합니다.
- 감사 실무에 쓸 수 있도록, 사례들에 공통된 점검 착안사항이 보이면 마지막에 2~3줄로 덧붙입니다. 이 부분은 문서 인용이 아닌 정리임을 밝힙니다.
- 공개본에서 지워진 업체명·인명(빈칸, A, B)은 추측하지 않습니다.
- 한국어로, 짧은 단락과 목록으로 답합니다.
"""


@dataclass
class Source:
    n: int
    case_id: int
    title: str
    cited_text: str


@dataclass
class Answer:
    parts: list[dict] = field(default_factory=list)   # {"text": str, "refs": [n, ...]}
    sources: list[Source] = field(default_factory=list)
    cases: list[dict] = field(default_factory=list)
    refused: bool = False


def _case_document(case: dict, chunks: list[str] | None) -> dict:
    head = [
        f"사건번호: #{case['id']}",
        f"제목: {case['title']}",
        f"조치기관: {case.get('action_agency') or case.get('agency') or '-'}",
        f"처분: {', '.join(case.get('dispositions') or []) or '-'}",
    ]
    if case.get("amount_krw"):
        head.append(f"금액(AI 추출): {case['amount_krw']:,}원")
    if chunks and len(case["body"]) > FULL_BODY_LIMIT:
        body = "\n...\n".join(c.split("\n", 1)[-1] for c in chunks)
    else:
        body = case["body"]
    text = "\n".join(head) + "\n\n" + body
    if case.get("action_text"):
        text += "\n\n조치할 사항: " + case["action_text"]
    return {
        "type": "document",
        "source": {"type": "text", "media_type": "text/plain", "data": redact(text)},
        "title": f"#{case['id']} {case['title']}",
        "citations": {"enabled": True},
    }


def retrieve(conn, question: str, filters: Filters | None = None, k: int = TOP_CASES) -> list[dict]:
    ids = hybrid_search(conn, question, filters, limit=k)
    if not ids:
        return []
    rows = conn.execute(
        """SELECT id, title, body, agency, action_agency, dispositions, amount_krw,
                  action_text, audit_year, violation_types, summary
           FROM cases WHERE id = ANY(%s)""",
        (ids,),
    ).fetchall()
    by_id = {r["id"]: r for r in rows}
    return [by_id[i] for i in ids if i in by_id]


def build_request(question: str, cases: list[dict], chunks: dict[int, list[str]], model: str) -> dict:
    content = [_case_document(c, chunks.get(c["id"])) for c in cases]
    content.append({"type": "text", "text": f"질문: {question}"})
    return dict(
        model=model,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}],
        output_config={"effort": "medium"},
        # 안전 분류기가 거절하면 서버에서 다른 모델로 다시 시도
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )


def parse_response(response, cases: list[dict]) -> Answer:
    ans = Answer(cases=[{k: c[k] for k in ("id", "title", "action_agency", "dispositions")}
                        for c in cases])
    if response.stop_reason == "refusal":
        ans.refused = True
        ans.parts.append({"text": "이 질문에는 답변이 거절되었습니다. 질문을 바꿔 다시 시도해 주세요.",
                          "refs": []})
        return ans
    seen: dict[tuple[int, str], int] = {}
    for block in response.content:
        if block.type != "text":
            continue
        refs = []
        for cit in block.citations or []:
            idx = getattr(cit, "document_index", None)
            if idx is None or idx >= len(cases):
                continue
            key = (idx, cit.cited_text)
            if key not in seen:
                seen[key] = len(ans.sources) + 1
                ans.sources.append(Source(seen[key], cases[idx]["id"], cases[idx]["title"],
                                          cit.cited_text.strip()))
            refs.append(seen[key])
        ans.parts.append({"text": block.text, "refs": refs})
    return ans


def ask(conn, question: str, filters: Filters | None = None, client=None,
        model: str | None = None) -> Answer:
    cases = retrieve(conn, question, filters)
    if not cases:
        return Answer(parts=[{"text": "검색된 사례가 없습니다. 검색어나 조건을 바꿔 보세요.", "refs": []}])
    chunks = best_chunks(conn, question, [c["id"] for c in cases if len(c["body"]) > FULL_BODY_LIMIT])
    if client is None:
        import anthropic
        client = anthropic.Anthropic()
    model = model or os.environ.get("ASK_MODEL", DEFAULT_MODEL)
    response = client.beta.messages.create(**build_request(question, cases, chunks, model))
    return parse_response(response, cases)
