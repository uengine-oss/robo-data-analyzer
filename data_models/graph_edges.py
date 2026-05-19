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
from typing import Literal


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

    Linking 1 단계 결정론 grep 또는 Linking Agent (stage3) 가 생성.
    """
    function_id: str
    table_fqn: str
    relation: Literal["READS", "WRITES"]
    evidence_file: str = ""                  # 발견된 파일
    evidence_line: int = 0                   # 발견된 라인


# ── Function ↔ Function (호출 / 부모-자식) ─────────────────

@dataclass(frozen=True)
class Calls:
    """Function → Function (호출 관계).

    Framework: LLM 배치 (link_calls_by_llm) 또는 Agent (stage3_resolve_unresolved).
    DBMS: regex 추출 (link_calls_by_regex — PKG.FUNC 단순 패턴).

    scope:
    - ``internal``: 같은 코드베이스 안 함수
    - ``external``: 외부 라이브러리 (예: java.util.*, DBMS_/UTL_ 내장)
    - ``unresolved``: Linking Agent 도 못 푼 케이스 (사람 검토 큐)
    """
    caller_function_id: str
    callee_function_id: str
    scope: Literal["internal", "external", "unresolved"] = "internal"


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
    """Function → 외부 자원 (SQL statement / Config / unresolved 등).

    target_type:
    - ``sql_statement``: XML mapper 의 namespace.id
    - ``config_section``: 설정 파일의 키
    - ``unresolved``: 사람 검토 큐
    """
    function_id: str
    target_type: Literal["sql_statement", "config_section", "unresolved"]
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
