"""통계 대시보드용 집계.

- 한 사건에 위반유형이 여러 개면 유형별 집계에는 각각 1건씩 들어가고(합이 전체보다 클 수 있음),
  연도별 추이의 그룹 구분에는 첫 번째 위반유형의 그룹(주 유형)만 씁니다.
- 금액은 AI 분류가 뽑은 지적금액(amount_krw) 기준이며, 분류 전 사건은 '미분류'로 따로 셉니다.
"""
from __future__ import annotations

from .search import Filters
from .taxonomy import DISPOSITIONS, VIOLATION_BY_CODE, VIOLATION_TYPES

YEAR = "COALESCE(c.audit_year, EXTRACT(YEAR FROM c.published_on)::int)"
HEAVY = ["징계요구", "문책요구", "변상판정", "고발", "수사요청"]
# 고정 상수(사용자 입력 아님)라 SQL에 바로 넣음
HEAVY_SQL = "ARRAY[" + ",".join(f"'{d}'" for d in HEAVY) + "]::text[]"
GROUPS = list(dict.fromkeys(v.group for v in VIOLATION_TYPES))   # 횡령·유용, 계약 유착, 재정 누수, 기타


def collect_stats(conn, f: Filters | None = None, top_n: int = 10) -> dict:
    f = f or Filters()
    where, p = f.sql("c")

    def q(sql: str, extra: list | None = None):
        return conn.execute(sql.replace("{W}", where), p + (extra or [])).fetchall()

    kpi = q(f"""SELECT count(*) AS cases,
                       count(*) FILTER (WHERE c.summary IS NOT NULL) AS classified,
                       coalesce(sum(c.amount_krw), 0)::bigint AS amount,
                       count(*) FILTER (WHERE c.amount_krw IS NOT NULL) AS with_amount,
                       count(*) FILTER (WHERE c.dispositions && {HEAVY_SQL}) AS heavy,
                       count(*) FILTER (WHERE c.vendor_related) AS vendor,
                       count(*) FILTER (WHERE {YEAR} IS NULL) AS no_year
                FROM cases c WHERE {{W}}""")[0]

    by_type = q(f"""SELECT t AS code, count(*) AS cases, coalesce(sum(c.amount_krw), 0)::bigint AS amount,
                          count(*) FILTER (WHERE c.dispositions && {HEAVY_SQL}) AS heavy
                   FROM cases c, unnest(c.violation_types) AS t WHERE {{W}}
                   GROUP BY t ORDER BY cases DESC, amount DESC""")
    for r in by_type:
        v = VIOLATION_BY_CODE.get(r["code"])
        r["label"], r["group"] = (v.label, v.group) if v else (r["code"], "기타")

    by_year_rows = q(f"""SELECT {YEAR} AS year, c.violation_types[1] AS primary_type, count(*) AS cases,
                                coalesce(sum(c.amount_krw), 0)::bigint AS amount
                         FROM cases c WHERE {{W}} AND {YEAR} IS NOT NULL
                         GROUP BY 1, 2 ORDER BY 1""")
    years: dict[int, dict] = {}
    for r in by_year_rows:
        y = years.setdefault(r["year"], {"year": r["year"], "total": 0, "amount": 0,
                                         **{g: 0 for g in GROUPS}, "미분류": 0})
        v = VIOLATION_BY_CODE.get(r["primary_type"]) if r["primary_type"] else None
        y[v.group if v else "미분류"] += r["cases"]
        y["total"] += r["cases"]
        y["amount"] += r["amount"]
    if years:  # 빈 해도 0으로 채워 축이 끊기지 않게
        for yr in range(min(years), max(years) + 1):
            years.setdefault(yr, {"year": yr, "total": 0, "amount": 0, **{g: 0 for g in GROUPS}, "미분류": 0})

    by_disp = q("""SELECT d AS disposition, count(*) AS cases FROM cases c, unnest(c.dispositions) AS d
                   WHERE {W} GROUP BY d""")
    order = {d: i for i, d in enumerate(DISPOSITIONS)}
    by_disp.sort(key=lambda r: order.get(r["disposition"], 99))

    by_agency = q("""SELECT coalesce(c.action_agency, c.agency, '(미상)') AS agency, count(*) AS cases,
                            coalesce(sum(c.amount_krw), 0)::bigint AS amount
                     FROM cases c WHERE {W} GROUP BY 1 ORDER BY cases DESC, amount DESC LIMIT %s""", [top_n])

    matrix = q("""SELECT t AS code, d AS disposition, count(*) AS cases
                  FROM cases c, unnest(c.violation_types) AS t, unnest(c.dispositions) AS d
                  WHERE {W} GROUP BY t, d""")

    top_amount = q("""SELECT c.id, c.title, coalesce(c.action_agency, c.agency) AS agency,
                             c.amount_krw, c.dispositions, c.violation_types, c.published_on, c.audit_year
                      FROM cases c WHERE {W} AND c.amount_krw IS NOT NULL
                      ORDER BY c.amount_krw DESC LIMIT %s""", [top_n])

    return {
        "kpi": kpi,
        "groups": GROUPS,
        "heavy_dispositions": HEAVY,
        "by_type": by_type,
        "by_year": [years[y] for y in sorted(years)],
        "by_disposition": by_disp,
        "by_agency": by_agency,
        "matrix": matrix,
        "top_amount": top_amount,
    }
