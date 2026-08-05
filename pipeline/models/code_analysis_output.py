"""단순 LLM 호출 (Agent 아닌 것) 의 Pydantic 출력 스키마.

3 분할 fan-out + 모듈 요약 + 테이블 description 등.
각 yaml 의 ``response_format`` 으로 강제 — 자유 텍스트 응답 X.

사용처:
- ``pipeline/steps/step7_analyze_code``: SummaryOutput / TableInsightsOutput / RulesExamplesOutput
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
    NL_PATTERN,
    RULE_LEAF_PATTERN,
)


def _normalize_summary_format(v: str) -> str:
    """의미 내용은 보존하고 옛 표시 마커·빈 줄·주변 공백만 정리한다."""
    if not v:
        return v
    v = v.replace("[핵심]", " ").strip()
    lines = [ln.strip() for ln in v.splitlines() if ln.strip()]
    return "\n".join(lines)


# ── 3 분할 #1: code_summary ───────────────────────────


class SummaryOutput(BaseModel):
    """모델이 소유하는 최소 의미 헤더; 구조 사실은 코드가 별도로 렌더링한다."""
    summary: str = Field(
        ...,
        min_length=1,
        description="대상의 최종 책임만 설명하는 purpose; 분기·코드값을 다시 쓰지 않음",
    )
    stereotype: str = Field("", description="ontology_roles.yaml 풀에서 선택")
    questions: list["QuestionItem"] = Field(
        default_factory=list,
        max_length=15,
        description="코드와 제공 근거만으로 답할 수 없는 실제 질문",
    )

    @field_validator("summary")
    @classmethod
    def _validate_summary_format(cls, v: str) -> str:
        return _normalize_summary_format(v)

    @model_validator(mode="after")
    def _require_grounded_text(self) -> "SummaryOutput":
        if not self.summary.strip():
            raise ValueError("summary cannot be empty")
        return self


# ── 3 분할 #2: code_table_insights ────────────────────


class ColumnInsight(BaseModel):
    """코드노드 관점 컬럼 의미 1건 — 소비처(enrich_table_description)가 name·logical_name·description 사용.

    ``usage`` 는 생성돼도 소비 0(전수 grep 확정) → 응답 스키마에서 제외(불필요 토큰·폭주면적 차단).
    ``logical_name`` = 테이블 logical_name(spec 025) 미러 — 짧은 업무 표시명(spec 028).
    """
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="DDL 컬럼명 정확히 (추측 X)")
    logical_name: str = Field("", description="컬럼 도메인 표시명 한 줄 (코드 식별자·DDL 타입·동어반복 X)")
    description: str = Field("", description="비즈니스 의미 한 줄 (DDL 타입 X / 동어반복 X)")


class TableInsightItem(BaseModel):
    """코드노드가 한 테이블에 대해 갖는 직접 역할 + 컬럼 description."""
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
        description="주입된 해당 테이블 허용 컬럼을 정확히 한 번씩 모두 반환한 함수 관점 의미",
    )


class TableInsightsOutput(BaseModel):
    table_insights: list[TableInsightItem] = Field(default_factory=list)


# ── 3 분할 #3: code_rules_examples — 구조화 파이썬 표현식 leaf (spec 116) ──


class QuestionItem(BaseModel):
    """Meaning-critical missing context emitted only by the summary concern."""
    model_config = ConfigDict(extra="forbid")

    text: str = Field(..., description="한 줄 질문")
    reason: str = Field(..., description="코드 근거 부재 사유")


# ── 구조화 Rule·Example — JSON 봉투 + 파이썬 표현식 leaf (spec 116 3단계) ──
#
# 스키마는 **형태만** 강제한다(개행 금지·길이 상한 pattern = 문법 강제 디코딩의 끝 경계,
# 헌법 VI 절제·폭주 방어). 파이썬 문법·어휘·분기표 의미 대조는 채점기(rule_grading)가
# 한 번 수행한다. 위반 후보는 행 좌표가 있는 finding으로 격리하고 검증된 sibling은
# 보존한다. 어휘·pattern의 정의처는 pipeline/models/rule_expression.py 하나다.

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
    """Canonical Rule after the outer required key restores its anchor coordinate."""

    model_config = ConfigDict(extra="forbid")

    anchor_line: int = Field(
        ..., strict=True, gt=0,
        description="분기표 행 번호 — wire의 R<행> key에서 코드가 복원",
    )
    cond: _RuleLeafStr | None = Field(
        None,
        description='파이썬 불리언 표현식 leaf. 분기 아님(직선 코드)이면 null',
    )
    then: list[_RuleLeafStr] = Field(
        default_factory=list, description="효과 호출문 목록 — 유일한 채점 대상"
    )
    # examples 는 LLM 이 내지 않는다 — 결정론 합성 (spec 116 §21, 헌법 I).
    # 개방형 자연어가 마지막이어도 NL_PATTERN 길이 상한이 결정론 끝 경계를 준다.
    nl: str = Field(
        "", pattern=NL_PATTERN, description="사람용 한국어 한 문장 — 표시 전용, 채점 제외"
    )


class RulesExamplesOutput(BaseModel):
    """Rule 봉투 — 개수 상한 없음(유한성은 분기표 행수+토큰예산+반복가드가 보증)."""

    rules: list[RuleItem] = Field(default_factory=list)


# ── DBMS 배치 3분할 (🅕 — 같은 유효부모 형제 N 노드를 관심사별 1콜) ──
# 3관심사(요약/테이블/비즈니스로직)를 각각 배치(노드 리스트)로 호출 → 각 콜은
# "한 관심사 × N 노드"의 평탄 리스트 = 깊은 중첩 0 (한 콜에 4중첩 합본 → 폭주 차단).
# framework per-node 3분할과 동일 관심사·동일 항목(RuleItem/
# TableInsightItem 재사용), 다른 점은 노드 리스트로 묶어 던지는 것뿐. node_key(줄범위)로 분배.


class BatchSummaryItem(BaseModel):
    """배치 노드의 최소 의미 헤더."""
    node_key: str = Field(..., description="입력 줄범위 '<start>-<end>' 그대로 echo")
    summary: str = Field(
        ...,
        min_length=1,
        description="노드의 최종 책임만 설명하는 purpose; 분기·코드값을 다시 쓰지 않음",
    )
    stereotype: str = Field("", description="ontology_roles.yaml 풀에서 선택")
    questions: list[QuestionItem] = Field(
        default_factory=list,
        max_length=15,
        description="코드와 제공 근거만으로 답할 수 없는 실제 질문",
    )

    @field_validator("summary")
    @classmethod
    def _validate_summary_format(cls, v: str) -> str:
        return _normalize_summary_format(v)

    @model_validator(mode="after")
    def _require_grounded_text(self) -> "BatchSummaryItem":
        if not self.summary.strip():
            raise ValueError("summary cannot be empty")
        return self


class BatchSummaryOutput(BaseModel):
    """요약 배치 결과 — 입력 노드와 1:1; 키 불일치 후보는 concern finding으로 격리된다."""
    nodes: list[BatchSummaryItem] = Field(default_factory=list)


class BatchRulesItem(BaseModel):
    """배치 규칙 계약 — 노드 1개의 파이썬 leaf 규칙 (spec 116 5단계).

    canonical 모델은 동적 required-key schema 검증 뒤의 정규화 결과만 받는다.
    규칙 내용은 RuleItem(anchor 복창 + leaf)이며 상한은 없다 —
    유한성은 분기표 행수+토큰예산+반복가드(결정 원장).
    """
    node_key: str = Field(..., description="입력 줄범위 '<start>-<end>' 그대로 echo")
    rules: list[RuleItem] = Field(default_factory=list)


class BatchRulesOutput(BaseModel):
    nodes: list[BatchRulesItem] = Field(default_factory=list)


class BatchTableInsightsItem(BaseModel):
    """배치 테이블 분할 — 노드 1개의 테이블 인사이트 (DML 노드만 대상)."""
    node_key: str = Field(..., description="입력 줄범위 '<start>-<end>' 그대로 echo")
    table_insights: list[TableInsightItem] = Field(default_factory=list)


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


# ── 컴포넌트 이름 (build_components_step) ─────────────────


class ComponentNamingOutput(BaseModel):
    """코드노드 군집의 사용자용 이름. 실제 저장 경로가 소비하는 단일 출력."""
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, description="부품 도메인 이름 한 줄 (코드 식별자 X)")


# ── 테이블 description 통합 (enrich_table_description_step) ──


class TableDescription(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    db: str
    schema_: str = Field(..., alias="schema")
    name: str
    logical_name: str = Field(...)   # 짧은 도메인 표시명(물리명과 별개, 근거없으면 빈값) — spec 025
    description: str
    columns: list[ColumnInsight] = Field(
        ...,
        description="입력 허용 컬럼별 통합 논리명·설명. 이름은 입력 컬럼을 정확히 한 번씩 보존",
    )

# (옛 ``TableDescriptionsOutput`` 삭제 — 참조 0건. 실제 response_model 은 단수형
#  ``TableDescription`` (enrich_table_description_step). spec 046 FR-010.)
