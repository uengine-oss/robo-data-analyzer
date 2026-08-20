"""단순 LLM 호출 (Agent 아닌 것) 의 Pydantic 출력 스키마.

3 분할 fan-out + 모듈 요약 + 테이블 description 등.
각 yaml 의 ``response_format`` 으로 강제 — 자유 텍스트 응답 X.

사용처:
- ``pipeline/steps/step7_analyze_code``: SummaryOutput / TableInsightsOutput 및 결정론 Rule projection
- ``pipeline/steps/step8_post_processing``: ModuleSummaryOutput / TableDescription

호출 모드 (단일 진실):
runner가 이 모델의 canonical JSON Schema를 provider 구조화 출력에 전달하고, raw/usage를
확보한 뒤 이 Pydantic 모델로 내부 의미 계약을 검증한다.
자유 키와 깊고 상한 없는 반복 구조는 SGLang+xgrammar에서 반복 폭주를 일으켰다. 현재 wire는
코드가 확정한 유한 좌표를 required key로 만들고 공통 leaf schema를 한 번만 참조한다. Example은
모델 출력이 아니라 accepted Rule의 제한된 Python 표기에서 파생한다.

검증 규칙 (Pydantic validator):
- ``summary`` 내용은 후처리가 줄이거나 다시 쓰지 않는다. 옛 ``[핵심]`` 표시와 주변 공백만
  제거하며, 길이와 책임 경계는 최초 생성 prompt가 담당한다.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from pipeline.models.rule_expression import (
    EXAMPLE_LEAF_PATTERN,
    RULE_LEAF_PATTERN,
)
from pipeline.models.semantic_frame import SemanticFrame


def _normalize_summary_format(v: str) -> str:
    """의미 내용은 보존하고 옛 표시 마커·빈 줄·주변 공백만 정리한다."""
    if not v:
        return v
    v = v.replace("[핵심]", " ").strip()
    lines = [ln.strip() for ln in v.splitlines() if ln.strip()]
    return "\n".join(lines)


# ── 3 분할 #1: code_summary ───────────────────────────

EvidenceStatus = Literal["sufficient", "partial", "insufficient"]


def _validate_evidence_state(
    *, summary: str, stereotype: str, evidence_status: EvidenceStatus,
    missing_context: list[str],
) -> None:
    """Keep prompt, wire schema and every consumer on one no-fabrication contract."""
    has_summary = bool(summary.strip())
    has_stereotype = bool(stereotype.strip())
    has_missing = bool(missing_context)
    if evidence_status == "sufficient":
        if not has_summary or not has_stereotype or has_missing:
            raise ValueError(
                "sufficient requires summary+stereotype and empty missing_context"
            )
    elif evidence_status == "partial":
        if not has_summary or not has_stereotype or not has_missing:
            raise ValueError(
                "partial requires grounded summary+stereotype and missing_context"
            )
    elif has_summary or has_stereotype or not has_missing:
        raise ValueError(
            "insufficient requires empty summary+stereotype and non-empty missing_context"
        )


class SummaryOutput(BaseModel):
    """모델이 소유하는 전체 흐름 요약과 입력 충분성 판정."""
    summary: str = Field(
        ...,
        description=(
            "입력·검증·주요 조건별 처리·CALL/RW·반환/상태 변화의 사람이 읽는 전체 흐름; "
            "insufficient이면 빈 문자열"
        ),
    )
    stereotype: str = Field("", description="ontology_roles.yaml 풀에서 선택")
    evidence_status: EvidenceStatus = Field(
        ..., description="현재 입력만으로 전체 의미를 확정할 수 있는 정도",
    )
    missing_context: list[str] = Field(
        default_factory=list,
        description="partial/insufficient 판단을 해소하는 데 필요한 구체적 근거",
    )
    questions: list["QuestionItem"] = Field(
        default_factory=list,
        description="코드와 제공 근거만으로 답할 수 없는 실제 질문",
    )
    semantic_frame: SemanticFrame | None = Field(default=None, exclude=True)

    @field_validator("summary")
    @classmethod
    def _validate_summary_format(cls, v: str) -> str:
        return _normalize_summary_format(v)

    @model_validator(mode="after")
    def _require_grounded_text(self) -> "SummaryOutput":
        _validate_evidence_state(
            summary=self.summary,
            stereotype=self.stereotype,
            evidence_status=self.evidence_status,
            missing_context=self.missing_context,
        )
        return self


# ── 3 분할 #2: code_table_insights ────────────────────


class ColumnUsageSourceLine(BaseModel):
    """Deterministically selected absolute source line for one column use."""

    model_config = ConfigDict(extra="forbid")

    line: int = Field(..., gt=0)
    text: str = Field(..., min_length=1)
    source_id: str = Field("", description="이 줄을 문법적으로 소유한 full code node id")
    ownership: str = Field("", description="qualified/lexical/derived def-use 근거 종류")


class ColumnInsight(BaseModel):
    """Deterministic code-node → physical-column coordinate evidence.

    Column meaning is generated only once by the final TABLE/COLUMN concern.  The intermediate
    table-use model never writes ``usage_summary``; identity, ownership, coordinates and source
    lines are attached by the deterministic response normalizer.
    """
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, description="DDL 컬럼명 정확히")
    usage_summary: str = Field(
        "",
        description="호환 필드. 중간 단계에서는 비우고 최종 TABLE/COLUMN 모델이 원문에서 의미 생성",
    )
    ownership: Literal["qualified", "sole_direct_table", "ddl_unique_in_scope"]
    evidence_lines: list[int] = Field(..., min_length=1)
    source_lines: list[ColumnUsageSourceLine] = Field(..., min_length=1)


class GroundedColumnDescription(BaseModel):
    """Final catalog column meaning with an explicit evidence-sufficiency outcome."""
    model_config = ConfigDict(extra="forbid")

    name: str
    logical_name: str = ""
    description: str = ""
    evidence_status: EvidenceStatus
    missing_context: list[str] = Field(default_factory=list)
    semantic_frame: SemanticFrame | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def _validate_evidence_state(self) -> "GroundedColumnDescription":
        has_meaning = bool(self.logical_name.strip() or self.description.strip())
        if self.evidence_status == "sufficient" and (not has_meaning or self.missing_context):
            raise ValueError("sufficient column meaning requires evidence text and no missing_context")
        if self.evidence_status == "partial" and (not has_meaning or not self.missing_context):
            raise ValueError("partial column meaning requires evidence text and missing_context")
        if self.evidence_status == "insufficient" and (has_meaning or not self.missing_context):
            raise ValueError("insufficient column meaning must stay empty and name missing context")
        return self


class TableInsightItem(BaseModel):
    """코드노드가 한 테이블에 대해 갖는 직접 역할 + 결정론 컬럼 좌표."""
    model_config = ConfigDict(extra="forbid")

    table: str = Field(..., description="db.schema.table fqn 또는 table 이름")
    role: Literal[
        "READ-only",
        "WRITE-only",
        "READ_WRITE",
        "append-only",
        "lookup",
        "upsert",
    ] = Field(..., description="직접 R/W로 확정하는 폐쇄형 테이블 접근 역할")
    usage_summary: str = Field(
        ...,
        min_length=1,
        description="현재 코드노드가 이 테이블에 직접 수행하는 조회·쓰기·판단 효과 한 줄. 다른 테이블 효과 금지",
    )
    columns: list[ColumnInsight] = Field(
        default_factory=list,
        description="LLM 의미가 아닌 주입된 해당 테이블 컬럼의 결정론 좌표 근거",
    )


class TableInsightsOutput(BaseModel):
    table_insights: list[TableInsightItem] = Field(default_factory=list)
    semantic_frame: SemanticFrame | None = Field(default=None, exclude=True)


# ── 결정론 Rule/Example projection ──


class QuestionItem(BaseModel):
    """Meaning-critical missing context emitted only by the summary concern."""
    model_config = ConfigDict(extra="forbid")

    text: str = Field(..., description="한 줄 질문")
    reason: str = Field(..., description="코드 근거 부재 사유")


# ── 구조화 Rule·Example — JSON 봉투 + 파이썬 표현식 leaf (spec 116 3단계) ──
#
# 스키마는 **형태만** 강제한다(개행 금지). Rule과 Example은 모두 parser-owned facts에서
# 결정론 생성되므로 source predicate를 길이로 자르거나 버리지 않는다. 파이썬 문법·어휘·
# 분기표 의미 대조는 채점기(rule_grading)가 한 번 수행한다. 위반 후보는 행 좌표가 있는
# finding으로 격리하고 검증된 sibling은 보존한다. 어휘·pattern의 정의처는
# pipeline/models/rule_expression.py 하나다.

_ExampleLeafStr = Annotated[str, StringConstraints(pattern=EXAMPLE_LEAF_PATTERN)]
_RuleLeafStr = Annotated[str, StringConstraints(pattern=RULE_LEAF_PATTERN)]


class ExampleItem(BaseModel):
    """Rule 검증 시나리오 — given/then 전부 파이썬 leaf (자연어 금지).

    **LLM 출력이 아니다.** 전체 run 실측에서 모델이 낸 예시의 71.9% 가 cond/then 의
    기계적 재진술(새 정보 0)이었고 나머지도 여집합 부정이거나 창작이었다 — 헌법 I 에
    따라 `quality_gates.example_synthesis` 가 결정론으로 만든다 (spec 116 §21).
    이 모델은 그 산출물의 형태 계약이며 DBMS 배치 경로도 같은 것을 쓴다.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["satisfy"]
    given: list[_ExampleLeafStr] = Field(
        default_factory=list,
        description="파이썬 등식 목록 — cond 를 만족시키는 한 경로",
    )
    then: list[_ExampleLeafStr] = Field(
        default_factory=list, description="그때의 효과 호출문 목록"
    )


class RuleItem(BaseModel):
    """Canonical parser-owned Rule with deterministic condition/effects."""

    model_config = ConfigDict(extra="forbid")

    anchor_line: int = Field(
        ..., strict=True, gt=0,
        description="분기표의 절대 파일 행 좌표",
    )
    cond: _RuleLeafStr | None = Field(
        None,
        description='파이썬 불리언 표현식 leaf. 분기 아님(직선 코드)이면 null',
    )
    then: list[_RuleLeafStr] = Field(
        default_factory=list, description="효과 호출문 목록 — 유일한 채점 대상"
    )
    # Rule prose는 생성하지 않는다. 검색/화면은 cond/then을 구조적으로 렌더한다.
    nl: Literal[""] = Field("", description="빈 호환 필드 — 의미 정본이 아님")


class RulesExamplesOutput(BaseModel):
    """Deterministic Rule collection; finite by branch-inventory obligations."""

    model_config = ConfigDict(extra="forbid")

    rules: list[RuleItem] = Field(default_factory=list)


# ── DBMS 배치 3분할 (🅕 — 같은 유효부모 형제 N 노드를 관심사별 1콜) ──
# 3관심사(요약/테이블/비즈니스로직)를 각각 배치(노드 리스트)로 호출 → 각 콜은
# "한 관심사 × N 노드"의 평탄 리스트 = 깊은 중첩 0 (한 콜에 4중첩 합본 → 폭주 차단).
# framework per-node 3분할과 동일 관심사·동일 항목(RuleItem/
# TableInsightItem 재사용), 다른 점은 노드 리스트로 묶어 던지는 것뿐. node_key(줄범위)로 분배.


class BatchSummaryItem(BaseModel):
    """배치 노드의 전체 흐름 요약과 입력 충분성 판정."""
    node_key: str = Field(..., description="입력 줄범위 '<start>-<end>' 그대로 echo")
    summary: str = Field(
        ...,
        description=SummaryOutput.model_fields["summary"].description,
    )
    stereotype: str = Field("", description="ontology_roles.yaml 풀에서 선택")
    evidence_status: EvidenceStatus = Field(
        ..., description=SummaryOutput.model_fields["evidence_status"].description,
    )
    missing_context: list[str] = Field(
        default_factory=list,
        description=SummaryOutput.model_fields["missing_context"].description,
    )
    questions: list[QuestionItem] = Field(
        default_factory=list,
        description="코드와 제공 근거만으로 답할 수 없는 실제 질문",
    )
    semantic_frame: SemanticFrame | None = Field(default=None, exclude=True)

    @field_validator("summary")
    @classmethod
    def _validate_summary_format(cls, v: str) -> str:
        return _normalize_summary_format(v)

    @model_validator(mode="after")
    def _require_grounded_text(self) -> "BatchSummaryItem":
        _validate_evidence_state(
            summary=self.summary,
            stereotype=self.stereotype,
            evidence_status=self.evidence_status,
            missing_context=self.missing_context,
        )
        return self


class BatchSummaryOutput(BaseModel):
    """요약 배치 결과 — 입력 노드와 1:1; 키 불일치 후보는 concern finding으로 격리된다."""
    nodes: list[BatchSummaryItem] = Field(default_factory=list)


class BatchRulesItem(BaseModel):
    """One DBMS node's deterministically assigned routine Rule rows."""
    model_config = ConfigDict(extra="forbid")

    node_key: str = Field(..., description="입력 줄범위 '<start>-<end>' 그대로 echo")
    rules: list[RuleItem] = Field(default_factory=list)


class BatchTableInsightsItem(BaseModel):
    """배치 테이블 분할 — 노드 1개의 테이블 인사이트 (DML 노드만 대상)."""
    node_key: str = Field(..., description="입력 줄범위 '<start>-<end>' 그대로 echo")
    table_insights: list[TableInsightItem] = Field(default_factory=list)
    semantic_frame: SemanticFrame | None = Field(default=None, exclude=True)


class BatchTableInsightsOutput(BaseModel):
    nodes: list[BatchTableInsightsItem] = Field(default_factory=list)


# ── 모듈 사전분석 (module_pre_analysis — 코드노드 분석 전 1회) ──


class ModulePreAnalysisOutput(BaseModel):
    """모듈 헤더 주석 → 정제된 모듈 의도(intent). 코드노드 분석 3분할 context 에 주입.

    표시명(logical_name)은 여기서 만들지 X — 헤더 주석이 아니라 분석 결과에서
    도출해야 하므로 summarize_module 이 담당 (M11).
    """
    model_config = ConfigDict(extra="forbid")

    intent: str = Field(
        ...,
        description="헤더가 입증하는 현재 모듈 의도 0~2문장. 시그니처만으로 추측하지 않으며 근거 없으면 빈 문자열",
    )


# ── 모듈 요약 (summarize_module_step) ─────────────────────


class ModuleSummaryOutput(BaseModel):
    summary: str = Field(..., min_length=1, description="모듈 전체 의미 (~2 단락)")
    stereotype: str = Field("", description="예: 'Service' / 'Repository' / 'Controller'")
    logical_name: str = Field("", description="모듈 도메인 표시명 한 줄 (코드 식별자 X)")
    semantic_frame: SemanticFrame | None = Field(default=None, exclude=True)


# ── 컴포넌트 이름 (build_components_step) ─────────────────


class ComponentNamingOutput(BaseModel):
    """코드노드 군집의 사용자용 이름. 실제 저장 경로가 소비하는 단일 출력."""
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, description="부품 도메인 이름 한 줄 (코드 식별자 X)")
    semantic_frame: SemanticFrame | None = Field(default=None, exclude=True)


# ── 테이블 description 통합 (enrich_table_description_step) ──


class TableDescription(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    db: str
    schema_: str = Field(..., alias="schema")
    name: str
    logical_name: str = Field(...)   # 짧은 도메인 표시명(물리명과 별개, 근거없으면 빈값) — spec 025
    description: str
    evidence_status: EvidenceStatus
    missing_context: list[str] = Field(default_factory=list)
    columns: list[GroundedColumnDescription] = Field(
        ...,
        description="입력 허용 컬럼별 통합 논리명·설명. 이름은 입력 컬럼을 정확히 한 번씩 보존",
    )
    semantic_frame: SemanticFrame | None = Field(default=None, exclude=True)
    column_semantic_frames: dict[str, SemanticFrame] = Field(default_factory=dict, exclude=True)
    semantic_frame: SemanticFrame | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def _validate_evidence_state(self) -> "TableDescription":
        has_meaning = bool(self.logical_name.strip() or self.description.strip())
        if self.evidence_status == "sufficient" and (not has_meaning or self.missing_context):
            raise ValueError("sufficient table meaning requires evidence text and no missing_context")
        if self.evidence_status == "partial" and (not has_meaning or not self.missing_context):
            raise ValueError("partial table meaning requires evidence text and missing_context")
        if self.evidence_status == "insufficient" and (has_meaning or not self.missing_context):
            raise ValueError("insufficient table meaning must stay empty and name missing context")
        return self

# (옛 ``TableDescriptionsOutput`` 삭제 — 참조 0건. 실제 response_model 은 단수형
#  ``TableDescription`` (enrich_table_description_step). spec 046 FR-010.)
