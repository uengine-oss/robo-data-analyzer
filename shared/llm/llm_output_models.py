"""단순 LLM 호출 (Agent 아닌 것) 의 Pydantic 출력 스키마.

3 분할 fan-out + 모듈 요약 + 테이블 description 등.
각 yaml 의 ``response_format`` 으로 강제 — 자유 텍스트 응답 X.

사용처:
- ``analysis_flow/step6_analyze_function``: SummaryOutput / TableInsightsOutput / RulesExamplesOutput
- ``analysis_flow/step7_post_processing``: ModuleSummaryOutput / TableDescription

**strict 호환 (OpenAI structured output 정책)**:
자유 키 ``dict`` 와 ``list[dict]`` 는 OpenAI strict mode 가 400 BadRequest 로 거부
(``additionalProperties`` 미명시). 이 파일의 모든 필드는 명시 Pydantic 모델 또는
``list[KeyValueItem]`` 로 표현 — cross-provider 안전 (OpenAI / Anthropic / SGLang /
OpenRouter 모두 정확 출력). ``agent_tools/agent_output_models.py`` 의 ``CrudMapping``
이 같은 영역의 정립된 패턴.

검증 규칙 (Pydantic validator):
- ``summary`` = ``핵심:`` 한 줄 + 내용 — ``핵심:`` 마커 없으면 LLM 재시도 유도
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, field_validator


# ── 공통 — 자유 키 영역(inputs / globals / out_params 등) strict 표현 ──


class KeyValueItem(BaseModel):
    """자유 키 dict 의 strict 호환 표현 (예: inputs / globals / out_params / where / values).

    LLM 출력 시 ``[{"key": "user_id", "value": "12345"}, ...]`` 모양.
    value 는 단순값·JSON 직렬화 문자열 모두 허용 (모델 내부에선 str).
    """
    key: str = Field(..., description="필드/변수/컬럼 이름")
    value: str = Field("", description="값 (단순값 또는 JSON 직렬화 문자열)")


# ── 3 분할 #1: function_summary ───────────────────────────


class SummaryOutput(BaseModel):
    """함수/range 의미 요약 — ``핵심:`` 한 줄 + 내용 + 메타.

    summary 는 Bottom-up DAG 로 상위(caller/부모)에 전달되므로 — 분기·조건·상태 등
    핵심 키포인트를 누락 없이, 단 원문보다 짧게(요약≤원문) 담아야 한다.
    """
    summary: str = Field(..., description="'핵심:' 한 줄 + 내용 (키포인트 보존, 원문보다 짧게)")
    stereotype: str = Field("", description="ontology_roles.yaml 풀에서 선택")
    logical_name: str = Field(
        "",
        description="형제 함수와 구별되는 도메인 한 줄 (코드 식별자 X — 프롬프트 [0] 지침)",
    )

    @field_validator("summary")
    @classmethod
    def _validate_summary_format(cls, v: str) -> str:
        """``핵심:`` 마커 검증 — 한 줄 핵심 + 내용 형식 강제."""
        if v and "핵심:" not in v:
            raise ValueError("summary 는 '핵심:' 한 줄로 시작해야 함 (핵심 + 내용)")
        return v


# ── 3 분할 #2: function_table_insights ────────────────────


class ColumnInsight(BaseModel):
    """함수 관점 컬럼 의미 1건 — yaml 의 columns[].{name,description,usage} 명시."""
    name: str = Field(..., description="DDL 컬럼명 정확히 (추측 X)")
    description: str = Field("", description="비즈니스 의미 한 줄 (DDL 타입 X / 동어반복 X)")
    usage: str = Field("", description="read/write/where/set/value/select/values/join_on/group_by/order_by 등")


class TableInsightItem(BaseModel):
    """함수가 한 테이블에 대해 갖는 역할 + 컬럼 description."""
    table: str = Field(..., description="db.schema.table fqn 또는 table 이름")
    role: str = Field(..., description="예: 'READ-only' / 'WRITE-then-READ' / 'WRITE-only'")
    columns: list[ColumnInsight] = Field(
        default_factory=list,
        description="이 함수 관점 컬럼 의미 (본문에 등장한 컬럼만)",
    )


class TableInsightsOutput(BaseModel):
    table_insights: list[TableInsightItem] = Field(default_factory=list)


# ── 3 분할 #3: function_rules_examples ────────────────────


class RuleItem(BaseModel):
    """비즈니스 규칙 1개 — Example Mapping(M12) 의 Rule 노드 입력."""
    rule_id: str = Field(..., description="'R1' / 'R2' ...")
    statement: str = Field(..., description="<조건>이면 <결과> — 코드 식별자 X")
    flow_id: str = Field("", description="제어 흐름 위치 — '1' / '1-1' / '2-1-1'")
    guard_rule_id: Optional[str] = Field(None, description="선행 통과 전제 Rule id (없으면 null)")
    branch_from: Optional[str] = Field(None, description="if/else 갈래면 분기 원 Rule id (없으면 null)")
    coupled_domains: list[str] = Field(default_factory=list, description="결합된 타 도메인 이름")


class ExceptionInfo(BaseModel):
    """then.exception — 구조 명확하므로 명시 모델."""
    type: str = Field(..., description="예외 타입 (예: 'RAISE_APPLICATION_ERROR' / 'IllegalArgumentException')")
    code: str = Field("", description="예외 코드 (예: '-20001' / 'BIZ_E001')")
    message: str = Field("", description="예외 메시지")


class ReadItem(BaseModel):
    """then.reads[] — 시나리오에서 읽는 테이블+컬럼."""
    table: str = Field(..., description="테이블 이름")
    columns: list[str] = Field(default_factory=list, description="이 시나리오에서 읽는 컬럼 이름")


class WriteItem(BaseModel):
    """then.writes[] — 시나리오에서 쓰는 테이블+op+조건+값."""
    table: str = Field(..., description="테이블 이름")
    op: str = Field("WRITE", description="INSERT / UPDATE / DELETE / MERGE / WRITE")
    where: list[KeyValueItem] = Field(default_factory=list, description="WHERE 절 조건 (컬럼=값)")
    values: list[KeyValueItem] = Field(default_factory=list, description="SET / VALUES (컬럼=값)")


class GivenContext(BaseModel):
    """ExampleItem.given — 시나리오 진입 조건."""
    narrative: str = Field("", description="왜 이 시나리오인지 한 줄 (inputs 풀어쓰기 X)")
    inputs: list[KeyValueItem] = Field(default_factory=list, description="파라미터 key:value")
    globals: list[KeyValueItem] = Field(default_factory=list, description="전제 전역 상태 (preconditions.globals)")
    stubs: list[KeyValueItem] = Field(
        default_factory=list,
        description="외부 호출 반환값 (preconditions.stubs — key=호출, value=반환값/JSON)",
    )


class ThenContext(BaseModel):
    """ExampleItem.then — 시나리오 결과."""
    narrative: str = Field("", description="결과 핵심 한 줄 (return·writes 풀어쓰기 X)")
    return_value: str = Field(
        "",
        description="반환값 (단순값 또는 JSON 직렬화 문자열). 반환 없으면 빈 문자열",
    )
    exception: Optional[ExceptionInfo] = Field(None, description="예외 발생 시. 없으면 null")
    reads: list[ReadItem] = Field(default_factory=list, description="이 시나리오 SELECT 대상")
    writes: list[WriteItem] = Field(default_factory=list, description="이 시나리오 INSERT/UPDATE/DELETE")
    globals: list[KeyValueItem] = Field(default_factory=list, description="변경된 전역")
    out_params: list[KeyValueItem] = Field(default_factory=list, description="변경된 OUT 파라미터")


class ExampleItem(BaseModel):
    """Rule 1개의 given/when/then 시나리오 — Example Mapping(M12) 의 Example 노드 입력."""
    rule_id: str = Field(..., description="참조 Rule id")
    description: str = Field("", description="사람용 한 줄 요약")
    given: GivenContext = Field(default_factory=GivenContext)
    when: str = Field("", description="자연어 한 줄 — 메서드명·클래스명 X")
    then: ThenContext = Field(default_factory=ThenContext)
    is_boundary: bool = Field(False, description="경계 케이스(이상/이하/초과/미만) 여부")


class QuestionItem(BaseModel):
    """코드만으로 답 안 되는 모호점 — Example Mapping 의 Question 노드 입력.

    Rule / Example 과 함께 Example Mapping 의 세 번째 축 — #3 분할(function_rules_examples)
    이 함께 출력. 테이블 유무와 무관하므로 #2(table_insights) 가 아닌 #3 소속.
    """
    text: str = Field(..., description="한 줄 질문")
    reason: str = Field("", description="코드 근거 부재 사유")


class RulesExamplesOutput(BaseModel):
    rules: list[RuleItem] = Field(default_factory=list)
    examples: list[ExampleItem] = Field(default_factory=list)
    questions: list[QuestionItem] = Field(
        default_factory=list,
        description="사람 검토 큐로 갈 질문 (text + reason)",
    )


# ── 모듈 사전분석 (module_pre_analysis — 함수 분석 전 1회) ──


class ModulePreAnalysisOutput(BaseModel):
    """모듈 헤더 주석 → 정제된 모듈 의도(intent). 함수 분석 3분할 context 에 주입.

    표시명(logical_name)은 여기서 만들지 X — 헤더 주석이 아니라 분석 결과에서
    도출해야 하므로 summarize_module 이 담당 (M11).
    """
    intent: str = Field(
        "",
        description="모듈 비즈니스 의도 1~3문장 (변경이력·작성자 노이즈 제거)",
    )


# ── 모듈 요약 (summarize_module_step) ─────────────────────


class ModuleSummaryOutput(BaseModel):
    summary: str = Field(..., description="모듈 전체 의미 (~2 단락)")
    stereotype: str = Field("", description="예: 'Service' / 'Repository' / 'Controller'")
    logical_name: str = Field("", description="모듈 도메인 표시명 한 줄 (코드 식별자 X)")


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
