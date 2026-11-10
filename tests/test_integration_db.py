"""실제 PostgreSQL(pgvector 포함)이 있을 때만 돌립니다.

    TEST_DATABASE_URL=postgresql://bai@127.0.0.1:5433/bai_test pytest tests/test_integration_db.py
"""
import os
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL 없음")

DOC = """감 사 원
{disp}
제 목 {title}
소 관 기 관 {agency}
조 치 기 관 {agency}
내 용
1. 업무 개요
{body}
조치할 사항 {agency}장은 업무를 철저히 하시기 바랍니다.({tag})"""


@pytest.fixture()
def conn(monkeypatch):
    import psycopg
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("EMBED_BACKEND", "hash")
    from bai_search import db, embed
    embed.get_embedder.cache_clear()
    schema = (Path(__file__).parent.parent / "db" / "schema.sql").read_text()
    with psycopg.connect(URL, autocommit=True) as c:
        c.execute("DROP TABLE IF EXISTS case_chunks, cases, documents CASCADE")
        c.execute(schema)
    from bai_search.cli import main
    raw = Path(os.environ.get("TMPDIR", "/tmp")) / "bai_it_raw"
    raw.mkdir(exist_ok=True)
    samples = [
        ("주 의 요 구", "물품 구매 수의계약 분할발주 부적정", "○○시",
         "○○시는 같은 물품을 여러 차례 나누어 특정업체와 수의계약을 체결하였다. " * 20, "주의"),
        ("징 계 요 구", "회계직원 공금 횡령", "○○공단",
         "회계담당자가 세입금을 개인 계좌로 이체하여 횡령하였다. " * 20, "징계"),
        ("통 보", "자살고위험군 정보연계 미흡", "△△청",
         "자살시도자의 정보가 자살예방센터에 제공되지 않았다. " * 20, "통보"),
    ]
    for i, (d, t, a, b, tag) in enumerate(samples):
        (raw / f"{i}.txt").write_text(DOC.format(disp=d, title=t, agency=a, body=b, tag=tag))
    main(["ingest", str(raw)])
    main(["embed"])
    with db.connect() as c:
        yield c


def test_ingest_and_embed(conn):
    n = conn.execute("SELECT count(*) FROM cases").fetchone()["count"]
    k = conn.execute("SELECT count(DISTINCT case_id) FROM case_chunks").fetchone()["count"]
    assert n == 3 and k == 3


def test_hybrid_search_finds_by_meaning_and_filter(conn):
    from bai_search.search import Filters, hybrid_search
    ids = hybrid_search(conn, "업체에 나눠서 계약 몰아준 사례")
    top = conn.execute("SELECT title FROM cases WHERE id=%s", (ids[0],)).fetchone()["title"]
    assert "분할발주" in top
    ids = hybrid_search(conn, "계약", Filters(disposition="징계요구"))
    titles = [conn.execute("SELECT title FROM cases WHERE id=%s", (i,)).fetchone()["title"] for i in ids]
    assert all("횡령" in t for t in titles)


def test_ask_end_to_end_with_fake_claude(conn):
    from bai_search.ask import ask

    captured = {}

    class FakeMessages:
        def create(self, **kw):
            captured.update(kw)
            return NS(stop_reason="end_turn", content=[
                NS(type="text", text="회계담당자가 세입금을 횡령했습니다",
                   citations=[NS(document_index=0, cited_text="세입금을 개인 계좌로 이체하여 횡령")]),
            ])

    client = NS(beta=NS(messages=FakeMessages()))
    ans = ask(conn, "공금 횡령 사례", client=client)
    assert "횡령" in captured["messages"][0]["content"][0]["title"]
    assert ans.sources[0].cited_text.startswith("세입금")


def test_api(conn):
    from fastapi.testclient import TestClient
    from bai_search.api import app

    c = TestClient(app)
    r = c.get("/api/search", params={"q": "공금 횡령"}).json()
    assert r["mode"] == "hybrid" and "횡령" in r["results"][0]["title"]
    first = r["results"][0]["id"]
    assert c.get(f"/api/cases/{first}").json()["action_text"].endswith("(징계)")
    assert c.post("/api/ask", json={"question": " "}).status_code == 400


def test_conditions_applied_and_relaxed(conn):
    from bai_search.search import search_with_conditions

    title = lambda i: conn.execute("SELECT title FROM cases WHERE id=%s", (i,)).fetchone()["title"]
    out = search_with_conditions(conn, "징계 받은 사례")
    assert out.conditions == ["처분: 징계요구"] and not out.relaxed
    assert [title(i) for i in out.ids] == ["회계직원 공금 횡령"]

    out = search_with_conditions(conn, "△△청 정보연계")
    assert out.conditions == ["기관: △△청"] and "정보연계" in title(out.ids[0])

    # 연도는 AI 분류 전이라 비어 있음 -> 0건 -> 조건 풀고 다시 검색
    out = search_with_conditions(conn, "2025년 공금 횡령")
    assert out.relaxed and "횡령" in title(out.ids[0])

    conn.execute("UPDATE cases SET audit_year = 2025, amount_krw = 300000000 WHERE title LIKE '%%횡령%%'")
    out = search_with_conditions(conn, "2025년 1억 원 이상 사례")
    assert not out.relaxed and [title(i) for i in out.ids] == ["회계직원 공금 횡령"]


def test_api_reports_conditions(conn):
    from fastapi.testclient import TestClient
    from bai_search.api import app

    r = TestClient(app).get("/api/search", params={"q": "고발된 횡령"}).json()
    assert r["conditions"] == ["처분: 고발"] and r["relaxed"] is True
    r = TestClient(app).get("/api/search", params={"q": "고발된 횡령", "auto": "false"}).json()
    assert r["conditions"] == [] and "횡령" in r["results"][0]["title"]
