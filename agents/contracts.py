"""Agent 출력 Pydantic 스키마 — Scan Agent · 잔여 패턴 루프의 ``response_format`` 강제.

LLM 이 자유 텍스트 응답 못 함. 다운스트림 파싱 부담 X — 환각 차단.

운영 정책 ``ROBO_LLM_REASONING_EFFORT=none`` 과 결합하면 더 강력.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import Field, model_validator

from llm.structured_schema import CompactLlmResponse


class StrictAgentOutput(CompactLlmResponse):
    """에이전트가 계약 밖 필드를 조용히 흘리지 못하게 하는 공통 출력 경계."""

# ═══════════════════════════════════════════════════════════════
# Scan Agent 출력
# ═══════════════════════════════════════════════════════════════
#
# Scan Agent 는 탐색 도구 + 검증 도구를 든 단일 에이전트다. 코드를 둘러보고
# SKILL 을 매칭하고, SKILL 이 없으면 정규식을 직접 작성·검증해 ``ScanResult`` 를
# 출력한다. 정규식 같은 구조물은 검증 도구를 가진 에이전트만 만들 수 있다 — 도구
# 없는 별도 결정 LLM 으로 쪼개면 검증된 정규식이 전달 스키마에서 증발한다(과거 버그).
# 그래서 탐색/결정을 쪼개지 않고 단일 에이전트로 둔다.


class OpMapping(StrictAgentOutput):
    """한 프레임워크 raw 매치 토큰 → 표준 SQL op 매핑 1행.

    R/W 분류는 코드가 자동 결정한다 (SELECT=READ, INSERT/UPDATE/DELETE=WRITE).
    LLM 이 R/W 박을 필요 X — 표준 op 만 선택하면 끝.

    유효한 출력 예시 (프레임워크별 token → 표준 op):
    - ``{"token": "selectOne", "op": "SELECT"}``  (mybatis 메서드명)
    - ``{"token": "insert",    "op": "INSERT"}``
    - ``{"token": "findOne",   "op": "SELECT"}``  (jpa/ofbiz 메서드명 → SELECT 의미)
    - ``{"token": "save",      "op": "INSERT"}``  (jpa — 새 row 의미면 INSERT)
    - ``{"token": "remove",    "op": "DELETE"}``  (ofbiz)
    - ``{"token": "s",         "op": "SELECT"}``  (proframe 한 글자)
    - ``{"token": "i",         "op": "INSERT"}``
    - ``{"token": "u",         "op": "UPDATE"}``
    - ``{"token": "d",         "op": "DELETE"}``
    - ``{"token": "SELECT",    "op": "SELECT"}``  (raw SQL — 토큰=표준 동사 같음)

    잘못된 출력 (절대 금지):
    - ``{"token": "save", "op": "WRITE"}``  ✗ — op 에 WRITE 박지 말 것. op 는 4가지 SQL 동사만.
    - ``{"token": "s"}``                    ✗ — op 필드 누락 (필수).

    이 모델 외 필드 추가 금지 — 정확히 token + op 두 필드만.
    """
    token: str = Field(
        ...,
        description=(
            "call_regex 의 <op_token> named group 이 매치한 raw 문자열. "
            "프레임워크별 다양 — 메서드명('findOne'), 한 글자('s'), SQL 동사('SELECT') 등"
        ),
    )
    op: Literal["SELECT", "INSERT", "UPDATE", "DELETE"] = Field(
        ...,
        description=(
            "이 token 의 표준 SQL op. 정확히 4가지 중 하나: "
            "SELECT / INSERT / UPDATE / DELETE. "
            "★ READ/WRITE/CREATE 등 다른 값 금지"
        ),
    )


class TableAccessPattern(StrictAgentOutput):
    """코드노드 → 테이블 접근 정규식 매칭 패턴 (Linking 1 단계 결정론 grep 용).

    ``call_regex`` 의 named group:
    - ``<op_token>``: 프레임워크 raw 매치 토큰 (예: 's', 'findOne', 'SELECT')
    - ``<entity>``: 테이블/엔티티 이름

    ``op_mappings`` 의 각 ``OpMapping`` 으로 token → 표준 SQL op (SELECT/INSERT/UPDATE/DELETE) 변환.
    READ/WRITE 는 코드가 자동 매핑 (SELECT=READ, 나머지=WRITE).
    """
    id: str = Field(..., description="패턴 식별자 (예: 'ofbiz_delegator')")
    call_regex: str = Field(..., description="named group <op_token> + <entity> 포함")
    op_mappings: list[OpMapping] = Field(...)


class TableAccessExclusionPattern(StrictAgentOutput):
    """A source pattern that is known not to represent a TABLE access.

    Exclusions never carry operation mappings and can never create graph edges.
    They only define physical source regions that TABLE rules must not claim.
    """

    id: str = Field(..., min_length=1, description="exclusion pattern identifier")
    call_regex: str = Field(..., min_length=1, description="compiled source shape")


# ── wrapper 규칙 DSL (spec 108/115) — 닫힌 템플릿 4종. 조합 문법 금지 ──
#
# 에이전트는 빈칸(필드)만 채우고, 정규식은 ``agents/rule_compiler.py`` 가 기계 생성한다
# → <entity>·<op_token> named group 이 구조상 보장돼 "죽은 규칙"(entity 누락)이 재발 불가.
# 4종에 안 들어가는 커스텀만 raw ``TableAccessPattern`` 탈출구 사용 (동일 검증 관문).
# 새 템플릿 종류 추가는 별도 spec 으로만 (미니 언어 방지 — 108 D1).


class WrapperKeyRule(StrictAgentOutput):
    """wrapper 함수의 문자열 키 인자 안에 테이블·op 가 인코딩된 형태.

    예: ``db_call("orders_read001", ...)``
    → wrapper="db_call", separator="_", op_tokens=["read","write"], suffix_digits=3
    """
    kind: Literal["wrapper_key"] = "wrapper_key"
    id: str = Field(..., description="패턴 식별자")
    wrapper: str = Field(..., description="wrapper 함수 이름")
    separator: str = Field("_", description="entity 와 op 사이 리터럴")
    op_tokens: list[str] = Field(..., description="op 토큰 문자들/문자열들")
    suffix_digits: int = Field(0, ge=0, le=8, description="op 뒤 숫자 자릿수. 0=없음")
    entity_not_ending: str = Field(
        "", description="entity 가 이 접미사로 끝나면 거부"
    )
    op_mappings: list[OpMapping] = Field(...)


class WrapperKeyExclusionRule(StrictAgentOutput):
    """A wrapper-key shape owned by a non-TABLE database object.

    Example shape: ``db_call("<entity><entity_suffix>_<op><digits>")``.
    The suffix is data supplied by the matched SKILL; generic code does not
    assign object meaning to any particular spelling.
    """

    kind: Literal["wrapper_key"] = "wrapper_key"
    id: str = Field(..., min_length=1, description="exclusion rule identifier")
    wrapper: str = Field(..., min_length=1, description="wrapper function name")
    entity_suffix: str = Field(..., min_length=1, description="non-TABLE entity marker")
    separator: str = Field("_", min_length=1, description="suffix/op separator")
    op_tokens: list[str] = Field(..., min_length=1, description="raw operation tokens")
    suffix_digits: int = Field(0, ge=0, le=8, description="digits following the op token")


class MethodCallRule(StrictAgentOutput):
    """리시버.메서드(…) 호출 형태 — entity 는 리시버 이름 또는 첫 문자열 인자.

    예: ``orderRepository.findByX(...)`` → receiver_suffix="Repository",
        entity_from="receiver", op_prefixes=["find","save",...]
    예: ``sqlSession.selectOne("Order.findById", ...)`` → receiver_name="sqlSession",
        entity_from="first_arg_string"
    """
    kind: Literal["method_call"] = "method_call"
    id: str = Field(..., description="패턴 식별자")
    receiver_suffix: str = Field(
        "", description="리시버 이름 접미사 (예: 'Repository' — entity_from=receiver 전용)"
    )
    receiver_name: str = Field(
        "", description="고정 리시버 이름 (예: 'sqlSession' / 'delegator')"
    )
    op_prefixes: list[str] = Field(..., description="메서드명 prefix 목록 (예: ['find','save'])")
    entity_from: Literal["receiver", "first_arg_string"] = Field(...)
    first_arg_charset: str = Field(
        r"\w", description="첫 인자 entity 문자클래스 (예: '\\w' / '[\\w.]' 의 내용부)"
    )
    op_mappings: list[OpMapping] = Field(...)


class SqlFragmentRule(StrictAgentOutput):
    """소스 문자열 속 SQL 조각 — C 문자열 개행 이음(``"..." "..."``)을 허용하는 매치.

    예: ``INSERT INTO 테이블`` / ``UPDATE 테이블 SET`` / ``FROM 테이블``.
    keyword 자체가 op_token 이 된다.
    """
    kind: Literal["sql_fragment"] = "sql_fragment"
    id: str = Field(..., description="패턴 식별자")
    keyword: str = Field(..., description="선두 SQL 키워드 = op_token (예: 'INSERT')")
    before_entity: str = Field("", description="entity 직전 리터럴 (예: 'INTO' / 'FROM'). ''=없음")
    after_entity: str = Field("", description="entity 직후 리터럴 (예: 'SET'). ''=없음")
    op_mappings: list[OpMapping] = Field(...)


class EmbeddedSqlRule(StrictAgentOutput):
    """구분자(울타리)로 둘러싸인 SQL 블록 — Pro*C/COBOL/ABAP 류 임베디드 SQL (spec 115).

    예: ``EXEC SQL UPDATE orders SET ... END-EXEC``
    → delimiter_start="EXEC SQL", delimiter_end="END-EXEC"

    op_mappings 가 없다 — 블록 안은 진짜 SQL 이므로 relation/dml_type 판정을
    **DML 의미표(pipeline/dml_semantics.yaml)가 그대로** 수행한다 (R/W 로직 중복 0).
    """
    kind: Literal["embedded_sql"] = "embedded_sql"
    id: str = Field(..., description="패턴 식별자")
    delimiter_start: str = Field(..., description="블록 시작 구분자 (예: 'EXEC SQL')")
    delimiter_end: str = Field(..., description="블록 끝 구분자 (예: 'END-EXEC' / ';')")


TableAccessRule = WrapperKeyRule | MethodCallRule | SqlFragmentRule | EmbeddedSqlRule
TableAccessExclusionRule = WrapperKeyExclusionRule


class DdlTransform(StrictAgentOutput):
    """엔티티 이름 → DDL 테이블 이름 변환 규칙."""
    ddl_naming_rule: Literal["as_is", "camel_to_snake_upper", "snake_to_camel"] = Field(
        ..., description="예: 'as_is' / 'camel_to_snake_upper' / 'snake_to_camel'"
    )
    fallback_prefixes: list[str] = Field(default_factory=list)


# ───────────────────────────────────────────────────────────────
# 호출 wrapper — 인자(이름 string)가 실제 호출 대상인 코드노드
# ───────────────────────────────────────────────────────────────
#
# wrapper 자체는 ``framework_runtime_functions`` 에 들어가 호출 그래프에서 드롭되지만,
# 그 인자가 실제 호출 타겟(다른 코드노드/모듈)인 경우 — 그 인자를 추출해 ``Calls`` 엣지로
# 정합한다. 별도 카테고리/별도 step 이 아니라 ``call_classifier`` 안에서 처리 — 호출은
# 한 영역 (Calls 엣지) 으로 단일화.
#
# 적용 framework 예 (개념적):
# - 런타임 동적 dispatch (이름 string 으로 호출)
# - IoC 컨테이너 lookup (이름으로 빈/서비스 조회)
# - 리플렉션 호출 (메서드 이름 / 모듈 이름)


class CallWrapper(StrictAgentOutput):
    """호출 wrapper 명시 — wrapper 이름 + 인자 추출 위치.

    SKILL 의 ``ScanResult contract.call_wrappers``를 **그대로 옮겨 담는다** (matched
    SKILL에 항목이 있으면 반드시 채움). 누락 시 wrapper 호출이 호출 그래프에서 사라짐 (framework
    drop 으로 빠짐) — 모듈 간 의존관계 손실 사고. 대부분 빈 배열이 정답이지만
    (JPA/MyBatis/OFBiz 등은 wrapper 없음), ProFrame 류는 반드시 옮겨야 함.

    ``call_classifier`` 는 Parser common evidence가 보존한 grammar-owned 첫 인자 구조만 소비해
    실제 호출 대상으로 분류한다 (code_nodes 안에 있으면 internal, 없으면 external). 원문 라인을
    정규식·문자열 분할로 다시 파싱하지 않는다.
    """
    wrapper: str = Field(
        ...,
        description=(
            "wrapper 함수 이름 — SKILL contract의 ``call_wrappers[].wrapper`` 값 "
            "그대로 (예: ProFrame ``mpfm_dlcall``). framework_runtime_functions 에도 동시 포함되지만 "
            "인자가 진짜 호출 대상이라 별도 분류."
        ),
    )
    callee_from: Literal["first_arg_string", "first_arg_identifier"] = Field(
        "first_arg_string",
        description=(
            "호출 대상을 어디서 뽑을지 — SKILL 의 ``callee_from`` 값 그대로. "
            "지원: 'first_arg_string' (첫 인자 큰따옴표) / 'first_arg_identifier' "
            "(첫 인자 식별자). SKILL 이 wrapper 의 의미에 맞게 명시."
        ),
    )


class ApiContract(StrictAgentOutput):
    """프레임워크 API 의 문서 보증 값 계약 (spec 116 §19 — SKILL fence 전용).

    SKILL 카드 ``api_contracts`` fence 를 그대로 옮겨 담는다 — 추측으로 채우지 않는다.
    rules 프롬프트에 "소스만 봐서는 모르는 값 사실"(성공 판정값·인자 역할)로 주입되고,
    그 값 문자열은 채점기 창작 검사 풀에 합류한다.
    """
    name: str = Field(..., description="API/매크로 이름 (예: mpfmdbio)")
    arg_roles: dict[int, str] = Field(
        default_factory=dict,
        description="0-based 인자 위치 → 역할 이름 (예: {0: 'sql_key'})",
    )
    success_value: Optional[str] = Field(
        None, description="성공 판정에 쓰이는 값 원문 (예: SUCCESS_CODE). 문서 보증만."
    )


class ProposedTableAccessPattern(StrictAgentOutput):
    """Model-owned raw pattern body; Analyzer assigns its internal identity."""

    call_regex: str
    op_mappings: list[OpMapping]


class ProposedWrapperKeyRule(StrictAgentOutput):
    kind: Literal["wrapper_key"] = "wrapper_key"
    wrapper: str
    separator: str = "_"
    op_tokens: list[str]
    suffix_digits: int = Field(0, ge=0, le=8)
    entity_not_ending: str = ""
    op_mappings: list[OpMapping]


class ProposedMethodCallRule(StrictAgentOutput):
    kind: Literal["method_call"] = "method_call"
    receiver_suffix: str = ""
    receiver_name: str = ""
    op_prefixes: list[str]
    entity_from: Literal["receiver", "first_arg_string"]
    first_arg_charset: str = r"\w"
    op_mappings: list[OpMapping]


class ProposedSqlFragmentRule(StrictAgentOutput):
    kind: Literal["sql_fragment"] = "sql_fragment"
    keyword: str
    before_entity: str = ""
    after_entity: str = ""
    op_mappings: list[OpMapping]


class ProposedEmbeddedSqlRule(StrictAgentOutput):
    kind: Literal["embedded_sql"] = "embedded_sql"
    delimiter_start: str
    delimiter_end: str


ProposedTableAccessRule = (
    ProposedWrapperKeyRule
    | ProposedMethodCallRule
    | ProposedSqlFragmentRule
    | ProposedEmbeddedSqlRule
)


class ScanProposal(StrictAgentOutput):
    """Unknown-project Scan Agent가 제안할 수 있는 최소 계약.

    원문 좌표, 검증 상태, 신뢰도와 SKILL 소유 사실은 포함하지 않는다. 제안된
    테이블 접근 규칙은 Analyzer가 production linker로 다시 검증한 뒤에만
    ``ScanResult``로 승격한다.
    """

    matched_skill: Optional[str] = Field(..., description="매칭된 SKILL 이름 또는 null")
    table_access_rules: list[ProposedTableAccessRule] = Field(default_factory=list)
    table_access_patterns: list[ProposedTableAccessPattern] = Field(default_factory=list)
    ddl_transform: DdlTransform = Field(
        default_factory=lambda: DdlTransform(ddl_naming_rule="as_is")
    )
    call_wrappers: list[CallWrapper] = Field(default_factory=list)
    framework_runtime_functions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_proposal_scope(self) -> "ScanProposal":
        custom_fields_present = bool(
            self.table_access_rules
            or self.table_access_patterns
            or self.ddl_transform != DdlTransform(ddl_naming_rule="as_is")
            or self.call_wrappers
            or self.framework_runtime_functions
        )
        if self.matched_skill is not None and custom_fields_present:
            raise ValueError(
                "matched SKILL 제안은 이름만 반환해야 하며 SKILL 소유 계약을 복사할 수 없음"
            )
        return self


class ScanResult(StrictAgentOutput):
    """Analyzer가 검증을 끝낸 프로젝트 스캔 계약."""
    analysis_status: Literal["completed", "unavailable"] = "completed"
    table_access_status: Literal["completed", "unavailable"] = Field(
        "completed",
        description=(
            "테이블 R/W 계약 facet의 독립 상태. unavailable이면 테이블 규칙은 비우되 "
            "검증된 handler/API/call 계약까지 지우지 않는다."
        ),
    )
    matched_skill: Optional[str] = Field(None, description="매칭된 SKILL 이름 또는 null")

    # 결정론 grep 용 — 구조화 규칙(DSL, 우선) + raw 정규식(탈출구, 4종 템플릿 불가 시만).
    # 규칙은 코드(_finalize)가 컴파일해 patterns 에 합류 — 검증·링킹 관문은 하나 (spec 108).
    table_access_rules: list[
        WrapperKeyRule | MethodCallRule | SqlFragmentRule | EmbeddedSqlRule
    ] = Field(
        default_factory=list,
        description=(
            "구조화 접근 규칙 (kind=wrapper_key|method_call|sql_fragment|embedded_sql). DSL 우선"
        ),
    )
    table_access_patterns: list[TableAccessPattern] = Field(default_factory=list)
    table_access_exclusion_rules: list[WrapperKeyExclusionRule] = Field(
        default_factory=list,
        description=(
            "SKILL/source-grounded non-TABLE source shapes. These never create edges and "
            "reject overlapping TABLE patterns."
        ),
    )
    ddl_transform: DdlTransform = Field(
        default_factory=lambda: DdlTransform(ddl_naming_rule="as_is")
    )

    # 호출 wrapper — 인자가 실제 호출 대상인 코드노드 (SKILL 이 정의). 없으면 빈배열.
    # ``call_classifier`` 가 wrapper 호출을 만나면 인자를 추출해 Calls 엣지로 정합.
    call_wrappers: list[CallWrapper] = Field(default_factory=list)

    # 분기표 종료문 행용 — 예외 핸들러 진입 라벨/매크로 (spec 116 1단계 [handler] 행).
    # SKILL fence 계약이 있으면 그 값으로 결정론 덮어쓰기 — 추측으로 채우지 않는다.
    handler_markers: list[str] = Field(
        default_factory=list,
        description=(
            "예외 핸들러 진입 라벨/매크로 이름 (프레임워크 문서로 보증되는 것만, "
            "예: try/catch 를 대신하는 매크로). 확실하지 않으면 빈 배열."
        ),
    )

    # 값 사실 주입용 — 프레임워크 API 의 문서 보증 값 계약 (spec 116 §19).
    # SKILL fence 계약이 있으면 그 값으로 결정론 덮어쓰기 — 추측으로 채우지 않는다.
    api_contracts: list[ApiContract] = Field(
        default_factory=list,
        description=(
            "프레임워크 API 값 계약 (SKILL 문서로 보증되는 것만 — sql_key 인자 위치, "
            "성공 판정값 등). 확실하지 않으면 빈 배열."
        ),
    )

    # 호출 분류용 — 프레임워크/런타임 루틴 이름 (skill 문서에서 읽음). 호출 그래프에서 드롭.
    framework_runtime_functions: list[str] = Field(
        default_factory=list,
        description=(
            "드롭할 프레임워크/런타임 함수 이름 (PFM_*/mpfm_*/PRINT_* 류 — 업무 로직 아님, "
            "호출 분류 시 그래프에서 드롭). ★프로젝트 자기 함수(code_nodes)는 절대 넣지 말 것 "
            "— 여기 넣으면 그 함수 호출이 그래프에서 사라진다."
        ),
    )

    no_match_reason: Optional[str] = None

    @model_validator(mode="after")
    def validate_verified_scan_contract(self) -> "ScanResult":
        if self.analysis_status == "unavailable":
            if (
                self.matched_skill is not None
                or self.table_access_rules
                or self.table_access_patterns
                or self.table_access_exclusion_rules
                or self.call_wrappers
                or self.handler_markers
                or self.api_contracts
                or self.framework_runtime_functions
            ):
                raise ValueError("unavailable scan은 downstream 계약/skill을 포함할 수 없음")
            if not (self.no_match_reason or "").strip():
                raise ValueError("unavailable scan은 no_match_reason이 필수")
            if self.table_access_status != "unavailable":
                raise ValueError("unavailable scan은 table_access_status도 unavailable이어야 함")
            return self
        if self.table_access_status == "unavailable":
            if (
                self.table_access_rules
                or self.table_access_patterns
                or self.table_access_exclusion_rules
                or self.ddl_transform != DdlTransform(ddl_naming_rule="as_is")
            ):
                raise ValueError(
                    "table access unavailable은 검증되지 않은 R/W 규칙/변환을 포함할 수 없음"
                )
            if not (self.no_match_reason or "").strip():
                raise ValueError("table access unavailable은 no_match_reason이 필수")
        elif self.no_match_reason is not None:
            raise ValueError("completed table access는 no_match_reason을 포함할 수 없음")
        if self.matched_skill is None and self.table_access_exclusion_rules:
            raise ValueError(
                "table_access_exclusion_rules는 검토 가능한 matched SKILL 계약에서만 허용"
            )
        if (
            self.matched_skill is None
            and not self.table_access_rules
            and not self.table_access_patterns
            and not (self.no_match_reason or "").strip()
        ):
            raise ValueError(
                "SKILL 미매칭인데 table access 규칙/패턴이 없으면 no_match_reason 필수"
            )
        return self


# ═══════════════════════════════════════════════════════════════
# 잔여 패턴 루프 출력
# ═══════════════════════════════════════════════════════════════
#
# step5의 결정론 R/W 링킹이 못 푼 "잔여" 테이블 케이스를 — 케이스마다 추적하지
# 않고, 샘플에서 공통 패턴을 찾아 코드가 일괄 적용한다. 이 모델은 그 루프 한
# 라운드에서 에이전트가 발견한 패턴이다.


class ResidualPatternResult(StrictAgentOutput):
    """잔여 패턴 루프 — 단일 구조화 제안의 출력.

    잔여 미해결 테이블 케이스 샘플에서 발견한 공통 접근 패턴. ``ScanResult`` 와
    구성 요소(``TableAccessPattern`` / ``DdlTransform``)는 공유하되, 기존 변환을 그대로
    복사하지 않고 바꿀 때만 override를 낸다.
    """
    table_access_patterns: list[ProposedTableAccessPattern] = Field(
        ...,
        description="잔여 케이스의 새 공통 접근 패턴. production 코드가 검증한다",
    )
    ddl_transform_override: Optional[DdlTransform] = Field(
        ...,
        description="현재 entity→DDL 변환을 바꿔야 할 때만 새 전체 값, 아니면 null",
    )
