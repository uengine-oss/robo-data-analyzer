"""단순 LLM 호출 (Agent 아닌 것) 의 Pydantic 출력 스키마.

3 분할 fan-out + 모듈 요약 + Actor 통합 + 테이블 description + DML 메타 추출 등.
각 yaml 의 ``response_format`` 으로 강제 — 자유 텍스트 응답 X.

사용처:
- ``analysis_flow/step6_analyze_function``: SummaryOutput / TableInsightsOutput / RulesExamplesOutput
- ``analysis_flow/step7_post_processing``: ModuleSummaryOutput / ActorMergeOutput /
  TableDescription / DmlExtractOutput

검증 규칙 (Pydantic validator) — Plan 명세 강제:
- ``logical_name`` ≤ 25자
- ``summary`` 3 섹션 마크다운 형식 (``**핵심**`` 포함) — 위반 시 LLM 재시도 유도
"""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


# ── 3 분할 #1: function_summary ───────────────────────────


class SummaryOutput(BaseModel):
    """함수 의미 요약 — 3 섹션 마크다운 + 메타."""
    summary: str = Field(..., description="3 섹션 마크다운 (핵심/비즈니스로직/반환)")
    stereotype: str = Field("", description="ontology_roles.yaml 풀에서 선택")
    actor: str = Field("", description="비즈니스 actor 또는 빈 문자열")
    logical_name: str = Field(
        "",
        max_length=25,
        description="짧은 도메인 토큰 (≤ 25자)",
    )

    @field_validator("summary")
    @classmethod
    def _validate_summary_format(cls, v: str) -> str:
        """3 섹션 마크다운 형식 검증 — ``**핵심**`` 헤더 포함 필수."""
        if not v:
            return v
        if "**핵심**" not in v and "핵심:" not in v:
            raise ValueError(
                "summary 는 '**핵심**:' 으로 시작하는 3 섹션 마크다운이어야 함"
            )
        return v


# ── 3 분할 #2: function_table_insights ────────────────────


class TableInsightItem(BaseModel):
    """함수가 한 테이블에 대해 갖는 역할 + 컬럼 description."""
    table: str = Field(..., description="db.schema.table fqn 또는 table 이름")
    role: str = Field(..., description="예: 'READ-only' / 'WRITE-then-READ' / 'WRITE-only'")
    columns: list[dict] = Field(
        default_factory=list,
        description="[{name, description}, ...] — 이 함수 관점에서 컬럼 의미"
    )


class TableInsightsOutput(BaseModel):
    table_insights: list[TableInsightItem] = Field(default_factory=list)
    questions: list[str] = Field(
        default_factory=list,
        description="사람 검토 큐로 갈 질문"
    )


# ── 3 분할 #3: function_rules_examples ────────────────────


class RuleItem(BaseModel):
    """비즈니스 규칙 1개 + 코드 예시들."""
    rule: str = Field(..., description="비즈니스 규칙 (1~2 문장)")
    examples: list[str] = Field(default_factory=list, description="코드 라인 또는 분기 예시")


class RulesExamplesOutput(BaseModel):
    rules: list[RuleItem] = Field(default_factory=list)


# ── 모듈 요약 (summarize_module_step) ─────────────────────


class ModuleSummaryOutput(BaseModel):
    summary: str = Field(..., description="모듈 전체 의미 (~2 단락)")
    module_stereotype: str = Field("", description="예: 'Service' / 'Repository' / 'Controller'")
    logical_name: str = Field("", max_length=25, description="짧은 도메인 토큰")


# ── Actor 통합 (Plan §4 M5 — Actor 통합) ──────────────────


class ActorGroup(BaseModel):
    actor_id: str = Field(..., max_length=25)
    display_name: str
    member_function_ids: list[str] = Field(default_factory=list)


class ActorMergeOutput(BaseModel):
    actors: list[ActorGroup] = Field(default_factory=list)


# ── 테이블 description 통합 (enrich_table_description_step) ──


class TableDescription(BaseModel):
    db: str
    schema_: str = Field(..., alias="schema")
    name: str
    description: str

    class Config:
        populate_by_name = True


class TableDescriptionsOutput(BaseModel):
    tables: list[TableDescription] = Field(default_factory=list)


# ── DBMS dml_extract (extract_dml_metadata_step) ──────────


class DmlTableRef(BaseModel):
    table: str
    relation: str       # READS / WRITES
    columns: list[str] = Field(default_factory=list)


class DmlRangeOutput(BaseModel):
    range_id: str
    tables: list[DmlTableRef] = Field(default_factory=list)
    fk_relations: list[dict] = Field(default_factory=list)
    db_links: list[str] = Field(default_factory=list)


class DmlExtractOutput(BaseModel):
    ranges: list[DmlRangeOutput] = Field(default_factory=list)
