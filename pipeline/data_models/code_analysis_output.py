"""단순 LLM 호출 (Agent 아닌 것) 의 Pydantic 출력 스키마.

3 분할 fan-out + 모듈 요약 + 테이블 description 등.
각 yaml 의 ``response_format`` 으로 강제 — 자유 텍스트 응답 X.

사용처:
- ``pipeline/steps/step6_analyze_code``: SummaryOutput / TableInsightsOutput / RulesExamplesOutput
- ``pipeline/steps/step7_post_processing``: ModuleSummaryOutput / TableDescription

호출 모드 (단일 진실):
``llm.with_structured_output(..., method="function_calling")`` 가 기본 — strict 미강제.
단 SGLang + xgrammar 환경에서 자유 키 ``dict[str, str]`` 는 value 시작 토큰
선택 실패 → 공백 무한 폭주 사고 발생 (2026-05-30 5차). 깊고 상한 없는
``list[KeyValueItem]`` 도 같은 구조 반복 폭주 사고 (4차). 둘 다 schema 강제 부작용 —
결론적으로 examples 의
``given`` / ``then`` 은 **자연어 단일 문자열** 로 통합 (LLM 한테 가장 자유로운 출력 →
폭주 메커니즘 자체 차단).

나열형 필드(``domain_terms``) 폭주 방어 — 실측 근거로 확정한 3겹:
1. **필드 순서**: 나열형·개방형은 앞, 닫힌 선택지는 끝
   (``search_text`` → ``domain_terms`` → ``logical_name`` → ``summary`` → ``stereotype``).
   나열형이 **마지막**이면 뒤에 쓸 게 없어 "끝"의 경계가 사라지고, 앞서 쓴 산문을 재조합하며
   무한 생성으로 샌다(실측: domain_terms 가 맨 끝일 때 load_pg_company 30k 토큰·255초 폭주).
   ``search_text`` 를 먼저 두는 이유 = 코드를 정독하는 작업이라 코드가 끝나면 자연히 멈춘다(무폭주 실적).
2. **표제어 = 본문 실재 토큰만**: 코드에 그 글자 그대로 있는 식별자만. 조합어 창작 금지 →
   후보가 유한해져 다 훑으면 자연히 멈춘다. 단 **뜻풀이에는 약어를 일반 업계 말로 풀어써야**
   검색 다리가 놓인다(실측: 뜻이 "SK-VAN 등록 여부"뿐이면 "결제망" 질의가 못 찾음).
3. **폴백**: 그래도 터지면 ``SummaryCoreOutput`` 축소 재호출로 노드 상실 0 + 로그 태그
   ``search_field_overflow`` 로 표면화.
4. **구조·상한**: 자유키 dict가 아닌 고정형 ``DomainTermItem`` 배열(0~10)을 사용한다.
   10개는 목표가 아니라 절대 상한이다. 후보가 더 많으면 의미 우선순위 상위 10개만 선택한다.
   규칙별 출력 토큰 상한도 별도로 둔다.

검증 규칙 (Pydantic validator):
- ``summary`` = 마커 없는 1~3줄 짧은 요약 (spec 041 다이어트) — validator 는 거부가
  아니라 **결정론 정규화**: 옛 ``[핵심]`` 마커는 벗기고, 3줄 초과분은 절단. 호출 재시도가
  없는 구조라 거부는 곧 노드 분석 소실(실측: 다이어트 런에서 요약 7건 유실) — 편차는
  흡수하고 마지노선은 기계가 보장한다. 세부 분기·코드값은 ``search_text`` 가 담당.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


def _normalize_diet_summary(v: str) -> str:
    """summary 다이어트 정규화 — 마커 제거 + 비어있지 않은 줄 3줄 절단 (실패 없음)."""
    if not v:
        return v
    v = v.replace("[핵심]", " ").strip()
    lines = [ln.strip() for ln in v.splitlines() if ln.strip()]
    return "\n".join(lines[:3])


# ── 3 분할 #1: code_summary ───────────────────────────


class SummaryOutput(BaseModel):
    """코드노드/range 의미 요약(1~3줄) + 검색 서술 + 메타.

    summary = 사람이 "뭘 하는 함수인지" 즉시 파악하는 짧은 요약(마커 없는 1~3줄) —
    Bottom-up DAG 로 상위(caller/부모)에 전달된다. 분기·코드값·테이블 세부는
    search_text(검색 전용, 프론트 비노출)가 누락 없이 담는다.
    """
    # ★ 필드 순서 = 폭주 방어 설계 (실측 근거):
    #   ① 나열형·개방형 필드는 앞에 — 마지막이면 "끝"의 경계가 없어 무한 생성으로 샌다
    #      (실측: domain_terms 가 맨 끝일 때 load_pg_company 30k 토큰 폭주).
    #   ② search_text 가 먼저 — 코드를 정독하며 분기·식별자를 훑는 작업. 코드가 끝나면
    #      쓸 게 없어 자연히 멈춘다(무폭주 실적).
    #   ③ domain_terms 는 그 직후 — 방금 훑은 식별자가 눈앞에 있고, 뒤에 남은 필드가
    #      "끝"의 경계가 되어준다.
    #   ④ summary 는 최종 결론이라 뒤쪽. stereotype 은 닫힌 15지선다라 폭주 불가 → 맨 끝.
    search_text: str = Field(
        "",
        description="검색 전용 서술 — 분기 조건·상태 전이·코드값·테이블 상호작용을 업무 어휘로 (프론트 비노출, 단순 노드는 빈 값)",
    )
    domain_terms: list["DomainTermItem"] = Field(
        default_factory=list,
        max_length=10,
        description="본문 실재 표제어와 업무 뜻의 구조화 목록 (0~10개; 10개는 목표가 아닌 절대 상한)",
    )
    logical_name: str = Field(
        "",
        description="형제 함수와 구별되는 도메인 한 줄 (코드 식별자 X — 프롬프트 [0] 지침)",
    )
    summary: str = Field(
        ...,
        description="마커 없는 1~3줄 짧은 요약 (세부 분기·코드값은 search_text 책임)",
    )
    stereotype: str = Field("", description="ontology_roles.yaml 풀에서 선택")

    @field_validator("summary")
    @classmethod
    def _validate_summary_format(cls, v: str) -> str:
        return _normalize_diet_summary(v)


class SummaryCoreOutput(BaseModel):
    """분할1 축소 폴백 — 검색 필드(search_text/domain_terms) 생성 중 출력 폭주로 본 호출이
    실패했을 때, 핵심 3필드만으로 1회 재호출해 노드 상실을 막는다(전부 잃기 < 핵심 확보)."""
    summary: str = Field(..., description="마커 없는 1~3줄 짧은 요약")
    stereotype: str = Field("", description="ontology_roles.yaml 풀에서 선택")
    logical_name: str = Field("", description="형제 함수와 구별되는 도메인 한 줄")

    @field_validator("summary")
    @classmethod
    def _validate_summary_format(cls, v: str) -> str:
        return _normalize_diet_summary(v)


class DomainTermItem(BaseModel):
    """LLM 경계용 고정 키 구조. 내부에서는 검증 후 dict로 변환한다."""
    term: str = Field(..., min_length=1, max_length=60)
    meaning: str = Field(..., min_length=1, max_length=500)


# ── 3 분할 #2: code_table_insights ────────────────────


class ColumnInsight(BaseModel):
    """코드노드 관점 컬럼 의미 1건 — 소비처(enrich_table_description)가 name·logical_name·description 사용.

    ``usage`` 는 생성돼도 소비 0(전수 grep 확정) → 응답 스키마에서 제외(불필요 토큰·폭주면적 차단).
    ``logical_name`` = 테이블 logical_name(spec 025) 미러 — 짧은 업무 표시명(spec 028).
    """
    name: str = Field(..., description="DDL 컬럼명 정확히 (추측 X)")
    logical_name: str = Field("", description="컬럼 도메인 표시명 한 줄 (코드 식별자·DDL 타입·동어반복 X)")
    description: str = Field("", description="비즈니스 의미 한 줄 (DDL 타입 X / 동어반복 X)")


class TableInsightItem(BaseModel):
    """코드노드가 한 테이블에 대해 갖는 역할 + 컬럼 description."""
    table: str = Field(..., description="db.schema.table fqn 또는 table 이름")
    role: str = Field(..., description="예: 'READ-only' / 'WRITE-then-READ' / 'WRITE-only'")
    columns: list[ColumnInsight] = Field(
        default_factory=list,
        description="이 함수 관점 컬럼 의미 (본문에 등장한 컬럼만)",
    )


class TableInsightsOutput(BaseModel):
    table_insights: list[TableInsightItem] = Field(default_factory=list)


# ── 3 분할 #3: code_rules_examples ────────────────────


class AffectedTable(BaseModel):
    """이 시나리오에서 영향받는 테이블 1건 — AFFECTS_TABLE 엣지 단일 진실."""
    table: str = Field(..., description="테이블 이름")
    op: Literal["READ", "INSERT", "UPDATE", "DELETE", "MERGE"] = Field(
        ...,
        description="READ (SELECT) / INSERT / UPDATE / DELETE / MERGE",
    )


class ExampleItem(BaseModel):
    """Rule 하위 given/when/then 시나리오 — Example Mapping 의 Example 노드 입력.

    given/then 은 자유키 dict 폭주 차단 위해 자연어 단일 문자열. 소속 Rule 하위 중첩 —
    참조 id 없이 구조로 연결.
    """
    given: str = Field("", description="진입 조건 한 줄 (확정된 코드 리터럴은 정확히 보존)")
    when: str = Field("", description="트리거 한 줄 (메서드명·클래스명 X, 확정 리터럴 보존)")
    then: str = Field("", description="결과 한 줄 (확정된 실제 코드값 보존, 불명확하면 추측 X)")
    affected_tables: list[AffectedTable] = Field(
        default_factory=list,
        description="이 시나리오의 READ/WRITE 테이블 (op enum) — AFFECTS_TABLE 엣지 단일",
    )


class RuleItem(BaseModel):
    """비즈니스 규칙 1개 + 그 시나리오 — Example Mapping 의 Rule 노드 입력.

    흐름(순서/분기)은 응답에서 뺀다: 순서=배열 순서, 분기 의미=statement 텍스트.
    examples 는 소속 rule 하위 중첩(참조 id 폐기).
    """
    statement: str = Field(..., description="<조건>이면 <결과> — 코드 식별자 X")
    coupled_domains: list[str] = Field(
        default_factory=list,
        description="결합된 타 도메인 이름 (타 도메인 테이블 WRITE·값 분기·계산 시)",
    )
    examples: list[ExampleItem] = Field(
        default_factory=list,
        max_length=80,
        description="이 규칙의 given/when/then 시나리오 (같은결과 변주만 1~2)",
    )


class QuestionItem(BaseModel):
    """코드만으로 답 안 되는 모호점 — Example Mapping 의 Question 노드 입력.

    Rule / Example 과 함께 Example Mapping 의 세 번째 축 — #3 분할(code_rules_examples)
    이 함께 출력. 테이블 유무와 무관하므로 #2(table_insights) 가 아닌 #3 소속.
    """
    text: str = Field(..., description="한 줄 질문")
    reason: str = Field("", description="코드 근거 부재 사유")


class RulesExamplesOutput(BaseModel):
    """Rule(하위 Example 중첩) + Question 추출 결과.

    개수는 가치 결정 질문 (prompt §[1]) 통과한 것만. max_length 캡은 **비즈니스 규칙이 아니라
    provider 출력 폭주(수백 개) 안전망**이다 — "정상적으로 많은 것"을 막지 않는다(spec 054 FR-022).
    실제 품질(반복·무근거·Rule 비매핑) 판정은 결정론 품질검사기(rule_quality)와 폴백이 전담한다.
    examples 캡은 30→80 (관측 정상 범위 21~47 을 여유 있게 수용, 수백 개 폭주는 여전히 차단).
    rules/questions 캡(30/15)은 개수 폭주가 관측된 적 없어 유지.
    """
    rules: list[RuleItem] = Field(default_factory=list, max_length=30)
    questions: list[QuestionItem] = Field(
        default_factory=list,
        max_length=15,
        description="사람 검토 큐로 갈 질문 (text + reason)",
    )


# ── DBMS 배치 3분할 (🅕 — 같은 유효부모 형제 N 노드를 관심사별 1콜) ──
# 3관심사(요약/테이블/비즈니스로직)를 각각 배치(노드 리스트)로 호출 → 각 콜은
# "한 관심사 × N 노드"의 평탄 리스트 = 깊은 중첩 0 (한 콜에 4중첩 합본 → 폭주 차단).
# framework per-node 3분할과 동일 관심사·동일 항목(RuleItem/ExampleItem/QuestionItem/
# TableInsightItem 재사용), 다른 점은 노드 리스트로 묶어 던지는 것뿐. node_key(줄범위)로 분배.


class BatchSummaryItem(BaseModel):
    """배치 요약분할 — 노드 1개의 요약/식별 결과."""
    node_key: str = Field(..., description="입력 줄범위 '<start>-<end>' 그대로 echo")
    # ★ 필드 순서 = 폭주 방어 (SummaryOutput 주석 참조): 나열형 앞 → 닫힌 선택지 끝.
    search_text: str = Field(
        "",
        description="검색 전용 서술 — 분기 조건·상태 전이·코드값·테이블 상호작용을 업무 어휘로 (프론트 비노출, 단순 노드는 빈 값)",
    )
    domain_terms: list[DomainTermItem] = Field(
        default_factory=list,
        max_length=10,
        description="본문 실재 표제어와 업무 뜻의 구조화 목록 (0~10개; 10개는 목표가 아닌 절대 상한)",
    )
    logical_name: str = Field("", description="형제와 구별되는 도메인 한 줄 표시명")
    summary: str = Field("", description="마커 없는 1~3줄 짧은 요약 (세부 분기·코드값은 search_text 책임)")
    stereotype: str = Field("", description="ontology_roles.yaml 풀에서 선택 (없으면 빈 값)")

    @field_validator("summary")
    @classmethod
    def _validate_summary_format(cls, v: str) -> str:
        return _normalize_diet_summary(v)


class BatchSummaryOutput(BaseModel):
    """요약 배치 결과 — 입력 노드와 1:1 (node_key 매칭). 누락은 호출자가 미니 재요청."""
    nodes: list[BatchSummaryItem] = Field(default_factory=list)


class BatchRulesItem(BaseModel):
    """배치 비즈니스로직 분할 — 노드 1개의 규칙(하위 예시 중첩)/질문."""
    node_key: str = Field(..., description="입력 줄범위 '<start>-<end>' 그대로 echo")
    rules: list[RuleItem] = Field(default_factory=list, max_length=30)
    questions: list[QuestionItem] = Field(default_factory=list, max_length=15)


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
    intent: str = Field(
        "",
        description="모듈 비즈니스 의도 1~3문장 (변경이력·작성자 노이즈 제거)",
    )


# ── 모듈 요약 (summarize_module_step) ─────────────────────


class ModuleSummaryOutput(BaseModel):
    summary: str = Field(..., description="모듈 전체 의미 (~2 단락)")
    stereotype: str = Field("", description="예: 'Service' / 'Repository' / 'Controller'")
    logical_name: str = Field("", description="모듈 도메인 표시명 한 줄 (코드 식별자 X)")


# ── 용어사전 종합 (build_glossary_step, spec 043) ─────────────


class GlossarySynthesisOutput(BaseModel):
    """용어 1개의 종합 결과 — 평탄 문자열 3필드(중첩·자유키 금지, 폭주 이력 준수).

    수용은 결정론 검증이 한다: evidence 가 입력 문맥의 실제 부분열이 아니면 종합 기각
    (기존 뜻 유지), aliases 는 전체 표제어 목록에 실재하는 것만 인정."""
    meaning: str = Field(..., description="종합된 뜻 한 줄 — 제공된 문맥에 적힌 근거로만")
    aliases: str = Field("", description="같은 실체를 가리키는 다른 표제어들(쉼표구분, 목록에 실재하는 표기만, 없으면 빈 값)")
    evidence: str = Field("", description="뜻·변종 판단의 근거 문장(제공된 문맥에서 그대로 복사)")


# ── 컴포넌트 이름 (build_components_step) ─────────────────


class ComponentNamingOutput(BaseModel):
    """부품(코드노드 군집) 이름·설명 — 멤버 코드노드 요약 종합. 평탄 구조(자유키 dict X)."""
    name: str = Field(..., description="부품 도메인 이름 한 줄 (코드 식별자 X)")
    description: str = Field("", description="이 부품이 하는 일 한 줄 설명")
    stereotype: str = Field("", description="역할 라벨 (예: Service/Repository/Batch — 없으면 빈 값)")


# ── 테이블 description 통합 (enrich_table_description_step) ──


class TableDescription(BaseModel):
    db: str
    schema_: str = Field(..., alias="schema")
    name: str
    logical_name: str = ""          # 짧은 도메인 표시명(물리명과 별개, 근거없으면 빈값) — spec 025
    description: str

    class Config:
        populate_by_name = True


# (옛 ``TableDescriptionsOutput`` 삭제 — 참조 0건. 실제 response_model 은 단수형
#  ``TableDescription`` (enrich_table_description_step). spec 046 FR-010.)
