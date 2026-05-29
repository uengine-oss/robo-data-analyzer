"""단순 LLM 호출 (Agent 아닌 것) 의 Pydantic 출력 스키마.

3 분할 fan-out + 모듈 요약 + 테이블 description 등.
각 yaml 의 ``response_format`` 으로 강제 — 자유 텍스트 응답 X.

사용처:
- ``analysis_flow/step6_analyze_function``: SummaryOutput / TableInsightsOutput / RulesExamplesOutput
- ``analysis_flow/step7_post_processing``: ModuleSummaryOutput / TableDescription

호출 모드 (단일 진실):
``llm.with_structured_output(..., method="function_calling")`` 가 기본 — strict 미강제.
단 SGLang + xgrammar 환경에서 자유 키 ``dict[str, str]`` 는 value 시작 토큰
선택 실패 → 공백 무한 폭주 사고 발생 (2026-05-30 5차). ``list[KeyValueItem]`` 도
같은 구조 반복 폭주 사고 (4차). 둘 다 schema 강제 부작용 — 결론적으로 examples 의
``given`` / ``then`` 은 **자연어 단일 문자열** 로 통합 (LLM 한테 가장 자유로운 출력 →
폭주 메커니즘 자체 차단).

검증 규칙 (Pydantic validator):
- ``summary`` = 2 섹션 ``[핵심] 한 줄 + 빈 줄 + 내용`` — ``[핵심]`` 마커 없으면
  LLM 재시도 유도 (단순 위임 함수는 ``[핵심]`` 한 줄로 끝, 내용 생략 OK).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


# ── 3 분할 #1: function_summary ───────────────────────────


class SummaryOutput(BaseModel):
    """함수/range 의미 요약 — 2 섹션 ``[핵심] 한 줄 + 내용`` + 메타.

    summary 는 Bottom-up DAG 로 상위(caller/부모)에 전달되므로 — 분기·조건·상태 등
    핵심 키포인트를 누락 없이, 단 원문보다 짧게(요약≤원문) 담아야 한다.
    형식: ``[핵심]`` 한 줄 필수 + 빈 줄 + 내용 (자유 서술, 단순 위임 함수는 내용 생략 OK).
    """
    summary: str = Field(
        ...,
        description="2 섹션 '[핵심] 한 줄' + 빈 줄 + 내용 (단순 위임은 [핵심] 만으로 끝)",
    )
    stereotype: str = Field("", description="ontology_roles.yaml 풀에서 선택")
    logical_name: str = Field(
        "",
        description="형제 함수와 구별되는 도메인 한 줄 (코드 식별자 X — 프롬프트 [0] 지침)",
    )

    @field_validator("summary")
    @classmethod
    def _validate_summary_format(cls, v: str) -> str:
        """``[핵심]`` 마커 검증 — 필수 진입점 강제 (없으면 LLM 재시도)."""
        if v and "[핵심]" not in v:
            raise ValueError("summary 는 '[핵심]' 한 줄로 시작해야 함")
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
    """비즈니스 규칙 1개 — Example Mapping(M12) 의 Rule 노드 입력.

    옛 ``guard_rule_id`` / ``branch_from`` 은 ``flow_id`` 로 도출 가능 (builder 가
    flow_id 의 ``-`` 계층으로 BRANCH 자동) → 폐기. ``flow_id`` + ``coupled_domains``
    만으로 그래프 흐름·도메인 결합 표현 충분.
    """
    rule_id: str = Field(..., description="'R1' / 'R2' ...")
    statement: str = Field(..., description="<조건>이면 <결과> — 코드 식별자 X")
    flow_id: str = Field(
        "",
        description="제어 흐름 위치 — '1' / '2' (메인) / '1-1' (R1 분기) / '1-1-1' (중첩)",
    )
    coupled_domains: list[str] = Field(
        default_factory=list,
        description="결합된 타 도메인 이름 (타 도메인 테이블 WRITE·값 분기·계산 시)",
    )


class AffectedTable(BaseModel):
    """이 시나리오에서 영향받는 테이블 1건 — AFFECTS_TABLE 엣지 단일 진실."""
    table: str = Field(..., description="테이블 이름")
    op: Literal["READ", "INSERT", "UPDATE", "DELETE", "MERGE"] = Field(
        ...,
        description="READ (SELECT) / INSERT / UPDATE / DELETE / MERGE",
    )


class ExampleItem(BaseModel):
    """Rule 1개의 given/when/then 시나리오 — Example Mapping 의 Example 노드 입력.

    given/then 은 **자연어 단일 문자열** — schema 강제 부작용 (자유 키 dict 공백 폭주 /
    list[KeyValueItem] 패턴 반복) 둘 다 차단. 사람용 narrative 1 줄.
    """
    rule_id: str = Field(..., description="참조 Rule id")
    description: str = Field("", description="시나리오 사람용 한 줄 요약")
    given: str = Field(
        "",
        description="진입 조건 한 줄 — 'iFlag=0, 오류코드=X001' 처럼 자연어 (공백/빈 영역 X)",
    )
    when: str = Field("", description="자연어 한 줄 — 메서드명·클래스명 X")
    then: str = Field(
        "",
        description="결과 한 줄 — '정상 RC_NRM 반환 / NotFound 면 RC_NFD' 처럼 자연어",
    )
    affected_tables: list[AffectedTable] = Field(
        default_factory=list,
        description="이 시나리오의 READ/WRITE 테이블 (op enum) — AFFECTS_TABLE 엣지 단일",
    )


class QuestionItem(BaseModel):
    """코드만으로 답 안 되는 모호점 — Example Mapping 의 Question 노드 입력.

    Rule / Example 과 함께 Example Mapping 의 세 번째 축 — #3 분할(function_rules_examples)
    이 함께 출력. 테이블 유무와 무관하므로 #2(table_insights) 가 아닌 #3 소속.
    """
    text: str = Field(..., description="한 줄 질문")
    reason: str = Field("", description="코드 근거 부재 사유")


class RulesExamplesOutput(BaseModel):
    """Rule + Example + Question 추출 결과.

    개수는 가치 결정 질문 (prompt §[1]) 통과한 것만 — Pydantic level 의 인위적 상한 X.
    grammar 폭주 안전망은 max_length=30 으로 느슨하게 (실제 함수의 진짜 Rule 은 3~7 개라
    이 상한엔 도달하지 X — 가치 결정 질문이 자연 압축).
    """
    rules: list[RuleItem] = Field(default_factory=list, max_length=30)
    examples: list[ExampleItem] = Field(default_factory=list, max_length=30)
    questions: list[QuestionItem] = Field(
        default_factory=list,
        max_length=15,
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
