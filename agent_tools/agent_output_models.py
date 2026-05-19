"""Agent 출력 Pydantic 스키마 — Profile / Linking Agent 의 ``response_format`` 강제.

LLM 이 자유 텍스트 응답 못 함. 다운스트림 파싱 부담 X — 환각 차단.

운영 정책 ``ROBO_LLM_REASONING_EFFORT=none`` 과 결합하면 더 강력.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════
# Profile Agent — 탐색/결정 분리
# ═══════════════════════════════════════════════════════════════
#
# 단일 invoke 안 결정 turn 의 컨텍스트가 누적 도구 결과 5만 토큰 + 지침 다 보고
# 결정하던 패턴 → 환각 위험 ↑ + 토큰 폭증. 두 단계로 쪼갬:
#
#   explorer (create_agent + PROFILE_TOOLS)
#      ↓ ProfileFindings (탐색 사실 종합)
#   profile_decide.yaml (도구 X, 결정 전담)
#      ↓ ProfileResult
#
# explorer 의 누적 컨텍스트는 invoke 안에 갇힘. 결정 LLM 입력 = Findings dict 만.


class ProfileFindings(BaseModel):
    """Profile Agent 의 explorer 단계 출력 — 탐색 사실 종합.

    결정 LLM (``profile_decide.yaml``) 의 입력. 도구 결과 누적 X — 핵심 사실만.
    스키마 풍부하게 (telephone game 방지) — 결정 LLM 이 필요한 모든 단서 포함.
    """
    language: str = Field(..., description="주 언어: 'C' / 'Java' / 'Python' / ...")
    framework_hints: list[str] = Field(
        default_factory=list,
        description="프레임워크 단서. 예: ['proframe', 'pfmdbio']",
    )
    db_access_hints: list[str] = Field(
        default_factory=list,
        description="DB 접근 함수/매크로 후보. 예: ['mpfm_dbio()', 'EXEC SQL']",
    )
    sample_files: list[str] = Field(
        default_factory=list,
        description="정독한 파일 (project_root 상대 경로)",
    )
    sample_functions: list[str] = Field(
        default_factory=list,
        description="본 함수명 (signature 일부 포함 가능)",
    )
    sample_patterns: list[str] = Field(
        default_factory=list,
        description="grep / read_file_range 로 본 패턴 (코드 일부 인용)",
    )
    matched_skill_name: Optional[str] = Field(
        None,
        description="SKILL 매칭 시도 결과 — read_skill 호출했으면 그 SKILL 이름",
    )
    evidence_locations: list[str] = Field(
        default_factory=list,
        description="'path:line' ≥ 3. 결정 LLM 의 ProfileResult.evidence_locations 그대로 사용",
    )
    notes: str = Field(
        "",
        description="자유 서술 (결정 LLM 에 추가 컨텍스트). codebase 성격 / wrapper / 명명 컨벤션",
    )


# ═══════════════════════════════════════════════════════════════
# Profile Agent 최종 출력 (decider 단계 — profile_decide.yaml)
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
# Linking Agent — 탐색/결정 분리
# ═══════════════════════════════════════════════════════════════
#
# Profile 과 같은 패턴. case 별 invoke 안에서 탐색 (bundle_search_lines / ddl_lookup /
# read_file_range / graph_get_*) + 결정 (LinkingAgentResult 출력 + 그래프 등록) 섞임 →
# 결정 LLM 입력 = 누적 도구 결과 + 지침 다 봄. 분리:
#
#   linking_explorer (create_agent + LINKING_EXPLORER_TOOLS)
#      ↓ LinkingFindings (case 추적 사실 종합)
#   linking_decide.yaml (도구 X, 결정 + edges_created 배열 출력)
#      ↓ LinkingAgentResult — 코드가 edges_created 배열 보고 graph_create_* 등록


class LinkingFindings(BaseModel):
    """Linking Agent 의 explorer 단계 출력 — case 추적 사실 종합."""
    case_id: str
    diagnosis_hint: Optional[Literal[
        "variable_alias",
        "wrapper_unwrap",
        "virtual_dispatch",
        "company_dao_box",
        "dynamic_sql",
        "unknown",
    ]] = Field(None, description="case 진단 추정 — 결정 LLM 이 참고")
    candidate_tables: list[str] = Field(
        default_factory=list,
        description="ddl_lookup 으로 매칭된 후보 테이블 fqn",
    )
    candidate_evidence: list[str] = Field(
        default_factory=list,
        description="'path:line' — 인용 가능한 본문 위치. 추측 X",
    )
    related_callers: list[str] = Field(
        default_factory=list,
        description="graph_get_callers 결과 fqn",
    )
    related_callees: list[str] = Field(
        default_factory=list,
        description="graph_get_callees 결과 fqn",
    )
    notes: str = Field(
        "",
        description="자유 서술 — 결정 LLM 이 참고. 추적 흐름 / 의심 패턴 등",
    )


# ═══════════════════════════════════════════════════════════════
# Linking Agent 최종 출력 + 입력
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


class EdgeRegistration(BaseModel):
    """결정 LLM 이 등록 요청한 그래프 엣지 (코드가 graph_create_* 호출).

    decider 가 도구 호출 X (yaml rule 단발) — edges 는 dict 로 출력.
    코드가 dict 보고 ``graph_create_table_access`` / ``graph_create_refer_to`` 호출.
    """
    edge_type: Literal["READS", "WRITES", "REFER_TO"]
    function_id: str
    # READS / WRITES 시
    table: Optional[str] = Field(None, description="테이블 fqn (READS/WRITES)")
    # REFER_TO 시
    target_type: Optional[Literal["sql_statement", "config_section", "unresolved"]] = None
    target_key: Optional[str] = Field(None, description="sql_key / config_key / unresolved 키")
    # 공통
    evidence: str = Field(..., description="'file:line' — 본문에서 인용 가능 위치. 추측 X")


class LinkingAgentResult(BaseModel):
    """Linking Agent invoke 의 최종 출력 (decider 단계).

    ``edges_created`` 는 **등록 요청** — 코드가 받아 ``graph_create_*`` 호출.
    옛 정책 (``list[str]`` = 등록된 id) 에서 변경. 다운스트림 사용처 0 — 안전.
    """
    case_id: str
    resolved: bool
    confidence: float = Field(..., ge=0.0, le=1.0)
    edges_created: list[EdgeRegistration] = Field(default_factory=list)
    diagnosis: Optional[Literal[
        "variable_alias",
        "wrapper_unwrap",
        "virtual_dispatch",
        "company_dao_box",
        "dynamic_sql",
        "unknown",
    ]] = None
    notes: Optional[str] = None
