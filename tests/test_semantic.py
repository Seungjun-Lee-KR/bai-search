import math
from types import SimpleNamespace as NS

import pytest

from bai_search.ask import build_request, parse_response
from bai_search.embed import DIM, HashEmbedder, chunk_case, to_pgvector
from bai_search.search import rrf, terms


def _cos(a, b):
    return sum(x * y for x, y in zip(a, b))


def test_hash_embedder_shape_and_similarity():
    e = HashEmbedder()
    a, b, c = e.embed(["수의계약 분할발주로 특정업체 계약", "수의계약을 분할하여 특정업체와 계약",
                       "자살예방센터 정보연계 미흡"], "document")
    assert len(a) == DIM and math.isclose(_cos(a, a), 1.0, rel_tol=1e-6)
    assert _cos(a, b) > _cos(a, c)


def test_to_pgvector_rejects_wrong_dim():
    with pytest.raises(ValueError):
        to_pgvector([0.1, 0.2])


def test_chunk_case_prefixes_title_and_limits_size():
    body = "\n".join(f"{i}번째 문단 " + "가" * 200 for i in range(10))
    chunks = chunk_case("공금 횡령", body, "주의를 촉구하시기 바랍니다.(주의)")
    assert all(c.startswith("제목: 공금 횡령\n") for c in chunks)
    assert chunks[-1].endswith("(주의)")
    assert max(len(c) for c in chunks[:-1]) < 1200


def test_terms_strip_josa_and_stopwords():
    assert terms("수의계약을 쪼개서 특정업체에 준 사례") == ["수의계약", "쪼개서", "특정업체"]


def test_rrf_prefers_items_in_both_lists():
    assert rrf([1, 2, 3], [3, 4])[0] == 3


CASES = [
    {"id": 7, "title": "전산장비 유지보수 계약 부적정", "body": "변경계약으로 650,232,586원을 추가 부담",
     "action_agency": "법원행정처", "dispositions": ["주의요구"], "amount_krw": None, "action_text": "주의"},
    {"id": 9, "title": "공탁금 횡령 미통보", "body": "횡령 사실을 감사원에 통보하지 않음",
     "action_agency": "법원행정처", "dispositions": ["주의요구"], "amount_krw": None, "action_text": None},
]


def test_build_request_uses_cited_documents_and_fallback():
    req = build_request("변경계약 사례", CASES, {}, "claude-opus-5-5")
    docs = req["messages"][0]["content"][:-1]
    assert [d["title"] for d in docs] == ["#7 전산장비 유지보수 계약 부적정", "#9 공탁금 횡령 미통보"]
    assert all(d["citations"] == {"enabled": True} for d in docs)
    assert req["fallbacks"] == "default"


def _cit(idx, text):
    return NS(type="char_location", document_index=idx, cited_text=text)


def test_parse_response_numbers_sources_once():
    resp = NS(stop_reason="end_turn", content=[
        NS(type="thinking", thinking=""),
        NS(type="text", text="법원행정처는 추가 비용을 부담했습니다", citations=[_cit(0, "650,232,586원을 추가 부담")]),
        NS(type="text", text=". 같은 근거: ", citations=None),
        NS(type="text", text="추가 부담", citations=[_cit(0, "650,232,586원을 추가 부담"), _cit(1, "횡령 사실")]),
    ])
    ans = parse_response(resp, CASES)
    assert [p["refs"] for p in ans.parts] == [[1], [], [1, 2]]
    assert [(s.n, s.case_id) for s in ans.sources] == [(1, 7), (2, 9)]


def test_parse_response_refusal():
    ans = parse_response(NS(stop_reason="refusal", content=[]), CASES)
    assert ans.refused and ans.parts


def test_explain_api_error_messages():
    import anthropic
    import httpx
    from bai_search.classify import explain_api_error

    assert "API 키가 없습니다" in explain_api_error(TypeError("Could not resolve authentication method"))
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    err = anthropic.AuthenticationError("bad", response=httpx.Response(401, request=req), body=None)
    assert "올바르지 않습니다" in explain_api_error(err)
    assert "연결하지 못했습니다" in explain_api_error(anthropic.APIConnectionError(request=req))
    assert explain_api_error(KeyError("x")) is None
