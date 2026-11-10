from bai_search.classify import CaseExtraction, redact
from bai_search.segment import split_cases
from bai_search.taxonomy import VIOLATION_CODES


def test_split_by_title_lines():
    body = "내용 " * 120
    text = f"감사결과 보고서\n제 목: 수의계약 분할발주 부적정\n{body}\n제 목: 업무추진비 사적 사용\n{body}"
    segs = split_cases(text)
    assert [s.title for s in segs] == ["수의계약 분할발주 부적정", "업무추진비 사적 사용"]


def test_no_heading_returns_whole_document():
    segs = split_cases("첫 줄\n본문")
    assert len(segs) == 1 and segs[0].title == "첫 줄"


def test_codes_unique():
    assert len(set(VIOLATION_CODES)) == len(VIOLATION_CODES)


def test_schema_rejects_unknown_code():
    import pydantic
    import pytest

    base = dict(agency="○○시", agency_type="지방자치단체", audit_year=2024,
                dispositions=["주의요구"], amount_krw=1000, contract_method="수의계약",
                vendor_related=True, related_laws=[], summary="요약", confidence=0.9)
    CaseExtraction(violation_types=["SPLIT_CONTRACT"], **base)
    with pytest.raises(pydantic.ValidationError):
        CaseExtraction(violation_types=["NOPE"], **base)


def test_redact():
    assert redact("900101-1234567 010-1234-5678") == "[주민번호] [전화번호]"
