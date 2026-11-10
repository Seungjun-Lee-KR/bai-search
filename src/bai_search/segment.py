"""감사결과 보고서 전문을 지적사항(사건) 단위로 나눕니다.

감사원 공개문은 지적사항마다 '제 목', '관계기관' 같은 머리 항목이 반복됩니다.
실제 문서를 받아 본 뒤 HEADING_PATTERNS를 조정하세요.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

HEADING_PATTERNS = (
    re.compile(r"^\s*제\s*목\s*[:：]?\s*(?P<title>.+)$"),
    re.compile(r"^\s*【\s*(?P<title>[^】]{4,80})\s*】\s*$"),
    re.compile(r"^\s*(?:\d{1,2}|[가-하])\s*[.)]\s+(?P<title>.{4,80}(?:부적정|부당|소홀|미흡|누락|위반|횡령|미징수|과다|특혜))\s*$"),
)

MIN_BODY_CHARS = 200


@dataclass
class Segment:
    title: str
    body: str


def split_cases(text: str) -> list[Segment]:
    lines = text.split("\n")
    starts: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        for pat in HEADING_PATTERNS:
            m = pat.match(line)
            if m:
                starts.append((i, m.group("title").strip()))
                break

    if not starts:
        return [Segment(title=_first_line(text), body=text)]

    segments: list[Segment] = []
    for idx, (line_no, title) in enumerate(starts):
        end = starts[idx + 1][0] if idx + 1 < len(starts) else len(lines)
        body = "\n".join(lines[line_no:end]).strip()
        if len(body) < MIN_BODY_CHARS and segments:
            segments[-1].body += "\n" + body  # 너무 짧은 조각은 앞 사건에 붙임
            continue
        segments.append(Segment(title=title, body=body))
    return segments


def _first_line(text: str) -> str:
    for line in text.split("\n"):
        if line.strip():
            return line.strip()[:120]
    return "(제목 없음)"
