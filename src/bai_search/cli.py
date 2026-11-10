"""명령줄 도구.

  bai ingest data/raw --source audit_report   # 폴더 안 PDF/HWP/HWPX를 읽어 DB에 저장
  bai classify --limit 50                     # 아직 분류 안 된 사건을 Claude로 분류
  bai embed                                   # 의미 검색용 임베딩 생성
  bai ask "수의계약을 쪼개서 특정업체에 준 사례"   # 자연어 질문
  bai serve                                   # 검색 웹 서버 실행 (http://localhost:8000)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

SUPPORTED = {".pdf", ".hwp", ".hwpx", ".txt"}


def cmd_ingest(args) -> None:
    from . import db
    from .parsers import ParseError, extract_text
    from .segment import split_cases

    files = sorted(p for p in Path(args.folder).rglob("*") if p.suffix.lower() in SUPPORTED)
    if not files:
        sys.exit(f"{args.folder} 에 PDF/HWP/HWPX 파일이 없습니다.")
    with db.connect() as conn:
        for path in files:
            try:
                text = extract_text(path)
            except ParseError as e:
                print(f"[건너뜀] {path.name}: {e}")
                continue
            segments = split_cases(text)
            title = segments[0].title if len(segments) == 1 else path.stem
            doc_id = db.insert_document(conn, source=args.source, title=title,
                                        full_text=text, file_path=str(path))
            conn.execute("DELETE FROM cases WHERE document_id = %s", (doc_id,))
            for seg in segments:
                db.insert_case(conn, document_id=doc_id, seg=seg)
            conn.commit()
            print(f"[저장] {path.name}: 사건 {len(segments)}건")


def cmd_classify(args) -> None:
    from . import db
    from .classify import ClassificationRefused, classify_case, known_fields

    with db.connect() as conn:
        rows = db.pending_cases(conn, args.limit)
        for row in rows:
            try:
                result = classify_case(row["title"], row["body"], model=args.model,
                                       known=known_fields(row))
            except (ClassificationRefused, ValueError) as e:
                print(f"[검수 필요] #{row['id']} {row['title'][:40]}: {e}")
                continue
            db.save_classification(conn, row["id"], result)
            conn.commit()
            print(f"[분류] #{row['id']} {row['title'][:40]} -> {', '.join(result.violation_types)}")
    print(f"완료: {len(rows)}건 처리")


def cmd_embed(args) -> None:
    from . import db
    from .embed import chunk_case, get_embedder, to_pgvector

    emb = get_embedder()
    with db.connect() as conn:
        if args.all:
            conn.execute("DELETE FROM case_chunks")
        rows = db.cases_to_embed(conn, args.limit)
        for row in rows:
            texts = chunk_case(row["title"], row["body"], row["action_text"])
            vectors = [to_pgvector(v) for v in emb.embed(texts, "document")]
            db.save_chunks(conn, row["id"], texts, vectors, emb.name)
            conn.commit()
            print(f"[임베딩] #{row['id']} {row['title'][:40]}: 조각 {len(texts)}개")
    print(f"완료: {len(rows)}건 ({emb.name})")


def cmd_ask(args) -> None:
    from . import db
    from .ask import ask

    with db.connect() as conn:
        ans = ask(conn, args.question, model=args.model)
    for part in ans.parts:
        refs = "".join(f"[{n}]" for n in part["refs"])
        print(part["text"] + refs, end="")
    print("\n\n근거")
    for s in ans.sources:
        print(f"[{s.n}] #{s.case_id} {s.title}\n    \"{s.cited_text}\"")


def cmd_serve(args) -> None:
    import uvicorn

    uvicorn.run("bai_search.api:app", host=args.host, port=args.port, reload=False)


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    p = argparse.ArgumentParser(prog="bai", description="감사원 결정례 검색 도구")
    sub = p.add_subparsers(required=True)

    s = sub.add_parser("ingest", help="문서 폴더를 읽어 DB에 저장")
    s.add_argument("folder")
    s.add_argument("--source", default="audit_report",
                   choices=["audit_report", "review_decision", "compensation", "other"])
    s.set_defaults(func=cmd_ingest)

    s = sub.add_parser("classify", help="미분류 사건을 Claude로 분류")
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--model", default=None, help="기본값은 환경변수 CLASSIFY_MODEL")
    s.set_defaults(func=cmd_classify)

    s = sub.add_parser("embed", help="의미 검색용 임베딩 생성")
    s.add_argument("--limit", type=int, default=None)
    s.add_argument("--all", action="store_true", help="기존 임베딩을 지우고 전부 다시 생성")
    s.set_defaults(func=cmd_embed)

    s = sub.add_parser("ask", help="자연어로 질문하고 원문 인용과 함께 답 받기")
    s.add_argument("question")
    s.add_argument("--model", default=None, help="기본값은 환경변수 ASK_MODEL")
    s.set_defaults(func=cmd_ask)

    s = sub.add_parser("serve", help="검색 웹 서버 실행")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(func=cmd_serve)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
