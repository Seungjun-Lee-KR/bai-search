"""위반유형·처분 분류표.

감사 실무 기준으로 계속 다듬는 파일입니다. 코드(code)는 DB에 저장되는 값이므로
한 번 정한 코드는 바꾸지 말고, 이름(label)·설명만 고치거나 새 코드를 추가하세요.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ViolationType:
    code: str
    group: str
    label: str
    description: str
    keywords: tuple[str, ...] = ()


VIOLATION_TYPES: tuple[ViolationType, ...] = (
    # 횡령·유용
    ViolationType("EMBEZZLE_FUNDS", "횡령·유용", "공금 횡령",
                  "회계·출납 담당자 등이 공금, 세입금, 보관금을 개인적으로 가져간 경우",
                  ("횡령", "착복", "개인 계좌", "유용")),
    ViolationType("MISUSE_CARD", "횡령·유용", "업무추진비·법인카드 사적 사용",
                  "업무추진비, 법인카드, 클린카드 등을 사적 용도나 부적정 업소에서 사용",
                  ("업무추진비", "법인카드", "클린카드", "사적 사용")),
    ViolationType("FALSE_CLAIM", "횡령·유용", "출장비·수당 허위 청구",
                  "출장여비, 초과근무수당, 연구비 등을 허위·과다 청구",
                  ("허위 출장", "초과근무", "여비", "부당 수령")),
    # 계약 유착
    ViolationType("SPLIT_CONTRACT", "계약 유착", "수의계약 분할발주(쪼개기)",
                  "수의계약 한도를 피하려 동일 사업을 나누어 발주",
                  ("분할", "쪼개", "수의계약 한도")),
    ViolationType("FAVORED_VENDOR", "계약 유착", "특정업체 몰아주기",
                  "정당한 사유 없이 특정 업체와 반복·집중 계약",
                  ("특정업체", "특정 업체", "몰아주", "반복 계약")),
    ViolationType("RELATED_PARTY", "계약 유착", "퇴직자·친인척·이해관계 업체 계약",
                  "퇴직 공무원, 친인척, 이해관계자가 관련된 업체와 계약",
                  ("퇴직자", "친인척", "이해충돌", "가족")),
    ViolationType("BID_RIGGING", "계약 유착", "입찰 담합·들러리 방치",
                  "담합, 들러리 입찰을 알고도 방치하거나 입찰 조건을 특정업체에 맞춤",
                  ("담합", "들러리", "입찰 조건", "자격 제한")),
    ViolationType("CHANGE_ORDER", "계약 유착", "설계변경 통한 부당 증액",
                  "설계변경, 물량 증가 등으로 계약금액을 부당하게 증액",
                  ("설계변경", "증액", "물가변동")),
    ViolationType("POOR_INSPECTION", "계약 유착", "검사·검수 부실",
                  "미시공, 미납품인데 준공·검수 처리하고 대금 지급",
                  ("검수", "준공검사", "미시공", "기성")),
    # 재정 누수
    ViolationType("SUBSIDY_FRAUD", "재정 누수", "보조금 부정수급",
                  "보조사업자의 허위 정산, 목적 외 사용 등",
                  ("보조금", "부정수급", "목적 외")),
    ViolationType("DUPLICATE_PAY", "재정 누수", "중복·과다 지급",
                  "동일 대상에 중복 지급하거나 기준보다 많이 지급",
                  ("중복 지급", "과다 지급", "과다 산정")),
    ViolationType("UNCOLLECTED", "재정 누수", "미징수·미회수",
                  "부과·징수해야 할 금액을 부과하지 않거나 회수하지 않음",
                  ("미징수", "미회수", "부과 누락")),
    # 기타
    ViolationType("PERSONNEL", "기타", "인사 부당",
                  "채용, 승진, 전보 등 인사 업무 부당 처리", ("채용", "승진", "인사")),
    ViolationType("PERMIT", "기타", "인허가 부당",
                  "인허가, 승인 업무 부당 처리", ("인허가", "허가", "승인")),
    ViolationType("NEGLIGENCE", "기타", "직무태만·관리 부실",
                  "위 유형에 해당하지 않는 업무 소홀, 관리 감독 부실", ("소홀", "부실", "태만")),
    ViolationType("OTHER", "기타", "기타", "위 어디에도 해당하지 않음"),
)

VIOLATION_CODES: tuple[str, ...] = tuple(v.code for v in VIOLATION_TYPES)
VIOLATION_BY_CODE: dict[str, ViolationType] = {v.code: v for v in VIOLATION_TYPES}

# 감사원법상 처분요구 등 (제31조~제34조의2, 고발·수사요청 포함)
DISPOSITIONS: tuple[str, ...] = (
    "변상판정", "징계요구", "문책요구", "시정요구", "주의요구",
    "개선요구", "권고", "통보", "고발", "수사요청",
)

AGENCY_TYPES: tuple[str, ...] = ("중앙행정기관", "지방자치단체", "공공기관", "기타")

CONTRACT_METHODS: tuple[str, ...] = ("수의계약", "제한경쟁", "지명경쟁", "일반경쟁", "해당없음")


def taxonomy_prompt() -> str:
    """LLM 분류 프롬프트에 넣을 분류표 설명."""
    lines = []
    for v in VIOLATION_TYPES:
        lines.append(f"- {v.code} [{v.group}] {v.label}: {v.description}")
    return "\n".join(lines)
