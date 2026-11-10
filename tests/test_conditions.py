from datetime import date

import pytest

from bai_search.conditions import extract, merge, parse_amount, won
from bai_search.search import Filters

TODAY = date(2026, 11, 10)
AGENCIES = ["인천광역시", "법원행정처", "보건복지부", "소방청"]


def ex(q):
    return extract(q, AGENCIES, TODAY)


@pytest.mark.parametrize("expr,value", [
    ("1억", 10**8), ("1억 5천만", 150_000_000), ("5천만", 50_000_000), ("3.5억", 350_000_000),
    ("50,000,000", 50_000_000), ("1,000만", 10**7), ("천만", 10**7), ("10억", 10**9),
])
def test_parse_amount(expr, value):
    assert parse_amount(expr) == value


def test_won_label():
    assert won(150_000_000) == "1억 5천만 원" and won(10**8) == "1억 원" and won(5 * 10**7) == "5,000만 원"


@pytest.mark.parametrize("q,yf,yt", [
    ("2025년 수의계약 쪼개기 사례", 2025, 2025),
    ("2023~2025년 업무추진비 사적 사용", 2023, 2025),
    ("2023년부터 2025년까지 횡령", 2023, 2025),
    ("2024년 이후 보조금 부정수급", 2024, None),
    ("2022년 이전 설계변경 증액", None, 2022),
    ("최근 3년간 공금 횡령", 2024, 2026),
    ("작년 감사에서 지적된 계약", 2025, 2025),
])
def test_years(q, yf, yt):
    f = ex(q).filters
    assert (f.year_from, f.year_to) == (yf, yt)


@pytest.mark.parametrize("q,amount", [
    ("1억 원 이상 횡령 사례", 10**8),
    ("손실액이 5천만원 넘는 계약", 5 * 10**7),
    ("10억 초과 설계변경", 10**9),
    ("1억5천만 원을 넘는 보조금", 150_000_000),
    ("수십억대 아님, 3억대 변상", 3 * 10**8),
])
def test_amounts(q, amount):
    assert ex(q).filters.min_amount == amount


@pytest.mark.parametrize("q", ["3년 이상 근무한 회계직원", "5건 이상 반복 계약", "100 이상"])
def test_not_amounts(q):
    assert ex(q).filters.min_amount is None


@pytest.mark.parametrize("q,disps", [
    ("공금 횡령으로 징계 받은 사례", ["징계요구"]),
    ("횡령해서 고발되거나 변상판정된 건", ["변상판정", "고발"]),
    ("수사의뢰된 입찰 담합", ["수사요청"]),
    ("주의요구 처분된 계약", ["주의요구"]),
    ("시정요구 받은 보조금", ["시정요구"]),
])
def test_dispositions(q, disps):
    assert ex(q).filters.dispositions == disps


@pytest.mark.parametrize("q", [
    "공탁금 횡령 사실을 감사원에 통보하지 않은 사례",   # '통보'는 처분이 아니라 사실
    "주의 깊게 볼 계약 사례",
])
def test_weak_words_are_not_dispositions(q):
    assert ex(q).filters.dispositions == []


@pytest.mark.parametrize("q,t", [
    ("지자체 수의계약 쪼개기", "지방자치단체"),
    ("공공기관 업무추진비", "공공기관"),
    ("중앙부처 출장비 허위 청구", "중앙행정기관"),
])
def test_agency_type(q, t):
    assert ex(q).filters.agency_type == t


def test_agency_name_and_alias():
    assert ex("인천시 기부채납 사례").filters.agency == "인천광역시"
    assert ex("법원행정처 유지보수 계약").filters.agency == "법원행정처"
    assert ex("시청 공사 계약").filters.agency is None


def test_combined_and_query_cleanup():
    e = ex("2024년 이후 지자체에서 1억 원 이상 횡령해서 징계 받은 사례")
    f = e.filters
    assert (f.year_from, f.agency_type, f.min_amount, f.dispositions) == (
        2024, "지방자치단체", 10**8, ["징계요구"])
    assert e.labels == ["2024년 이후", "1억 원 이상", "처분: 징계요구", "지방자치단체"]
    assert "2024" not in e.query and "1억" not in e.query and "횡령" in e.query


def test_no_conditions():
    e = ex("특정업체에 몰아준 사례")
    assert not e.any() and e.query == "특정업체에 몰아준 사례"


def test_merge_explicit_wins():
    m = merge(Filters(disposition="주의요구", year_from=2020), Filters(year_from=2024, min_amount=10**8))
    assert (m.disposition, m.year_from, m.min_amount) == ("주의요구", 2020, 10**8)
