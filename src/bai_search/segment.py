"""감사원 처분요구서 문서를 구조화하고, 여러 건이 묶인 문서는 사건 단위로 나눕니다.

감사원 공개 처분요구서는 다음 양식을 따릅니다.

    감 사 원
    주 의 요 구            <- 처분 종류 (글자 사이 공백)
    제 목 ...
    소 관 기 관 ...
    조 치 기 관 ...
    내 용
    1. 업무 개요 ...
    조치할 사항 ○○장은 ... 바랍니다.(주의)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# 처분 종류 머리말(공백 제거 후)과 조치할 사항 끝 괄호 → taxonomy.DISPOSITIONS 값
_DISPOSITION_MAP = {
    "변상판정": "변상판정", "변상": "변상판정",
    "징계요구": "징계요구", "징계": "징계요구",
    "문책요구": "문책요구", "문책": "문책요구",
    "시정요구": "시정요구", "시정": "시정요구",
    "주의요구": "주의요구", "주의": "주의요구",
    "개선요구": "개선요구", "개선": "개선요구",
    "권고": "권고", "통보": "통보", "고발": "고발", "수사요청": "수사요청",
}

_LABEL = {
    "title": re.compile(r"^\s*제\s*목\s*[:：]?\s*(.*)$"),
    "responsible_agency": re.compile(r"^\s*소\s*관\s*기\s*관\s*[:：]?\s*(.*)$"),
    "action_agency": re.compile(r"^\s*조\s*치\s*기\s*관\s*[:：]?\s*(.*)$"),
    "content": re.compile(r"^\s*내\s*용\s*$"),
}
_ACTION = re.compile(r"^\s*조치할\s*사항\s*(.*)$")
_ACTION_TAG = re.compile(r"\((" + "|".join(sorted(_DISPOSITION_MAP, key=len, reverse=True)) + r")\)")
_PAGE_MARK = re.compile(r"^\s*-\s*\d+\s*-\s*$")
_FOOTNOTE = re.compile(r"^\s*\d{1,2}\)\s")

# 양식에 맞지 않는 문서용 예비 규칙
HEADING_PATTERNS = (
    _LABEL["title"],
    re.compile(r"^\s*【\s*(?P<title>[^】]{4,80})\s*】\s*$"),
)
MIN_BODY_CHARS = 200


@dataclass
class Segment:
    title: str
    body: str
    dispositions: list[str] = field(default_factory=list)
    responsible_agency: str | None = None
    action_agency: str | None = None
    action_text: str | None = None


def clean(text: str) -> str:
    """쪽 번호(- 3 -) 줄을 지웁니다."""
    return "\n".join(l for l in text.split("\n") if not _PAGE_MARK.match(l))


def parse_disposition_doc(text: str) -> Segment | None:
    """처분요구서 양식이면 Segment로, 아니면 None."""
    lines = clean(text).split("\n")
    found: dict[str, int] = {}
    for i, line in enumerate(lines[:40]):
        for key, pat in _LABEL.items():
            if key not in found and pat.match(line):
                found[key] = i
    if not {"title", "responsible_agency", "content"} <= found.keys():
        return None

    def value(key: str) -> str | None:
        if key not in found:
            return None
        return _LABEL[key].match(lines[found[key]]).group(1).strip() or None

    # 제목이 여러 줄로 이어지면 다음 머리 항목 전까지 붙임
    t0 = found["title"]
    nxt = min(v for k, v in found.items() if v > t0)
    title = " ".join([value("title") or ""] + [l.strip() for l in lines[t0 + 1:nxt]]).strip()

    header = "".join("".join(lines[:t0]).split())
    header = header.replace("감사원", "", 1)
    dispositions = [_DISPOSITION_MAP[header]] if header in _DISPOSITION_MAP else []

    start = found["content"] + 1
    action_at = next((i for i in range(start, len(lines)) if _ACTION.match(lines[i])), None)
    body_lines = lines[start:action_at] if action_at is not None else lines[start:]
    action_text = None
    if action_at is not None:
        tail = [_ACTION.match(lines[action_at]).group(1)]
        for l in lines[action_at + 1:]:
            if _FOOTNOTE.match(l):  # 마지막 쪽 각주
                break
            tail.append(l)
        action_text = "\n".join(s.strip() for s in tail if s.strip())
        for tag in _ACTION_TAG.findall(action_text):
            d = _DISPOSITION_MAP[tag]
            if d not in dispositions:
                dispositions.append(d)

    return Segment(
        title=title,
        body="\n".join(body_lines).strip(),
        dispositions=dispositions,
        responsible_agency=value("responsible_agency"),
        action_agency=value("action_agency"),
        action_text=action_text,
    )


def split_cases(text: str) -> list[Segment]:
    doc = parse_disposition_doc(text)
    if doc:
        return [doc]
    return _split_by_headings(clean(text))


def _split_by_headings(text: str) -> list[Segment]:
    lines = text.split("\n")
    starts: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        for pat in HEADING_PATTERNS:
            m = pat.match(line)
            if m:
                starts.append((i, (m.groupdict().get("title") or m.group(1)).strip()))
                break
    if not starts:
        return [Segment(title=_first_line(text), body=text)]

    segments: list[Segment] = []
    for idx, (line_no, title) in enumerate(starts):
        end = starts[idx + 1][0] if idx + 1 < len(starts) else len(lines)
        body = "\n".join(lines[line_no:end]).strip()
        if len(body) < MIN_BODY_CHARS and segments:
            segments[-1].body += "\n" + body
            continue
        segments.append(Segment(title=title, body=body))
    return segments


def _first_line(text: str) -> str:
    for line in text.split("\n"):
        if line.strip():
            return line.strip()[:120]
    return "(제목 없음)"
