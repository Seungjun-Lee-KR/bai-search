"""반자동 수집: 감사관이 내려받은 파일을 받은 파일 폴더(inbox)에 넣으면
읽기 → 사건 분할 → 저장 → (임베딩) → (AI 분류)까지 처리하고 파일을 정리합니다.

    data/inbox/            <- 여기에 PDF·HWP·HWPX·ZIP을 넣음 (목록.csv 선택)
    data/processed/날짜/   <- 처리 끝난 원본
    data/failed/           <- 읽지 못한 파일과 사유(.error.txt)
    data/processed/수집기록.csv

공개일·감사명은 다음 순서로 찾습니다.
  1) inbox 안의 CSV(예: 목록.csv) — 열: 파일명, 공개일, 감사명, 주소, 구분
  2) 파일명 속 날짜 — 예: 2026-05-12_감사명.pdf, 20260512 감사명.hwp
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
import shutil
import time
import unicodedata
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

SUPPORTED = {".pdf", ".hwp", ".hwpx", ".txt"}
TEMP_SUFFIXES = {".crdownload", ".part", ".tmp", ".download", ".partial"}
SETTLE_SECONDS = 5          # 내려받는 중인 파일을 건드리지 않도록 마지막 수정 후 기다리는 시간
MAX_ZIP_MEMBER = 100 * 2**20

SOURCE_WORDS = {
    "심사": "review_decision", "재심의": "review_decision",
    "변상": "compensation", "감사결과": "audit_report", "감사보고서": "audit_report",
}


@dataclass
class FileMeta:
    published_on: date | None = None
    audit_name: str | None = None
    source_url: str | None = None
    source: str = "audit_report"


@dataclass
class Outcome:
    file: str
    status: str                 # saved | duplicate | failed
    cases: int = 0
    document_id: int | None = None
    message: str = ""
    warnings: list[str] = field(default_factory=list)
    published_on: date | None = None
    title: str = ""


def nfc(s: str) -> str:
    """macOS 한글 파일명(NFD)과 Windows/CSV(NFC)를 맞춥니다."""
    return unicodedata.normalize("NFC", s)


# ---------------------------------------------------------------- 메타데이터
_DATE_PATTERNS = [
    re.compile(r"(20\d{2})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})"),
    re.compile(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)"),
]


def parse_date(text: str | None) -> date | None:
    if not text:
        return None
    for pat in _DATE_PATTERNS:
        m = pat.search(text)
        if m:
            try:
                return date(int(m[1]), int(m[2]), int(m[3]))
            except ValueError:
                continue
    return None


def meta_from_filename(name: str) -> FileMeta:
    stem = nfc(Path(name).stem)
    meta = FileMeta(published_on=parse_date(stem))
    for word, src in SOURCE_WORDS.items():
        if word in stem:
            meta.source = src
            break
    rest = re.sub(r"(20\d{2})[-._년 ]?\d{1,2}[-._월 ]?\d{1,2}일?", " ", stem)
    rest = re.sub(r"[_\s]+", " ", rest).strip(" -_")
    if len(rest) >= 4 and re.search(r"[가-힣]{2,}", rest):
        meta.audit_name = rest
    return meta


_COLS = {
    "file": ("파일명", "파일", "file", "filename"),
    "date": ("공개일", "게시일", "공개일자", "등록일", "날짜", "date"),
    "name": ("감사명", "제목", "감사사항", "title"),
    "url": ("주소", "원문주소", "링크", "url", "URL"),
    "kind": ("구분", "종류", "type"),
}


def _read_csv_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp949"):   # 엑셀 한글 CSV는 보통 cp949
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"{path.name}: 글자 인코딩을 알 수 없습니다(UTF-8 또는 CP949로 저장하세요).")


def load_manifest(folder: Path) -> dict[str, FileMeta]:
    """폴더 안 CSV 목록을 읽어 {파일명: FileMeta}. 파일명은 확장자 있어도 없어도 맞춰 봄."""
    out: dict[str, FileMeta] = {}
    for path in sorted(folder.glob("*.csv")):
        rows = list(csv.DictReader(io.StringIO(_read_csv_text(path))))
        if not rows:
            continue
        header = {nfc(h).strip(): h for h in rows[0].keys() if h}
        col = {k: next((header[c] for c in names if c in header), None) for k, names in _COLS.items()}
        if not col["file"]:
            continue
        for r in rows:
            fname = nfc((r.get(col["file"]) or "").strip())
            if not fname:
                continue
            meta = FileMeta(
                published_on=parse_date(r.get(col["date"]) if col["date"] else None),
                audit_name=(r.get(col["name"]) or "").strip() or None if col["name"] else None,
                source_url=(r.get(col["url"]) or "").strip() or None if col["url"] else None,
            )
            kind = (r.get(col["kind"]) or "") if col["kind"] else ""
            for word, src in SOURCE_WORDS.items():
                if word in kind:
                    meta.source = src
            out[fname] = meta
            out[Path(fname).stem] = meta
    return out


def resolve_meta(path: Path, manifest: dict[str, FileMeta]) -> FileMeta:
    name = nfc(path.name)
    from_name = meta_from_filename(name)
    m = manifest.get(name) or manifest.get(Path(name).stem)
    if not m:
        return from_name
    return FileMeta(
        published_on=m.published_on or from_name.published_on,
        audit_name=m.audit_name or from_name.audit_name,
        source_url=m.source_url,
        source=m.source if m.source != "audit_report" else from_name.source,
    )


# ---------------------------------------------------------------- 파일 다루기
def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def is_ready(path: Path, now: float | None = None) -> bool:
    if path.name.startswith((".", "~$")) or path.suffix.lower() in TEMP_SUFFIXES:
        return False
    now = now or time.time()
    return now - path.stat().st_mtime >= SETTLE_SECONDS


def expand_zip(path: Path, dest: Path) -> list[Path]:
    """지원 형식만 꺼냅니다. 폴더 경로는 버리고 파일명만 써서 압축 경로 조작을 막습니다."""
    out = []
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            raw_name = info.filename
            if not (info.flag_bits & 0x800):  # UTF-8 표시가 없으면 한국어 윈도우 압축(cp949)
                try:
                    raw_name = raw_name.encode("cp437").decode("cp949")
                except (UnicodeEncodeError, UnicodeDecodeError):
                    pass
            name = nfc(Path(raw_name.replace("\\", "/")).name)
            if Path(name).suffix.lower() not in SUPPORTED or info.file_size > MAX_ZIP_MEMBER:
                continue
            target = unique_path(dest / f"{path.stem}__{name}")
            with zf.open(info) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            out.append(target)
    return out


def unique_path(p: Path) -> Path:
    if not p.exists():
        return p
    for i in range(1, 10_000):
        q = p.with_name(f"{p.stem} ({i}){p.suffix}")
        if not q.exists():
            return q
    raise RuntimeError(f"이름을 정할 수 없습니다: {p}")


# ---------------------------------------------------------------- 처리
def process_file(conn, path: Path, meta: FileMeta, *, embed: bool = True,
                 classify: bool = False, force: bool = False) -> Outcome:
    from . import db
    from .parsers import ParseError, extract_text
    from .segment import split_cases

    out = Outcome(file=nfc(path.name), status="failed", published_on=meta.published_on)
    digest = sha256(path)
    existing = db.document_by_sha(conn, digest)
    if existing and not force:
        out.status, out.document_id, out.title = "duplicate", existing["id"], existing["title"]
        out.message = f"이미 저장된 문서(#{existing['id']})와 내용이 같습니다."
        filled = db.fill_missing_meta(conn, existing["id"], meta.published_on,
                                      meta.audit_name, meta.source_url)
        conn.commit()
        if filled:
            out.message += f" 비어 있던 {'·'.join(filled)}을(를) 채웠습니다."
        return out
    try:
        text = extract_text(path)
    except ParseError as e:
        out.message = str(e)
        return out
    if len(text) < 100:
        out.message = "본문이 거의 없습니다(스캔본이거나 빈 문서)."
        return out

    segments = split_cases(text)
    out.title = segments[0].title if len(segments) == 1 else (meta.audit_name or path.stem)
    if existing:
        conn.execute("DELETE FROM documents WHERE id = %s", (existing["id"],))
    doc_id = db.insert_document(
        conn, source=meta.source, title=out.title, full_text=text, file_path=str(path),
        source_url=meta.source_url, published_on=meta.published_on, sha256=digest,
        audit_name=meta.audit_name)
    case_ids = [db.insert_case(conn, document_id=doc_id, seg=seg, published_on=meta.published_on)
                for seg in segments]
    conn.commit()
    out.status, out.document_id, out.cases = "saved", doc_id, len(case_ids)

    if embed:
        try:
            embed_cases(conn, case_ids)
        except ModuleNotFoundError:
            conn.rollback()
            out.warnings.append('임베딩 생략: 의미 검색 모델이 설치되지 않았습니다. '
                                'pip install -e ".[local]" 후 bai embed 를 실행하세요. (키워드 검색은 지금도 됩니다)')
        except Exception as e:  # 저장은 유지하고 나중에 bai embed
            conn.rollback()
            out.warnings.append(f"임베딩 생략({type(e).__name__}: {e}). 나중에 'bai embed'를 실행하세요.")
    if classify:
        out.warnings += classify_cases(conn, case_ids)
    return out


def embed_cases(conn, case_ids: list[int]) -> None:
    from . import db
    from .embed import chunk_case, get_embedder, to_pgvector

    emb = get_embedder()
    for cid in case_ids:
        row = conn.execute("SELECT title, body, action_text FROM cases WHERE id=%s", (cid,)).fetchone()
        texts = chunk_case(row["title"], row["body"], row["action_text"])
        vectors = [to_pgvector(v) for v in emb.embed(texts, "document")]
        db.save_chunks(conn, cid, texts, vectors, emb.name)
    conn.commit()


def classify_cases(conn, case_ids: list[int]) -> list[str]:
    from . import db
    from .classify import classify_case, known_fields

    warnings = []
    for cid in case_ids:
        row = conn.execute(
            """SELECT id, title, body, dispositions, responsible_agency, action_agency, action_text
               FROM cases WHERE id=%s""", (cid,)).fetchone()
        try:
            result = classify_case(row["title"], row["body"], known=known_fields(row))
        except Exception as e:
            from .classify import explain_api_error
            msg = explain_api_error(e) or f"{type(e).__name__}: {e}"
            warnings.append(f"#{cid} 분류 실패 — {msg} 나중에 'bai classify'로 다시 시도하세요.")
            continue
        db.save_classification(conn, cid, result)
        conn.commit()
    return warnings


def handle(conn, path: Path, meta_for, *, processed: Path, failed: Path,
           embed: bool = True, classify: bool = False) -> list[Outcome]:
    """파일 하나(ZIP이면 안의 파일들)를 처리하고 처리됨/실패 폴더로 옮깁니다."""
    today_dir = processed / date.today().isoformat()
    targets = [path]
    if path.suffix.lower() == ".zip":
        try:
            targets = expand_zip(path, path.parent)
        except zipfile.BadZipFile:
            return [_fail(path, failed, "손상된 ZIP 파일입니다.")]
        _move(path, today_dir)
        if not targets:
            return [Outcome(file=nfc(path.name), status="failed",
                            message="ZIP 안에 PDF·HWP·HWPX 파일이 없습니다.")]
    outcomes = []
    for target in targets:
        if target.suffix.lower() not in SUPPORTED:
            outcomes.append(_fail(target, failed, "지원하지 않는 형식입니다(PDF·HWP·HWPX·ZIP만)."))
            continue
        try:
            oc = process_file(conn, target, meta_for(target), embed=embed, classify=classify)
        except Exception as e:
            conn.rollback()
            oc = Outcome(file=nfc(target.name), status="failed", message=f"{type(e).__name__}: {e}")
        if oc.status == "failed":
            _fail(target, failed, oc.message)
        else:
            _move(target, today_dir)
        outcomes.append(oc)
    return outcomes


def run_pass(conn, inbox: Path, *, processed: Path, failed: Path, embed: bool = True,
             classify: bool = False, now: float | None = None) -> list[Outcome]:
    """받은 파일 폴더를 한 번 훑어 준비된 파일을 모두 처리합니다."""
    inbox.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(inbox)
    outcomes: list[Outcome] = []
    for path in sorted(p for p in inbox.iterdir() if p.is_file()):
        if path.suffix.lower() == ".csv" or not is_ready(path, now):
            continue
        outcomes += handle(conn, path, lambda t: resolve_meta(t, manifest),
                           processed=processed, failed=failed, embed=embed, classify=classify)
    if outcomes:
        write_log(processed / "수집기록.csv", outcomes)
    return outcomes


def override_meta(base: FileMeta, published_on: date | None, audit_name: str | None,
                  source_url: str | None) -> FileMeta:
    return FileMeta(published_on=published_on or base.published_on,
                    audit_name=audit_name or base.audit_name,
                    source_url=source_url or base.source_url, source=base.source)


def _move(path: Path, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    return Path(shutil.move(str(path), unique_path(folder / path.name)))


def _fail(path: Path, failed: Path, message: str) -> Outcome:
    moved = _move(path, failed)
    moved.with_name(moved.name + ".error.txt").write_text(message + "\n", encoding="utf-8")
    return Outcome(file=nfc(path.name), status="failed", message=message)


LOG_FIELDS = ["처리시각", "파일", "결과", "문서번호", "사건수", "공개일", "제목", "메시지"]
STATUS_KO = {"saved": "저장", "duplicate": "중복", "failed": "실패"}


def write_log(path: Path, outcomes: list[Outcome]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", encoding="utf-8-sig" if new else "utf-8", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(LOG_FIELDS)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for o in outcomes:
            w.writerow([ts, o.file, STATUS_KO[o.status], o.document_id or "", o.cases or "",
                        o.published_on or "", o.title, " / ".join([o.message] + o.warnings).strip(" /")])
