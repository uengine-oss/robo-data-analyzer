"""Agent 출력 Pydantic 스키마 — Scan Agent · 잔여 패턴 루프의 ``response_format`` 강제.

LLM 이 자유 텍스트 응답 못 함. 다운스트림 파싱 부담 X — 환각 차단.

운영 정책 ``ROBO_LLM_REASONING_EFFORT=none`` 과 결합하면 더 강력.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════
# Scan Agent 출력
# ═══════════════════════════════════════════════════════════════
#
# Scan Agent 는 탐색 도구 + 검증 도구를 든 단일 에이전트다. 코드를 둘러보고
# SKILL 을 매칭하고, SKILL 이 없으면 정규식을 직접 작성·검증해 ``ScanResult`` 를
# 출력한다. 정규식 같은 구조물은 검증 도구를 가진 에이전트만 만들 수 있다 — 도구
# 없는 별도 결정 LLM 으로 쪼개면 검증된 정규식이 전달 스키마에서 증발한다(과거 버그).
# 그래서 탐색/결정을 쪼개지 않고 단일 에이전트로 둔다.


class CrudMapping(BaseModel):
    """한 ``op`` (메서드명 / 연산 코드) → CRUD 매핑 row 1 개.

    옛 ``dict[str, Literal["READ", "WRITE"]]`` 형식 대체 — OpenAI structured output
    strict mode 가 dict 의 free-form key 거부 (400 BadRequest). list 는 cross-provider
    안전 (OpenAI / Anthropic / SGLang / OpenRouter 모두 정확 출력).

    예시: ``op="findOne"`` + ``crud="READ"`` / ``op="s"`` + ``crud="READ"`` (proframe).
    """
    op: str = Field(..., description="call_regex named group <op> 의 값 (예: 'findOne', 's', 'select')")
    crud: Literal["READ", "WRITE"] = Field(..., description="이 op 의 R/W 분류")


class TableAccessPattern(BaseModel):
    """함수 → 테이블 접근 정규식 매칭 패턴 (Linking 1 단계 결정론 grep 용).

    ``call_regex`` 의 named group:
    - ``<op>``: CRUD 동사 (find/save/update/...)
    - ``<entity>``: 테이블/엔티티 이름

    ``crud_mappings`` 의 각 ``CrudMapping`` 으로 op 값 → READ/WRITE 매핑.
    """
    id: str = Field(..., description="패턴 식별자 (예: 'ofbiz_delegator')")
    call_regex: str = Field(..., description="named group <op> + <entity> 포함")
    crud_mappings: list[CrudMapping] = Field(...)
    entity_source: str = Field(..., description="entity 추출 위치 (예: 'first_arg_string')")


class DdlTransform(BaseModel):
    """엔티티 이름 → DDL 테이블 이름 변환 규칙."""
    ddl_naming_rule: str = Field(
        ..., description="예: 'as_is' / 'camel_to_snake_upper' / 'snake_to_camel'"
    )
    fallback_prefixes: list[str] = Field(default_factory=list)


class ScanResult(BaseModel):
    """Scan Agent 1회 invoke 의 최종 출력."""
    matched_skill: Optional[str] = Field(None, description="매칭된 SKILL 이름 또는 null")
    confidence: float = Field(..., ge=0.0, le=1.0)
    evidence: str = Field(..., description="자연어 단서")
    evidence_locations: list[str] = Field(default_factory=list, description="['path:line', ...]")

    # 결정론 grep 용
    table_access_patterns: list[TableAccessPattern] = Field(default_factory=list)
    ddl_transform: DdlTransform = Field(
        default_factory=lambda: DdlTransform(ddl_naming_rule="as_is")
    )

    # 분석용 텍스트 (step6_analyze_function 컨텍스트 + 잔여 패턴 루프 prefix)
    codebase_notes: str = Field("", description="프로젝트 성격 / wrapper / 명명 컨벤션 1단락")

    # 호출 분류용 — 프레임워크/런타임 함수 이름 (skill 문서에서 읽음). 호출 그래프에서 드롭.
    framework_functions: list[str] = Field(
        default_factory=list,
        description="프레임워크/런타임 함수 이름 (업무 로직 아님 — 호출 분류 시 드롭)",
    )

    no_match_reason: Optional[str] = None


# ═══════════════════════════════════════════════════════════════
# 잔여 패턴 루프 출력
# ═══════════════════════════════════════════════════════════════
#
# step4 의 결정론 R/W 링킹이 못 푼 "잔여" 테이블 케이스를 — 케이스마다 추적하지
# 않고, 샘플에서 공통 패턴을 찾아 코드가 일괄 적용한다. 이 모델은 그 루프 한
# 라운드에서 에이전트가 발견한 패턴이다.


class ResidualPatternResult(BaseModel):
    """잔여 패턴 루프 — 에이전트 1라운드의 출력.

    잔여 미해결 테이블 케이스 샘플에서 발견한 공통 접근 패턴. ``ScanResult`` 와
    구성 요소(``TableAccessPattern`` / ``DdlTransform``)는 공유하되 — 잔여 모드는
    skill 매칭·codebase 서술이 없으므로 필드를 분리해 별도 모델로 둔다.
    """
    table_access_patterns: list[TableAccessPattern] = Field(
        default_factory=list,
        description="잔여 케이스의 공통 접근 패턴 (verify_table_regex 통과분)",
    )
    ddl_transform: DdlTransform = Field(
        default_factory=lambda: DdlTransform(ddl_naming_rule="as_is"),
        description="잔여 케이스에 맞는 entity→DDL 테이블명 변환 규칙",
    )
    no_pattern_reason: Optional[str] = Field(
        None, description="공통 패턴을 못 찾았으면 사유. 찾았으면 null",
    )
    notes: str = Field("", description="추적 흐름·의심 패턴 등 자유 서술 (사람 검토용)")
