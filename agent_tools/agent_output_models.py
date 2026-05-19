"""Agent 출력 Pydantic 스키마 — Profile / Linking Agent 의 ``response_format`` 강제.

LLM 이 자유 텍스트 응답 못 함. 다운스트림 파싱 부담 X — 환각 차단.

운영 정책 ``ROBO_LLM_REASONING_EFFORT=none`` 과 결합하면 더 강력.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════
# Profile Agent 출력 (analysis_flow/step3_profile_project)
# ═══════════════════════════════════════════════════════════════


class TableAccessPattern(BaseModel):
    """함수 → 테이블 접근 정규식 매칭 패턴 (Linking 1 단계 결정론 grep 용).

    ``call_regex`` 의 named group:
    - ``<op>``: CRUD 동사 (find/save/update/...)
    - ``<entity>``: 테이블/엔티티 이름

    ``crud_map`` 으로 op 값 → READ/WRITE 매핑.
    """
    id: str = Field(..., description="패턴 식별자 (예: 'ofbiz_delegator')")
    call_regex: str = Field(..., description="named group <op> + <entity> 포함")
    crud_map: dict[str, Literal["READ", "WRITE"]] = Field(...)
    entity_source: str = Field(..., description="entity 추출 위치 (예: 'first_arg_string')")


class DdlTransform(BaseModel):
    """엔티티 이름 → DDL 테이블 이름 변환 규칙."""
    ddl_naming_rule: str = Field(
        ..., description="예: 'as_is' / 'camel_to_snake_upper' / 'snake_to_camel'"
    )
    fallback_prefixes: list[str] = Field(default_factory=list)


class ProfileResult(BaseModel):
    """Profile Agent 1회 invoke 의 최종 출력."""
    matched_skill: Optional[str] = Field(None, description="매칭된 SKILL 이름 또는 null")
    confidence: float = Field(..., ge=0.0, le=1.0)
    evidence: str = Field(..., description="자연어 단서")
    evidence_locations: list[str] = Field(default_factory=list, description="['path:line', ...]")

    # 결정론 grep 용
    table_access_patterns: list[TableAccessPattern] = Field(default_factory=list)
    ddl_transform: DdlTransform = Field(
        default_factory=lambda: DdlTransform(ddl_naming_rule="as_is")
    )

    # 분석용 텍스트 (step6_analyze_function 컨텍스트 + Linking Agent prefix)
    codebase_notes: str = Field("", description="프로젝트 성격 / wrapper / 명명 컨벤션 1단락")

    no_match_reason: Optional[str] = None


# ═══════════════════════════════════════════════════════════════
# Linking Agent 출력 (analysis_flow/step4_linking/stage3_resolve_unresolved)
# ═══════════════════════════════════════════════════════════════


class AttemptResult(BaseModel):
    """unresolved 케이스 1개에 대한 정규식 시도 결과."""
    pattern_id: str
    regex: str
    result: Literal["no_match", "matched"]
    groups: Optional[dict[str, str]] = None
    ddl_lookup_result: Optional[str] = None       # "not_found" / "found:TABLE_X"


class UnresolvedCase(BaseModel):
    """Linking Agent 가 받는 unresolved 케이스 1개 (입력)."""
    case_id: str
    file: str
    line: int
    call_text: str
    function_id: str
    attempted_patterns: list[AttemptResult] = Field(default_factory=list)


class LinkingAgentResult(BaseModel):
    """Linking Agent invoke 의 최종 출력."""
    case_id: str
    resolved: bool
    confidence: float = Field(..., ge=0.0, le=1.0)
    edges_created: list[str] = Field(default_factory=list)
    diagnosis: Optional[Literal[
        "variable_alias",
        "wrapper_unwrap",
        "virtual_dispatch",
        "company_dao_box",
        "dynamic_sql",
        "unknown",
    ]] = None
    notes: Optional[str] = None
