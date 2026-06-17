"""그래프 엣지 dataclass — Framework / DBMS 양쪽이 공유.

설계 원칙
─────────
- Framework / DBMS 양쪽이 같은 dataclass 사용 (분기 0개).
- DBMS 만의 PARENT_OF 도 공통 엣지로 포함 (Function 의 nested 표현).
- Neo4j 에 박힐 때는 ``shared/neo4j/edges/<type>.py`` 의 Cypher 쿼리가 변환.

endpoint 필드 = 가리키는 노드의 ``id`` (관계 역할명)
────────────────────────────────────────────────────
끝점은 관계 역할로 부른다 — 호출 ``caller_id``/``callee_id``, 트리·포함 ``parent_id``/
``child_id``(parent=컨테이너), 그 외 ``source_id``/``target_id``. 값은 그 노드의 ``id``.
같은 끝점 쌍이 raw 중복 들어와도 **Neo4j MERGE 가 자동 dedup** — evidence_* 같은 부속
메타는 정체성 무관.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional


# ── 공용 헬퍼 — dml_type 표준화 (정체성 외부) ────────────────

def derive_dml_type(standard_op: Optional[str]) -> str:
    """``standard_op`` (SELECT/INSERT/UPDATE/DELETE/MERGE) → ``dml_type`` 문자열.

    None / 빈값 / 정규화 실패 시 빈 문자열. ReadsWrites 만드는 모든 지점이
    공유 — 비표준 op 박는 사고 차단 (단일 진실).
    """
    if not standard_op:
        return ""
    v = standard_op.strip().upper()
    if v in ("SELECT", "INSERT", "UPDATE", "DELETE", "MERGE"):
        return v
    return ""


# ── 스키마 구조 엣지 (DDL 파싱에서만 생성) ──────────────────

@dataclass(frozen=True)
class HasColumn:
    """Table → Column (1:N). parent=table, child=column."""
    parent_id: str                           # table id "db.schema.table"
    child_id: str                            # column id "db.schema.table.column"


@dataclass(frozen=True)
class BelongsTo:
    """Table → Schema. child=table, parent=schema."""
    child_id: str                            # table id
    parent_id: str                           # schema id "db.schema"


@dataclass(frozen=True)
class Fk:
    """Table → Table (외래키).

    DDL 에서만 생성. ``fk_type`` 은 sqlglot 분석 또는 ``schema_resolver`` 결과.
    ``is_candidate=True`` 는 DDL 에 명시되지 않은 추론된 FK (사람 검토 큐).
    """
    from_id: str                             # 출발 table id
    to_id: str                               # 도착 table id
    from_column: str
    to_column: str
    fk_type: Literal["one_to_one", "one_to_many", "many_to_one", "many_to_many"] = "many_to_one"
    is_candidate: bool = False


# ── Function ↔ Table (Linking 결과) ────────────────────────

@dataclass(frozen=True)
class ReadsWrites:
    """Function → Table (R/W 엣지). source=function, target=table.

    Linking 1 단계 결정론 grep / DBMS DML 매칭 / Linking Agent (stage3) 가 생성.

    정체성 = (source_id, target_id, relation). dml_type 과 evidence_* 는 부속 메타 —
    같은 함수가 같은 테이블을 같은 동작으로 여러 라인에서 호출해도 그래프엔 1 엣지
    (Neo4j MERGE 가 dedup). dml_type 은 ``derive_dml_type(standard_op)`` 헬퍼로 생성.
    """
    source_id: str
    target_id: str
    relation: Literal["READS", "WRITES"]
    dml_type: str = ""                       # SELECT/INSERT/UPDATE/DELETE/MERGE — relation 보다 세분 (DBMS)
    evidence_file: str = ""                  # 발견된 파일
    evidence_line: int = 0                   # 발견된 라인


def normalize_relation(value: str) -> Optional[Literal["READS", "WRITES"]]:
    """R/W 표기 정규화 — ``READ``/``READS`` → ``READS``, ``WRITE``/``WRITES`` → ``WRITES``.

    대소문자 무시. 그 외 값은 ``None`` — 호출자가 기본값 적용·skip 을 결정한다.
    ``ReadsWrites`` 를 만드는 여러 곳(정규식 링킹·catalog 리니지)이 공유.
    """
    v = value.strip().upper()
    if v in ("READ", "READS"):
        return "READS"
    if v in ("WRITE", "WRITES"):
        return "WRITES"
    return None


# ── Function ↔ Function (호출 / 부모-자식) ─────────────────

@dataclass(frozen=True)
class Calls:
    """Function → Function (호출 관계).

    Framework: 결정론 분류 (link_calls_by_name — 함수 이름 목록 대조).
    DBMS: AST CALL 노드 분류 (link_calls_by_ast — 빌트인·자기루프 드롭).

    scope:
    - ``internal``: 같은 코드베이스 안 함수 — 콜리 정의 노드가 이미 존재.
    - ``external``: 분석 범위 밖 실 함수/프로시저 (예: 자기 스키마의 미분석 프로시저,
      다른 스키마/라이브러리). 콜리 정의 노드가 없어 ``upsert_calls`` 가
      ``:FUNCTION:EXTERNAL`` stub 노드를 모듈·스키마와 함께 internal 과 대칭으로 생성.

    external 분해 메타 (``callee_*``) — strategy 별 linker 가 **언어 의미**로 채운다
    (DBMS: module=프로시저·package=스키마, Framework: module=클래스·package=패키지).
    internal 은 콜리 노드가 이미 있어 빈 값.

    정체성 = (caller_id, callee_id). scope·callee_* 는 부속 — 같은 호출이 두 번 분류돼도 그래프엔 1 엣지.
    """
    caller_id: str
    callee_id: str
    scope: Literal["internal", "external"] = "internal"
    callee_module: str = ""                  # external 콜리의 모듈 id (DBMS=프로시저명 / FW=클래스)
    callee_package: str = ""                 # external 콜리의 패키지 id — 스키마(DBMS) / 패키지(FW)
    callee_name: str = ""                    # external 콜리 표시명


@dataclass(frozen=True)
class ParentOf:
    """Function → nested Function. parent=상위, child=중첩.

    DBMS 의 PL/SQL procedure 안 SELECT/IF/CASE 같은 분석 단위가 parent 의 child.
    Framework 는 거의 안 씀 (메서드는 평면 구조).

    Bottom-up DAG 알고리즘은 ``Calls`` / ``ParentOf`` 둘 다 같은 그래프 입력으로 처리.
    """
    parent_id: str
    child_id: str


# 컴포넌트 멤버십/그룹간 호출은 엣지로 저장하지 않는다 — 멤버십은 FUNCTION.component_id 속성,
# 그룹↔그룹 호출은 CALLS + component_id 로 온디맨드 GROUP BY (build_components_step 참고).


# ── 코드 구조 엣지 (load_ast 가 antlr JSON 트리에서 생성) ──

@dataclass(frozen=True)
class HasFunction:
    """Module → Function (모듈이 함수를 품음). parent=module, child=function."""
    parent_id: str
    child_id: str


@dataclass(frozen=True)
class HasVariable:
    """Module → Variable (모듈 레벨 변수 — 클래스 필드 / 전역변수). parent=module, child=variable."""
    parent_id: str
    child_id: str


@dataclass(frozen=True)
class HasMember:
    """DataType → Variable (struct / union 의 멤버 변수). parent=data_type, child=variable."""
    parent_id: str
    child_id: str


@dataclass(frozen=True)
class BelongsToPackage:
    """Module → Package (모듈이 든 패키지 = 파일이 든 폴더). child=module, parent=package."""
    child_id: str
    parent_id: str


# ── 코드 참조 엣지 (결정론 식별자 매칭) ────────────────────

@dataclass(frozen=True)
class References:
    """Function → Variable / Constant (함수 본문이 참조하는 변수·상수). source=function, target=var/const.

    ``target_label`` 로 upsert 가 올바른 라벨로 MATCH (키는 항상 ``id``).
    정체성 = (source_id, target_id, target_label). evidence_line 은 부속.
    """
    source_id: str
    target_id: str
    target_label: Literal["VARIABLE", "CONSTANT"]
    evidence_line: int = 0


@dataclass(frozen=True)
class InitBy:
    """변수/상수 → 출처 노드 (정의 시 참조하는 다른 변수·상수).

    의미 = "정의 시 외부 참조" — ``=`` 우변뿐 아니라 변수 size 표현식
    (``static char x [LEN +1]``) · 상수 식 (``#define B (A+1)``) 등 모두 포함.
    Python 쪽이 source 라인을 직접 읽어 토큰 추출하므로 antlr 가 못 준 의존도 잡힌다.

    ``from_label`` / ``source_label`` 로 upsert 가 올바른 라벨로 MATCH (키는 항상 ``id``).
    """
    from_id: str                              # 출발: Variable 또는 Constant id
    from_label: Literal["VARIABLE", "CONSTANT"]
    source_id: str
    source_label: Literal["VARIABLE", "CONSTANT"]


# ── Example Mapping 엣지 (analyze_function 3분할 → EM 빌더가 생성) ──

@dataclass(frozen=True)
class HasRule:
    """Function → Rule. source=function, target=rule. Rule 노드는 함수 간 공유되므로 per-function 정보는 이 엣지 속성.

    정체성 = (source_id, target_id). local_rule_id / flow_id 등은 부속 메타.
    flow_id 의 ``-`` 계층이 분기 구조를 표현 (BRANCH 엣지 자동 도출).
    """
    source_id: str
    target_id: str
    local_rule_id: str                       # 이 함수 안에서의 "R1" 등
    flow_id: str = ""
    coupled_domains: tuple[str, ...] = ()


@dataclass(frozen=True)
class HasExample:
    """Rule → Example. source=rule, target=example."""
    source_id: str
    target_id: str


@dataclass(frozen=True)
class HasQuestion:
    """Function → Question. source=function, target=question."""
    source_id: str
    target_id: str


@dataclass(frozen=True)
class Next:
    """Rule → Rule (메인 흐름 순서). 같은 함수(owner) 안 — HAS_RULE local_rule_id 로 매칭."""
    owner_id: str                            # 규칙들을 품은 함수의 id
    from_rule_id: str                        # local rule_id ("R1" 등)
    to_rule_id: str


@dataclass(frozen=True)
class Branch:
    """Rule → Rule (분기). 같은 함수(owner) 안 — HAS_RULE local_rule_id 로 매칭."""
    owner_id: str                            # 규칙들을 품은 함수의 id
    from_rule_id: str
    to_rule_id: str


@dataclass(frozen=True)
class AffectsTable:
    """Example → Table. source=example, table_name=이름매칭. op(INSERT/UPDATE/DELETE/WRITE/READ) 별."""
    source_id: str
    table_name: str
    op: str
