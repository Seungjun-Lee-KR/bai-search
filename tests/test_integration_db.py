"""실제 PostgreSQL(pgvector 포함)이 있을 때만 돌립니다.

    TEST_DATABASE_URL=postgresql://bai@127.0.0.1:5433/bai_test pytest tests/test_integration_db.py
"""
import os
import time
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


def _old(path):
    t = time.time() - 60
    os.utime(path, (t, t))


def test_watch_pass_dedup_failure_zip_and_year(conn, tmp_path):
    import zipfile
    from bai_search.collect import run_pass
    from bai_search.search import search_with_conditions

    inbox = tmp_path / "inbox"
    inbox.mkdir()
    body = "회계직원이 보조금을 가로챘다. " * 30
    doc = DOC.format(disp="고 발", title="보조금 횡령 고발", agency="□□군", body=body, tag="고발")
    (inbox / "a.txt").write_text(doc)
    (inbox / "a사본.txt").write_text(doc)                       # 같은 내용 -> 중복
    (inbox / "빈문서.txt").write_text("짧음")                     # 실패
    (inbox / "목록.csv").write_text("파일명,공개일,감사명\na.txt,2023-03-15,군 보조금 감사\n",
                                    encoding="utf-8-sig")
    with zipfile.ZipFile(inbox / "묶음.zip", "w") as zf:
        zf.writestr("b.txt", DOC.format(disp="주 의 요 구", title="물품 검수 부실", agency="□□군",
                                        body="납품되지 않은 물품을 검수 처리하였다. " * 30, tag="주의"))
    (inbox / "받는중.pdf.crdownload").write_text("x")
    for p in inbox.iterdir():
        _old(p)

    out = run_pass(conn, inbox, processed=tmp_path / "processed", failed=tmp_path / "failed")
    status = {o.file: o.status for o in out}
    assert status["a.txt"] == "saved" and status["a사본.txt"] == "duplicate"
    assert status["빈문서.txt"] == "failed" and status["묶음__b.txt"] == "saved"
    assert sorted(p.name for p in inbox.iterdir()) == ["목록.csv", "받는중.pdf.crdownload"]
    assert (tmp_path / "failed" / "빈문서.txt.error.txt").exists()
    log = (tmp_path / "processed" / "수집기록.csv").read_text(encoding="utf-8-sig")
    assert log.startswith("처리시각") and "중복" in log

    saved = next(o for o in out if o.file == "a.txt")
    d = conn.execute("SELECT published_on, audit_name FROM documents WHERE id=%s",
                     (saved.document_id,)).fetchone()
    assert str(d["published_on"]) == "2023-03-15" and d["audit_name"] == "군 보조금 감사"
    k = conn.execute("""SELECT count(*) FROM case_chunks k JOIN cases c ON c.id=k.case_id
                        WHERE c.document_id=%s""", (saved.document_id,)).fetchone()["count"]
    assert k > 0

    # AI 분류 전이어도 공개일 연도로 연도 조건이 걸림
    o = search_with_conditions(conn, "2023년 보조금")
    assert not o.relaxed and conn.execute("SELECT title FROM cases WHERE id=%s", (o.ids[0],)
                                          ).fetchone()["title"] == "보조금 횡령 고발"


def test_upload_api(conn, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from bai_search import api

    monkeypatch.setattr(api, "DATA_DIR", tmp_path)
    c = TestClient(api.app)
    doc = DOC.format(disp="시 정 요 구", title="출장비 허위 청구", agency="◇◇공사",
                     body="출장을 가지 않고 여비를 청구하였다. " * 30, tag="시정")
    r = c.post("/api/upload", files=[("files", ("출장.txt", doc.encode(), "text/plain")),
                                     ("files", ("x.exe", b"MZ", "application/octet-stream"))],
               data={"published_on": "2024-07-01"}).json()["results"]
    assert [x["status"] for x in r] == ["saved", "failed"] and r[0]["published_on"] == "2024-07-01"
    r = c.post("/api/upload", files=[("files", ("다시.txt", doc.encode(), "text/plain"))]).json()["results"]
    assert r[0]["status"] == "duplicate"
    assert c.post("/api/upload", files=[("files", ("y.txt", doc.encode(), "text/plain"))],
                  data={"published_on": "어제"}).status_code == 400
    docs = c.get("/api/recent").json()["documents"]
    assert docs[0]["title"] == "출장비 허위 청구" and docs[0]["unembedded"] == 0



def test_duplicate_fills_missing_meta(conn, tmp_path):
    from bai_search.collect import run_pass

    inbox = tmp_path / "inbox"
    inbox.mkdir()
    doc = DOC.format(disp="주 의 요 구", title="검수 부실", agency="◎◎시",
                     body="미납품 물품을 검수 처리하였다. " * 30, tag="주의")
    (inbox / "first.txt").write_text(doc)
    _old(inbox / "first.txt")
    run_pass(conn, inbox, processed=tmp_path / "p", failed=tmp_path / "f")
    (inbox / "second.txt").write_text(doc)
    (inbox / "목록.csv").write_text("파일명,공개일,감사명\nsecond.txt,2022-02-02,◎◎시 기관운영감사\n",
                                    encoding="utf-8")
    _old(inbox / "second.txt")
    out = run_pass(conn, inbox, processed=tmp_path / "p", failed=tmp_path / "f")
    assert out[0].status == "duplicate" and "공개일·감사명" in out[0].message
    d = conn.execute("SELECT published_on, audit_name FROM documents WHERE id=%s",
                     (out[0].document_id,)).fetchone()
    assert str(d["published_on"]) == "2022-02-02" and d["audit_name"] == "◎◎시 기관운영감사"
    c = conn.execute("SELECT published_on FROM cases WHERE document_id=%s", (out[0].document_id,)).fetchone()
    assert str(c["published_on"]) == "2022-02-02"


def test_stats(conn):
    from bai_search.stats import collect_stats
    from bai_search.search import Filters

    conn.execute("""UPDATE cases SET violation_types = ARRAY['EMBEZZLE_FUNDS','FALSE_CLAIM'],
                        amount_krw = 300000000, audit_year = 2024, agency_type = '공공기관',
                        summary = '요약', vendor_related = false
                    WHERE title LIKE '%%횡령%%'""")
    conn.execute("""UPDATE cases SET violation_types = ARRAY['SPLIT_CONTRACT'], amount_krw = 50000000,
                        audit_year = 2022, agency_type = '지방자치단체', summary = '요약', vendor_related = true
                    WHERE title LIKE '%%분할발주%%'""")
    st = collect_stats(conn)
    k = st["kpi"]
    assert (k["cases"], k["classified"], k["amount"], k["heavy"], k["vendor"]) == (3, 2, 350_000_000, 1, 1)
    assert {r["code"]: r["cases"] for r in st["by_type"]} == {
        "EMBEZZLE_FUNDS": 1, "FALSE_CLAIM": 1, "SPLIT_CONTRACT": 1}
    years = {y["year"]: y for y in st["by_year"]}
    assert sorted(years) == [2022, 2023, 2024]          # 빈 해(2023)도 0으로 채움
    assert years[2024]["횡령·유용"] == 1 and years[2022]["계약 유착"] == 1 and years[2023]["total"] == 0
    assert [d["disposition"] for d in st["by_disposition"]] == ["징계요구", "주의요구", "통보"]
    assert st["top_amount"][0]["amount_krw"] == 300_000_000
    assert {(m["code"], m["disposition"]) for m in st["matrix"]} == {
        ("EMBEZZLE_FUNDS", "징계요구"), ("FALSE_CLAIM", "징계요구"), ("SPLIT_CONTRACT", "주의요구")}

    f = Filters(year_from=2024)
    f.agency_type = "공공기관"
    assert collect_stats(conn, f)["kpi"]["cases"] == 1


def test_stats_api(conn):
    from fastapi.testclient import TestClient
    from bai_search.api import app

    r = TestClient(app).get("/api/stats", params={"agency_type": "지방자치단체"}).json()
    assert r["kpi"]["cases"] == 0 and r["by_year"] == []
    assert TestClient(app).get("/api/stats").json()["kpi"]["cases"] == 3


def _add(conn, tmp_path, name, disp, title, agency, body, tag):
    from bai_search.collect import FileMeta, process_file
    p = tmp_path / name
    p.write_text(DOC.format(disp=disp, title=title, agency=agency, body=body, tag=tag))
    return process_file(conn, p, FileMeta())


def test_similar_cases(conn, tmp_path):
    from bai_search.similar import find_similar

    embezzle = "회계담당자가 세입금을 개인 계좌로 이체하여 횡령하였다. " * 20
    a = _add(conn, tmp_path, "a.txt", "변 상 판 정", "출납직원 보관금 횡령", "◇◇군",
             "출납직원이 보관금을 개인 계좌로 이체하여 횡령하였다. " * 20, "변상")
    # 같은 지적을 다른 기관에 처분한 건(제목 같음) -> 추천에서 빠져야 함
    _add(conn, tmp_path, "b.txt", "징 계 요 구", "회계직원 공금 횡령", "○○재단", embezzle + "추가 ", "징계")
    src = conn.execute("SELECT id FROM cases WHERE title='회계직원 공금 횡령' AND action_agency='○○공단'"
                       ).fetchone()["id"]
    conn.execute("UPDATE cases SET violation_types = ARRAY['EMBEZZLE_FUNDS'] WHERE title LIKE '%%횡령%%'")

    res = find_similar(conn, src)
    titles = [r.title for r in res]
    assert titles[0] == "출납직원 보관금 횡령"
    assert "회계직원 공금 횡령" not in titles
    top = res[0]
    assert top.text_similarity and any(r.startswith("같은 위반유형: 공금 횡령") for r in top.reasons)
    assert top.dispositions == ["변상판정"]
    assert res[0].score >= res[-1].score

    # 임베딩이 없으면 핵심어로 대신
    conn.execute("DELETE FROM case_chunks")
    res = find_similar(conn, src)
    assert res and res[0].title == "출납직원 보관금 횡령" and res[0].text_similarity is not None
    assert res[0].reasons[0].startswith("핵심어 유사도")


def test_similar_api(conn):
    from fastapi.testclient import TestClient
    from bai_search.api import app

    c = TestClient(app)
    any_id = conn.execute("SELECT min(id) AS i FROM cases").fetchone()["i"]
    r = c.get(f"/api/cases/{any_id}/similar").json()["results"]
    assert all(x["id"] != any_id for x in r) and all("reasons" in x for x in r)
    assert c.get("/api/cases/999999/similar").status_code == 404
