from bai_search.classify import build_user_message, known_fields
from bai_search.segment import parse_disposition_doc, split_cases

# 실제 감사원 처분요구서 양식을 본뜬 가상 문서
SAMPLE = """감 사 원
주 의 요 구
제 목 전산장비 유지보수 용역
협상 계약 부적정
소 관 기 관 ○○처
조 치 기 관 ○○처
내 용
1. 업무 개요
○○처는 업체와 유지보수 계약(금액: 1,000,000,000원)을 체결하였다.
- 1 -
2. 관계 법령 등, 사실관계와 판단기준
제안서에 포함된 과업을 추가 과업으로 변경계약하여 50,000,000원을 추가 부담하였다.
조치할 사항 ○○처장은
① 기존 계약에 포함된 과업을 변경계약으로 추진하는 일이 없도록 하며
② 관련자에게는 주의를 촉구하시기 바랍니다.(주의)
9) 각주 내용
- 8 -"""


def test_parse_header_fields():
    seg = parse_disposition_doc(SAMPLE)
    assert seg.title == "전산장비 유지보수 용역 협상 계약 부적정"
    assert seg.responsible_agency == "○○처"
    assert seg.action_agency == "○○처"
    assert seg.dispositions == ["주의요구"]


def test_body_and_action_split():
    seg = parse_disposition_doc(SAMPLE)
    assert seg.body.startswith("1. 업무 개요")
    assert "- 1 -" not in seg.body
    assert "조치할 사항" not in seg.body
    assert seg.action_text.startswith("○○처장은")
    assert seg.action_text.endswith("(주의)")
    assert "각주" not in seg.action_text


def test_tongbo_and_multiple_tags():
    doc = SAMPLE.replace("주 의 요 구", "통 보").replace("(주의)", "(통보)(시정)")
    assert parse_disposition_doc(doc).dispositions == ["통보", "시정요구"]


def test_non_form_document_falls_back():
    assert parse_disposition_doc("그냥 문서\n본문") is None
    assert len(split_cases("그냥 문서\n본문")) == 1


def test_known_fields_reach_prompt():
    seg = parse_disposition_doc(SAMPLE)
    row = {"dispositions": seg.dispositions, "responsible_agency": seg.responsible_agency,
           "action_agency": seg.action_agency, "action_text": seg.action_text}
    msg = build_user_message(seg.title, seg.body, known_fields(row))
    assert "<확정정보>" in msg and "처분: 주의요구" in msg and "조치기관: ○○처" in msg
