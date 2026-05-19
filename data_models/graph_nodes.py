"""그래프 노드 dataclass — Framework / DBMS 양쪽이 공유.

설계 원칙
─────────
- Framework 와 DBMS 양쪽이 **같은 dataclass** 사용 (분기 0개).
- DBMS 의 nested DML range 도 ``Function`` 으로 흡수 (``parent_id`` 필드로 트리 표현).
- Neo4j 에 박힐 때는 ``shared/neo4j/nodes/<type>.py`` 의 Cypher 쿼리가 변환.

새 노드 종류 추가 시 손대는 곳
─────────────────────────────
1. 이 파일에 dataclass 추가
2. ``shared/neo4j/nodes/<new>.py`` 에 Cypher 쿼리 추가

두 곳 외에는 손댈 필요 X.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ── DB 객체 (DDL 파싱에서만 생성, 이후 단계는 신규 생성 X) ──

@dataclass(frozen=True)
class Schema:
    """DB 스키마 (예: Oracle SCOTT, PostgreSQL public).

    DDL 파싱 step (parse_ddl) 에서만 생성. 이후 단계는 신규 Schema 생성 X.
    """
    db: str                                  # datasource 이름 (예: "prod_main")
    name: str                                # 스키마 이름 (예: "PUBLIC")


@dataclass(frozen=True)
class Table:
    """DB 테이블.

    DDL 파싱에서만 생성 (Plan §3.1: DDL = 진실 기준). 이후 단계는 R/W 엣지만 추가.
    ``description`` 필드는 ``enrich_table_description`` step 이 채움.
    """
    db: str
    schema: str
    name: str
    description: str = ""                    # enrich_table_description 가 채움
    table_type: str = "TABLE"                # TABLE / VIEW / MATERIALIZED_VIEW


@dataclass(frozen=True)
class Column:
    """DB 컬럼.

    DDL 파싱에서만 생성. ``description`` 은 analyze_function 의 3 분할
    ``tableInsights`` 가 채움.

    fqn = ``"<db>.<schema>.<table>.<column>"`` — Neo4j unique key.
    """
    fqn: str                                 # "prod_main.PUBLIC.USERS.user_id"
    name: str                                # "user_id"
    dtype: str                               # "VARCHAR(32)" 등
    nullable: bool = True
    is_primary_key: bool = False
    is_unique: bool = False
    default_value: Optional[str] = None
    description: str = ""                    # analyze_function tableInsights 가 채움


# ── 코드 객체 (parse_source / analyze_function 이 채움) ──

@dataclass
class Function:
    """함수 / 메서드 / 프로시저 — 분석의 기본 단위.

    Framework 예: Java method / Python function / C function.
    DBMS 예: PL/SQL procedure / function / nested DML range (``parent_id`` 로 트리).

    ``summary`` 슬롯은 ``build_function_context`` step 에서는 비워두고
    ``analyze_function`` step 의 Bottom-up DAG 가 leaf → root 순으로 채움.

    Note: ``frozen=False`` — ``analyze_function`` 이 in-place 로 ``summary`` 등 채워야 함.
    """
    function_id: str                         # fqn 또는 "<file>:<proc>:<line>"
    signature: str                           # "save(Order order) -> OrderResult"
    file_path: str                           # 절대 또는 프로젝트 상대 경로
    start_line: int
    end_line: int

    # 트리 구조 (DBMS nested range 흡수 — Framework 는 거의 None)
    parent_id: Optional[str] = None

    # 분석 결과 (analyze_function 이 채움) ────
    summary: Optional[str] = None            # 3 섹션 마크다운 (핵심/비즈니스로직/반환)
    stereotype: Optional[str] = None         # ontology_roles.yaml 풀에서
    actor: Optional[str] = None              # 비즈니스 actor
    logical_name: Optional[str] = None       # 짧은 도메인 토큰 (≤ 25자)

    # strategy 별 확장 필드 (dict — 새 필드 추가 시 base 무변경) ────
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Module:
    """모듈 / 클래스 / 패키지 — Function 들의 컨테이너.

    Framework 예: Java class / Python module / Go package.
    DBMS 예: PL/SQL package.

    ``start_line`` / ``end_line`` 으로 한 파일 안 여러 클래스 (Java inner class 등)
    의 정확한 라인 범위 구분 → ``summarize_module`` 의 정확한 매칭 가능.

    ``summary`` 는 ``summarize_module`` step 이 채움.
    """
    module_id: str                           # fqn (예: "com.example.OrderService")
    name: str
    file_path: str
    start_line: int = 0                      # 0 = 파일 전체 (file-level module)
    end_line: int = 0                        # 0 = 파일 전체
    summary: str = ""                        # summarize_module 가 채움
    module_stereotype: str = ""              # summarize_module 가 채움


@dataclass(frozen=True)
class Actor:
    """비즈니스 actor (cross-module 통합).

    여러 함수의 ``actor`` 필드가 동의어로 묶이면 한 ``Actor`` 노드로 합침.
    ``prompts/<strategy>/actor_merge.yaml`` 이 동의어 클러스터링.
    """
    actor_id: str                            # 정규화된 actor 이름
    display_name: str
    member_function_ids: tuple[str, ...] = ()  # 이 actor 에 속하는 함수들 (sorted 고정)
