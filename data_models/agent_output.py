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


class OpMapping(BaseModel):
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


class TableAccessPattern(BaseModel):
    """함수 → 테이블 접근 정규식 매칭 패턴 (Linking 1 단계 결정론 grep 용).

    ``call_regex`` 의 named group:
    - ``<op_token>``: 프레임워크 raw 매치 토큰 (예: 's', 'findOne', 'SELECT')
    - ``<entity>``: 테이블/엔티티 이름

    ``op_mappings`` 의 각 ``OpMapping`` 으로 token → 표준 SQL op (SELECT/INSERT/UPDATE/DELETE) 변환.
    READ/WRITE 는 코드가 자동 매핑 (SELECT=READ, 나머지=WRITE).
    """
    id: str = Field(..., description="패턴 식별자 (예: 'ofbiz_delegator')")
    call_regex: str = Field(..., description="named group <op_token> + <entity> 포함")
    op_mappings: list[OpMapping] = Field(...)
    entity_source: str = Field(..., description="entity 추출 위치 (예: 'first_arg_string')")


class DdlTransform(BaseModel):
    """엔티티 이름 → DDL 테이블 이름 변환 규칙."""
    ddl_naming_rule: str = Field(
        ..., description="예: 'as_is' / 'camel_to_snake_upper' / 'snake_to_camel'"
    )
    fallback_prefixes: list[str] = Field(default_factory=list)


# ───────────────────────────────────────────────────────────────
# 호출 wrapper — 인자(이름 string)가 실제 호출 대상인 함수
# ───────────────────────────────────────────────────────────────
#
# wrapper 자체는 ``framework_functions`` 에 들어가 호출 그래프에서 드롭되지만,
# 그 인자가 실제 호출 타겟(다른 함수/모듈)인 경우 — 그 인자를 추출해 ``Calls`` 엣지로
# 정합한다. 별도 카테고리/별도 step 이 아니라 ``call_classifier`` 안에서 처리 — 호출은
# 한 영역 (Calls 엣지) 으로 단일화.
#
# 적용 framework 예 (개념적):
# - 런타임 동적 dispatch (이름 string 으로 호출)
# - IoC 컨테이너 lookup (이름으로 빈/서비스 조회)
# - 리플렉션 호출 (메서드 이름 / 모듈 이름)


class CallWrapper(BaseModel):
    """호출 wrapper 명시 — wrapper 함수 이름 + 인자 추출 위치.

    SKILL 의 ``## 호출 wrapper`` 절 본문을 **그대로 옮겨 담는다** (matched SKILL 에
    절이 있으면 반드시 채움). 누락 시 wrapper 호출이 호출 그래프에서 사라짐 (framework
    drop 으로 빠짐) — 모듈 간 의존관계 손실 사고. 대부분 빈 배열이 정답이지만
    (JPA/MyBatis/OFBiz 등은 wrapper 없음), ProFrame 류는 반드시 옮겨야 함.

    ``call_classifier`` 가 wrapper 호출을 만나면 그 라인의 본문에서 인자를 추출해
    실제 호출 대상으로 분류한다 (functions 안에 있으면 internal, 없으면 external).
    """
    wrapper: str = Field(
        ...,
        description=(
            "wrapper 함수 이름 — SKILL 의 ``## 호출 wrapper`` yaml 의 ``wrapper`` 값 "
            "그대로 (예: ProFrame ``mpfm_dlcall``). framework_functions 에도 동시 포함되지만 "
            "인자가 진짜 호출 대상이라 별도 분류."
        ),
    )
    callee_from: str = Field(
        "first_arg_string",
        description=(
            "호출 대상을 어디서 뽑을지 — SKILL 의 ``callee_from`` 값 그대로. "
            "지원: 'first_arg_string' (첫 인자 큰따옴표) / 'first_arg_identifier' "
            "(첫 인자 식별자). SKILL 이 wrapper 의 의미에 맞게 명시."
        ),
    )


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

    # 호출 wrapper — 인자가 실제 호출 대상인 함수 (SKILL 이 정의). 없으면 빈배열.
    # ``call_classifier`` 가 wrapper 호출을 만나면 인자를 추출해 Calls 엣지로 정합.
    call_wrappers: list[CallWrapper] = Field(default_factory=list)

    # 분석용 텍스트 (step6_analyze_function 컨텍스트 + 잔여 패턴 루프 prefix)
    codebase_notes: str = Field(
        "",
        description=(
            "프로젝트 성격 / wrapper / 명명 컨벤션 + op 결정 신호 1단락. "
            "함수 분석 LLM 호출마다 prefix 로 주입되므로 짧고 정확해야 한다. "
            "★ op_mappings 의 token→표준 SQL op (SELECT/INSERT/UPDATE/DELETE) 매핑을 "
            "1줄로 풀어 반드시 포함 (예: 'SQL_KEY <op>: s=SELECT, i=INSERT...'). "
            "누락 시 함수 분석 LLM 이 코드 주석에 끌려 op 를 잘못 해석."
        ),
    )

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
