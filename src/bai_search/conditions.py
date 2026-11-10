"""질문 문장에서 검색 조건(연도, 금액, 처분, 기관, 기관유형)을 규칙으로 뽑아냅니다.

AI를 쓰지 않으므로 비용이 없고 결과가 항상 같습니다. 뽑힌 조건은 화면에 그대로 보여 주고,
조건 때문에 결과가 0건이면 조건을 풀고 다시 검색합니다.

주의: 금액·기관유형은 AI 분류(bai classify) 결과를 기준으로 거르므로 아직 분류하지 않은
사건은 걸리지 않습니다. 연도는 AI가 뽑은 감사 연도가 없으면 공개일의 연도를 씁니다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import date

from .search import Filters

_UNITS = {"조": 10**12, "억": 10**8, "천만": 10**7, "백만": 10**6, "십만": 10**5, "만": 10**4}
_NUM = r"\d[\d,]*(?:\.\d+)?"
_AMOUNT_EXPR = rf"(?:{_NUM}\s*(?:조|억|천만|백만|십만|만)?\s*)+|(?:천만|백만|억)"
_MIN_WORDS = r"이상|넘는|넘게|넘은|넘어|초과|넘었던|이 넘는|을 넘는|를 넘는"
_MAX_WORDS = r"이하|미만|안 되는|아래|이내"


@dataclass
class Extracted:
    filters: Filters = field(default_factory=Filters)
    labels: list[str] = field(default_factory=list)
    query: str = ""          # 조건 표현을 뺀 나머지 (검색어로 사용)

    def any(self) -> bool:
        return bool(self.labels)


def parse_amount(expr: str) -> int | None:
    """'1억 5천만' -> 150000000, '50,000,000' -> 50000000, '천만' -> 10000000"""
    expr = expr.strip()
    if expr in _UNITS:
        return _UNITS[expr]
    total = 0
    found = False
    for num, unit in re.findall(rf"({_NUM})\s*(조|억|천만|백만|십만|만)?", expr):
        value = float(num.replace(",", ""))
        total += value * _UNITS.get(unit, 1)
        found = True
    return int(total) if found else None


def won(n: int) -> str:
    if n >= 10**8 and n % 10**7 == 0:
        eok, rest = divmod(n, 10**8)
        return f"{eok}억 {rest // 10**7}천만 원" if rest else f"{eok}억 원"
    if n >= 10**4 and n % 10**4 == 0:
        return f"{n // 10**4:,}만 원"
    return f"{n:,}원"


# ---------------------------------------------------------------- 처분
# 뜻이 분명한 말: 단독으로 나와도 처분 조건
_STRONG = [
    (r"변상\s*(?:판정|명령|책임)?", "변상판정"),
    (r"징계\s*(?:요구|처분)?", "징계요구"),
    (r"문책\s*(?:요구)?", "문책요구"),
    (r"고발", "고발"),
    (r"수사\s*(?:요청|의뢰)", "수사요청"),
]
# 일상어로도 쓰이는 말: '요구/처분'이 붙을 때만 (예: '통보하지 않은'은 제외)
_WEAK = [
    (r"주의\s*(?:요구|처분)", "주의요구"),
    (r"시정\s*(?:요구|처분)", "시정요구"),
    (r"개선\s*요구", "개선요구"),
    (r"통보\s*(?:처분|요구)|통보\s*(?:받은|된|건)", "통보"),
    (r"권고\s*(?:처분|받은|된|건)", "권고"),
]

_AGENCY_TYPES = [
    (r"지자체|지방자치단체|기초자치단체|광역자치단체|시\s*[·ㆍ.]?\s*군\s*[·ㆍ.]?\s*구", "지방자치단체"),
    (r"공공기관|공기업|준정부기관|공단\s*및\s*공사", "공공기관"),
    (r"중앙\s*부처|중앙행정기관|정부\s*부처", "중앙행정기관"),
]


def _agency_aliases(name: str) -> list[str]:
    out = [name]
    m = re.match(r"^(.+?)(특별시|광역시|특별자치시)$", name)
    if m:
        out.append(m.group(1) + "시")
    m = re.match(r"^(.+?)(특별자치도)$", name)
    if m:
        out.append(m.group(1) + "도")
    return out


def extract(question: str, agencies: list[str] | None = None, today: date | None = None) -> Extracted:
    today = today or date.today()
    f = Filters()
    labels: list[str] = []
    spans: list[tuple[int, int]] = []

    def take(m: re.Match) -> None:
        spans.append(m.span())

    # 연도 ---------------------------------------------------------------
    year_rules = [
        (rf"(20\d{{2}})\s*년?\s*(?:부터|~|∼|-|–|에서)\s*(20\d{{2}})\s*년?\s*(?:까지|사이)?",
         lambda m: (int(m[1]), int(m[2]))),
        (r"(20\d{2})\s*년\s*(?:이후|부터|以後)", lambda m: (int(m[1]), None)),
        (r"(20\d{2})\s*년\s*(?:이전|까지|以前)", lambda m: (None, int(m[1]))),
        (r"최근\s*(\d{1,2})\s*년(?:간)?", lambda m: (today.year - int(m[1]) + 1, today.year)),
        (r"(20\d{2})\s*년(?:도)?", lambda m: (int(m[1]), int(m[1]))),
        (r"올해|금년", lambda m: (today.year, today.year)),
        (r"작년|지난해|전년", lambda m: (today.year - 1, today.year - 1)),
    ]
    for pat, conv in year_rules:
        m = re.search(pat, question)
        if m:
            f.year_from, f.year_to = conv(m)
            take(m)
            if f.year_from and f.year_to and f.year_from == f.year_to:
                labels.append(f"{f.year_from}년")
            elif f.year_from and f.year_to:
                labels.append(f"{f.year_from}~{f.year_to}년")
            elif f.year_from:
                labels.append(f"{f.year_from}년 이후")
            else:
                labels.append(f"{f.year_to}년 이전")
            break

    # 금액 ---------------------------------------------------------------
    m = re.search(rf"({_AMOUNT_EXPR})\s*(원)?\s*(?:이|을|를)?\s*({_MIN_WORDS})", question)
    if m and (m[2] or re.search(r"조|억|만", m[1])):
        n = parse_amount(m[1])
        if n:
            f.min_amount = n
            labels.append(f"{won(n)} 이상")
            take(m)
    m = re.search(r"(\d+)\s*억\s*대", question)
    if m and not f.min_amount:
        f.min_amount = int(m[1]) * 10**8
        labels.append(f"{won(f.min_amount)} 이상")
        take(m)

    # 처분 ---------------------------------------------------------------
    disps: list[str] = []
    for pat, d in _STRONG + _WEAK:
        for m in re.finditer(pat, question):
            if d not in disps:
                disps.append(d)
            take(m)
    if disps:
        f.dispositions = disps
        labels.append("처분: " + "·".join(disps))

    # 기관유형 -----------------------------------------------------------
    for pat, t in _AGENCY_TYPES:
        m = re.search(pat, question)
        if m:
            f.agency_type = t
            labels.append(t)
            take(m)
            break

    # 기관명 (DB에 있는 이름과 대조) ---------------------------------------
    for name in sorted(agencies or [], key=len, reverse=True):
        hit = next((a for a in _agency_aliases(name) if len(a) >= 3 and a in question), None)
        if hit:
            f.agency = name
            labels.append(f"기관: {name}")
            i = question.index(hit)
            spans.append((i, i + len(hit)))
            break

    rest = question
    for a, b in sorted(spans, reverse=True):
        rest = rest[:a] + " " + rest[b:]
    rest = re.sub(r"\s+", " ", rest).strip()
    return Extracted(filters=f, labels=labels, query=rest or question)


def merge(explicit: Filters, auto: Filters) -> Filters:
    """사용자가 직접 고른 조건이 우선, 빈 칸만 자동 조건으로 채움."""
    out = replace(explicit)
    for k, v in vars(auto).items():
        if v not in (None, []) and getattr(out, k) in (None, []):
            setattr(out, k, v)
    return out


def known_agencies(conn) -> list[str]:
    rows = conn.execute(
        """SELECT DISTINCT unnest(ARRAY[agency, action_agency, responsible_agency]) AS a
           FROM cases"""
    ).fetchall()
    return [r["a"] for r in rows if r["a"]]
