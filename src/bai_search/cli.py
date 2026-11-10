"""명령줄 도구.

  bai watch                                   # data/inbox에 넣은 파일을 자동 처리 (반자동 수집)
  bai ingest data/raw                         # 폴더 안 PDF/HWP/HWPX를 한 번에 저장
  bai classify --limit 50                     # 아직 분류 안 된 사건을 Claude로 분류
  bai embed                                   # 의미 검색용 임베딩 생성
  bai ask "수의계약을 쪼개서 특정업체에 준 사례"   # 자연어 질문
  bai similar 12                              # 사건 #12와 비슷한 사례 추천
  bai serve                                   # 검색 웹 서버 실행 (http://localhost:8000)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

def _print_outcome(o) -> None:
    label = {"saved": "저장", "duplicate": "중복", "failed": "실패"}[o.status]
    extra = f" 사건 {o.cases}건" if o.cases else ""
    date = f" (공개일 {o.published_on})" if o.published_on else ""
    print(f"[{label}] {o.file}{extra}{date} {o.message}".rstrip())
    for w in o.warnings:
        print(f"    ! {w}")


def cmd_ingest(args) -> None:
    """폴더 안 파일을 그대로 둔 채 저장 (옮기지 않음)."""
    from . import db
    from .collect import SUPPORTED, load_manifest, process_file, resolve_meta

    folder = Path(args.folder)
    files = sorted(p for p in folder.rglob("*") if p.suffix.lower() in SUPPORTED)
    if not files:
        sys.exit(f"{args.folder} 에 PDF/HWP/HWPX 파일이 없습니다.")
    manifest = load_manifest(folder)
    with db.connect() as conn:
        for path in files:
            meta = resolve_meta(path, manifest)
            if args.source:
                meta.source = args.source
            _print_outcome(process_file(conn, path, meta, embed=not args.no_embed,
                                        classify=args.classify, force=args.force))


def cmd_watch(args) -> None:
    import time

    from . import db
    from .collect import run_pass

    inbox, root = Path(args.inbox), Path(args.inbox).parent
    processed, failed = root / "processed", root / "failed"
    print(f"받은 파일 폴더: {inbox.resolve()}")
    print("  PDF·HWP·HWPX·ZIP 파일을 넣으면 자동으로 처리합니다. 공개일·감사명은 목록.csv 또는 파일명에서 읽습니다.")
    if not args.once:
        print(f"  {args.interval}초마다 확인합니다. 끝내려면 Ctrl+C")
    try:
        while True:
            with db.connect() as conn:
                outcomes = run_pass(conn, inbox, processed=processed, failed=failed,
                                    embed=not args.no_embed, classify=args.classify)
            for o in outcomes:
                _print_outcome(o)
            if outcomes:
                n = {k: sum(o.status == k for o in outcomes) for k in ("saved", "duplicate", "failed")}
                print(f"-- 저장 {n['saved']} · 중복 {n['duplicate']} · 실패 {n['failed']}  (기록: {processed / '수집기록.csv'})")
            if args.once:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n중지했습니다.")


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
        ans = ask(conn, args.question, model=args.model, auto=not args.no_auto)
    if ans.conditions:
        print(("[조건에 맞는 사례가 없어 조건 없이 검색] " if ans.relaxed else "[적용 조건] ")
              + " · ".join(ans.conditions) + "\n")
    for part in ans.parts:
        refs = "".join(f"[{n}]" for n in part["refs"])
        print(part["text"] + refs, end="")
    print("\n\n근거")
    for s in ans.sources:
        print(f"[{s.n}] #{s.case_id} {s.title}\n    \"{s.cited_text}\"")


def cmd_similar(args) -> None:
    from . import db
    from .similar import find_similar

    with db.connect() as conn:
        src = conn.execute("SELECT title FROM cases WHERE id=%s", (args.case_id,)).fetchone()
        if not src:
            sys.exit(f"사건 #{args.case_id}이 없습니다.")
        print(f"#{args.case_id} {src['title']} 과 비슷한 사례\n")
        for s in find_similar(conn, args.case_id, args.limit):
            money = f" · {s.amount_krw:,}원" if s.amount_krw else ""
            print(f"#{s.id} {s.title} ({s.agency or '-'} · {', '.join(s.dispositions) or '-'}{money})")
            print(f"    점수 {s.score:.2f} — {'; '.join(s.reasons)}")


def cmd_serve(args) -> None:
    import uvicorn

    uvicorn.run("bai_search.api:app", host=args.host, port=args.port, reload=False)


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    p = argparse.ArgumentParser(prog="bai", description="감사원 결정례 검색 도구")
    sub = p.add_subparsers(required=True)

    s = sub.add_parser("ingest", help="문서 폴더를 읽어 DB에 저장 (파일은 그대로 둠)")
    s.add_argument("folder")
    s.add_argument("--source", default=None,
                   choices=["audit_report", "review_decision", "compensation", "other"])
    s.add_argument("--force", action="store_true", help="이미 저장된 같은 파일도 다시 처리")
    s.add_argument("--no-embed", action="store_true", help="임베딩은 나중에 bai embed로")
    s.add_argument("--classify", action="store_true", help="저장 직후 AI 분류까지 (API 비용 발생)")
    s.set_defaults(func=cmd_ingest)

    s = sub.add_parser("watch", help="받은 파일 폴더를 지켜보며 새 파일을 자동 처리")
    s.add_argument("inbox", nargs="?", default="data/inbox")
    s.add_argument("--interval", type=int, default=30, help="확인 간격(초)")
    s.add_argument("--once", action="store_true", help="한 번만 처리하고 끝냄 (작업 스케줄러용)")
    s.add_argument("--no-embed", action="store_true")
    s.add_argument("--classify", action="store_true", help="저장 직후 AI 분류까지 (API 비용 발생)")
    s.set_defaults(func=cmd_watch)

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
    s.add_argument("--no-auto", action="store_true", help="질문에서 조건을 자동으로 뽑지 않음")
    s.set_defaults(func=cmd_ask)

    s = sub.add_parser("similar", help="사건과 비슷한 사례 추천")
    s.add_argument("case_id", type=int)
    s.add_argument("--limit", type=int, default=5)
    s.set_defaults(func=cmd_similar)

    s = sub.add_parser("serve", help="검색 웹 서버 실행")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(func=cmd_serve)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
