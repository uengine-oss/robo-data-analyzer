"""그래프 엣지 dataclass — Framework / DBMS 양쪽이 공유.

설계 원칙
─────────
- Framework / DBMS 양쪽이 같은 dataclass 사용 (분기 0개).
- DBMS 만의 PARENT_OF 도 공통 엣지로 포함 (Function 의 nested 표현).
- Neo4j 에 박힐 때는 ``shared/neo4j/edges/<type>.py`` 의 Cypher 쿼리가 변환.

새 엣지 종류 추가 시 손대는 곳
─────────────────────────────
1. 이 파일에 dataclass 추가
2. ``shared/neo4j/edges/<new>.py`` 에 Cypher 쿼리 추가
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional


# ── 스키마 구조 엣지 (DDL 파싱에서만 생성) ──────────────────

@dataclass(frozen=True)
class HasColumn:
    """Table → Column (1:N)."""
    table_fqn: str                           # "db.schema.table"
    column_fqn: str                          # "db.schema.table.column"


@dataclass(frozen=True)
class BelongsTo:
    """Table → Schema."""
    table_fqn: str
    schema_fqn: str                          # "db.schema"


@dataclass(frozen=True)
class Fk:
    """Table → Table (외래키).

    DDL 에서만 생성. ``fk_type`` 은 sqlglot 분석 또는 ``schema_resolver`` 결과.
    ``is_candidate=True`` 는 DDL 에 명시되지 않은 추론된 FK (사람 검토 큐).
    """
    from_table_fqn: str
    to_table_fqn: str
    from_column: str
    to_column: str
    fk_type: Literal["one_to_one", "one_to_many", "many_to_one", "many_to_many"] = "many_to_one"
    is_candidate: bool = False


# ── Function ↔ Table (Linking 결과) ────────────────────────

@dataclass(frozen=True)
class ReadsWrites:
    """Function → Table (R/W 엣지).

    Linking 1 단계 결정론 grep / DBMS DML 매칭 / Linking Agent (stage3) 가 생성.
    """
    function_id: str
    table_fqn: str
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
    DBMS: regex 추출 (link_calls_by_regex — PKG.FUNC 단순 패턴).

    scope:
    - ``internal``: 같은 코드베이스 안 함수
    - ``external``: 분석 범위 밖 함수 / 외부 라이브러리 (예: java.util.*, DBMS_/UTL_ 내장)
    """
    caller_function_id: str
    callee_function_id: str
    scope: Literal["internal", "external"] = "internal"


@dataclass(frozen=True)
class ParentOf:
    """Function → nested Function.

    DBMS 의 PL/SQL procedure 안 SELECT/IF/CASE 같은 분석 단위가 parent_function 의 child.
    Framework 는 거의 안 씀 (메서드는 평면 구조).

    Bottom-up DAG 알고리즘은 ``Calls`` / ``ParentOf`` 둘 다 같은 그래프 입력으로 처리.
    """
    parent_function_id: str
    child_function_id: str


# ── Function ↔ 외부 자원 ──────────────────────────────────

@dataclass(frozen=True)
class ReferTo:
    """Function → 구조화 파일의 키-값 청크.

    target_type ``architect_chunk``: 구조화 파일(XML 등)의 키-값 청크 (ArchitectChunk 노드).
    종류(sql/config) 안 따짐 — 키 있으면 청크.

    ※ 보류 — architect 청크 링킹은 재설계 전까지 파이프라인에서 비활성
      (계획 §2.8 Issue I). 이 dataclass·refer_to upsert 인프라는 휴면 보존 — 삭제 금지.
    """
    function_id: str
    target_type: Literal["architect_chunk"]
    target_key: str
    evidence_file: str = ""
    evidence_line: int = 0


# ── Module ↔ Module ───────────────────────────────────────

@dataclass(frozen=True)
class Uses:
    """Module → Module (모듈간 사용).

    Framework 의 import / DBMS 의 package 참조 등.

    scope:
    - ``internal``: 같은 프로젝트 내
    - ``external``: 외부 라이브러리 의존
    """
    from_module_id: str
    to_module_id: str
    scope: Literal["internal", "external"] = "internal"


# ── 코드 구조 엣지 (load_ast 가 antlr JSON 트리에서 생성) ──

@dataclass(frozen=True)
class HasFunction:
    """Module → Function (모듈이 함수를 품음)."""
    module_id: str
    function_id: str


@dataclass(frozen=True)
class HasVariable:
    """Module → Variable (모듈 레벨 변수 — 클래스 필드 / 전역변수)."""
    module_id: str
    variable_id: str


@dataclass(frozen=True)
class HasMember:
    """DataType → Variable (struct / union 의 멤버 변수)."""
    datatype_id: str
    variable_id: str


@dataclass(frozen=True)
class BelongsToPackage:
    """Module → Package (모듈이 든 패키지 = 파일이 든 폴더)."""
    module_id: str
    package_id: str


# ── 코드 참조 엣지 (결정론 식별자 매칭) ────────────────────

@dataclass(frozen=True)
class References:
    """Function → Variable / Constant (함수 본문이 참조하는 변수·상수).

    ``target_label`` 로 upsert 가 올바른 키(variable_id / constant_id)로 MATCH.
    """
    function_id: str
    target_id: str
    target_label: Literal["VARIABLE", "CONSTANT"]
    evidence_line: int = 0


@dataclass(frozen=True)
class InitBy:
    """Variable → 출처 노드 (초기화식이 참조하는 변수·상수).

    ``source_label`` 로 upsert 가 올바른 키로 MATCH.
    """
    variable_id: str
    source_id: str
    source_label: Literal["VARIABLE", "CONSTANT"]


# ── Example Mapping 엣지 (analyze_function 3분할 → EM 빌더가 생성) ──

@dataclass(frozen=True)
class HasRule:
    """Function → Rule. Rule 노드는 함수 간 공유되므로 per-function 정보는 이 엣지 속성."""
    function_id: str
    rule_hash: str
    local_rule_id: str                       # 이 함수 안에서의 "R1" 등
    flow_id: str = ""
    guard_rule_id: Optional[str] = None
    branch_from: Optional[str] = None
    coupled_domains: tuple[str, ...] = ()


@dataclass(frozen=True)
class HasExample:
    """Rule → Example."""
    rule_hash: str
    example_id: str


@dataclass(frozen=True)
class HasQuestion:
    """Function → Question."""
    function_id: str
    question_id: str


@dataclass(frozen=True)
class Next:
    """Rule → Rule (메인 흐름 순서). 같은 함수 안 — HAS_RULE local_rule_id 로 매칭."""
    function_id: str
    from_rule_id: str                        # local rule_id ("R1" 등)
    to_rule_id: str


@dataclass(frozen=True)
class Branch:
    """Rule → Rule (분기). 같은 함수 안 — HAS_RULE local_rule_id 로 매칭."""
    function_id: str
    from_rule_id: str
    to_rule_id: str


@dataclass(frozen=True)
class AffectsTable:
    """Example → Table. op(INSERT/UPDATE/DELETE/WRITE/READ) 별."""
    example_id: str
    table_name: str
    op: str
