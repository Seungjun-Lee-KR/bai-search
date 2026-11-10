"""Claude로 지적사항을 구조화·분류합니다.

공개된 감사결과만 처리하세요. 비공개·내부 자료를 외부 LLM API로 보내면
공공기관 보안 지침에 저촉될 수 있습니다.
"""
from __future__ import annotations

import os
import re
from typing import Literal, Optional

from pydantic import BaseModel, Field

from .taxonomy import (
    AGENCY_TYPES, CONTRACT_METHODS, DISPOSITIONS, VIOLATION_CODES, taxonomy_prompt,
)

ViolationCode = Literal.__getitem__(VIOLATION_CODES)
Disposition = Literal.__getitem__(DISPOSITIONS)
AgencyType = Literal.__getitem__(AGENCY_TYPES)
ContractMethod = Literal.__getitem__(CONTRACT_METHODS)

DEFAULT_MODEL = "claude-opus-5-5"
MAX_BODY_CHARS = 60_000


class CaseExtraction(BaseModel):
    agency: Optional[str] = Field(description="피감기관명. 원문에 없으면 null")
    agency_type: AgencyType
    audit_year: Optional[int] = Field(description="감사 실시 연도. 모르면 null")
    violation_types: list[ViolationCode] = Field(description="해당하는 위반유형 코드. 1~3개")
    dispositions: list[Disposition] = Field(description="원문에 적힌 처분요구 종류")
    amount_krw: Optional[int] = Field(description="지적·손실·변상 금액(원). 여러 개면 합계. 없으면 null")
    contract_method: ContractMethod
    vendor_related: bool = Field(description="특정 업체와의 계약·유착이 쟁점이면 true")
    related_laws: list[str] = Field(description="원문에 인용된 법령 조문. 예: '국가계약법 시행령 제26조'")
    summary: str = Field(description="무엇이, 어떻게, 얼마나 잘못됐는지 3문장 이내 요약")
    confidence: float = Field(description="분류 확신도 0~1")


SYSTEM_PROMPT = f"""당신은 감사원 감사결과를 분석하는 감사 실무 보조자입니다.
주어진 지적사항 원문 하나를 읽고 구조화된 항목을 채우세요.

규칙
- 원문에 적힌 사실만 사용합니다. 원문에 없는 금액, 법령, 기관명은 만들지 말고 null 또는 빈 목록으로 둡니다.
- 금액은 원 단위 정수로 바꿉니다. 예: '3억 2천만 원' -> 320000000.
- 개인 이름은 요약에 쓰지 말고 직급이나 역할(예: 회계담당 6급)로 씁니다.
- 판단이 애매하면 confidence를 0.6 아래로 줍니다.
- <확정정보>는 문서 양식에서 읽은 값이므로 그대로 따릅니다.
- 원문의 업체명·인명은 공개 시 지워져 빈칸이나 A, B로 남아 있습니다. 추측해서 채우지 않습니다.

위반유형 분류표
{taxonomy_prompt()}
"""

_RRN = re.compile(r"\b\d{6}-?[1-4]\d{6}\b")
_PHONE = re.compile(r"\b01[016789]-?\d{3,4}-?\d{4}\b")


def redact(text: str) -> str:
    """API로 보내기 전 주민등록번호·휴대전화번호를 가립니다."""
    text = _RRN.sub("[주민번호]", text)
    return _PHONE.sub("[전화번호]", text)


class ClassificationRefused(Exception):
    pass


NO_KEY_MESSAGE = (".env 파일의 ANTHROPIC_API_KEY에 Anthropic API 키를 넣으세요. "
                  "키는 https://console.anthropic.com 에서 발급합니다.")


def explain_api_error(e: Exception) -> str | None:
    """Claude API 호출 실패를 사람이 읽을 안내로. 해당 없으면 None."""
    import anthropic

    if isinstance(e, TypeError) and "authentication" in str(e):
        return "API 키가 없습니다. " + NO_KEY_MESSAGE
    if isinstance(e, anthropic.AuthenticationError):
        return "API 키가 올바르지 않습니다. " + NO_KEY_MESSAGE
    if isinstance(e, anthropic.PermissionDeniedError):
        return "이 API 키로는 해당 모델을 쓸 수 없습니다. 콘솔에서 권한·결제 상태를 확인하세요."
    if isinstance(e, anthropic.RateLimitError):
        return "요청이 너무 많습니다. 잠시 뒤 다시 시도하세요."
    if isinstance(e, anthropic.APIConnectionError):
        return "Anthropic 서버에 연결하지 못했습니다. 인터넷·방화벽(api.anthropic.com) 설정을 확인하세요."
    if isinstance(e, anthropic.APIStatusError):
        return f"Claude API 오류({e.status_code}): {e.message}"
    return None


def known_fields(row: dict) -> dict[str, str]:
    """처분요구서 양식에서 이미 확정된 값. AI에 참고로 넘깁니다."""
    out = {}
    if row.get("dispositions"):
        out["처분"] = ", ".join(row["dispositions"])
    if row.get("responsible_agency"):
        out["소관기관"] = row["responsible_agency"]
    if row.get("action_agency"):
        out["조치기관"] = row["action_agency"]
    if row.get("action_text"):
        out["조치할 사항"] = row["action_text"]
    return out


def build_user_message(title: str, body: str, known: dict[str, str] | None = None) -> str:
    parts = [f"<제목>{title}</제목>"]
    if known:
        meta = "\n".join(f"{k}: {v}" for k, v in known.items())
        parts.append(f"<확정정보>\n{meta}\n</확정정보>")
    parts.append(f"<원문>\n{body}\n</원문>")
    return "\n".join(parts)


def classify_case(title: str, body: str, client=None, model: str | None = None,
                  known: dict[str, str] | None = None) -> CaseExtraction:
    import anthropic

    client = client or anthropic.Anthropic()
    model = model or os.environ.get("CLASSIFY_MODEL", DEFAULT_MODEL)
    if len(body) > MAX_BODY_CHARS:
        raise ValueError(f"본문이 너무 깁니다({len(body)}자). 사건 분할을 먼저 확인하세요.")

    response = client.messages.parse(
        model=model,
        max_tokens=4000,
        system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        messages=[{
            "role": "user",
            "content": redact(build_user_message(title, body, known)),
        }],
        output_config={"effort": "low"},
        output_format=CaseExtraction,
    )
    if response.stop_reason == "refusal":
        raise ClassificationRefused(getattr(response, "stop_details", None))
    if response.parsed_output is None:
        raise ValueError(f"구조화 결과가 없습니다(stop_reason={response.stop_reason}).")
    return response.parsed_output
