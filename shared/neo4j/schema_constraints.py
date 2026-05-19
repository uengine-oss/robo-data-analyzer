"""Neo4j 인덱스 / 제약 일괄 생성.

run 시작 시 1회 호출 (``ensure_schema()``).
``IF NOT EXISTS`` 라 idempotent — 이미 있으면 무시.

새 노드 종류 추가 시: 이 파일에 CONSTRAINT 1줄 + INDEX (필요 시) 추가.
"""
from __future__ import annotations

from shared.neo4j.neo4j_client import Neo4jClient


# 노드 유일성 제약 (UNIQUE)
CONSTRAINTS: list[str] = [
    # Schema: db.name (fqn) 유일
    "CREATE CONSTRAINT schema_db_name IF NOT EXISTS "
    "FOR (s:Schema) REQUIRE (s.db, s.name) IS UNIQUE",

    # Table: fqn ("db.schema.name") 단일 식별 — reads_writes 엣지의 MATCH 키와 통일.
    "CREATE CONSTRAINT table_fqn IF NOT EXISTS "
    "FOR (t:Table) REQUIRE t.fqn IS UNIQUE",

    # Column: fqn 유일
    "CREATE CONSTRAINT column_fqn IF NOT EXISTS "
    "FOR (c:Column) REQUIRE c.fqn IS UNIQUE",

    # Function: function_id 유일
    "CREATE CONSTRAINT function_id IF NOT EXISTS "
    "FOR (f:Function) REQUIRE f.function_id IS UNIQUE",

    # Module: module_id 유일
    "CREATE CONSTRAINT module_id IF NOT EXISTS "
    "FOR (m:Module) REQUIRE m.module_id IS UNIQUE",

    # Actor: actor_id 유일
    "CREATE CONSTRAINT actor_id IF NOT EXISTS "
    "FOR (a:Actor) REQUIRE a.actor_id IS UNIQUE",

    # SqlStatement / ConfigSection / UnresolvedRef (refer_to 엣지의 target 노드)
    "CREATE CONSTRAINT sql_statement_key IF NOT EXISTS "
    "FOR (s:SqlStatement) REQUIRE s.sql_key IS UNIQUE",
    "CREATE CONSTRAINT config_section_key IF NOT EXISTS "
    "FOR (c:ConfigSection) REQUIRE c.config_key IS UNIQUE",
    "CREATE CONSTRAINT unresolved_ref_key IF NOT EXISTS "
    "FOR (u:UnresolvedRef) REQUIRE u.target_key IS UNIQUE",
]

# 조회 인덱스
INDEXES: list[str] = [
    "CREATE INDEX function_file_path IF NOT EXISTS FOR (f:Function) ON (f.file_path)",
    "CREATE INDEX function_parent_id IF NOT EXISTS FOR (f:Function) ON (f.parent_id)",
    "CREATE INDEX table_schema IF NOT EXISTS FOR (t:Table) ON (t.schema)",
]


async def ensure_schema(client: Neo4jClient) -> None:
    """모든 제약 + 인덱스 생성 (idempotent). run 시작 시 1회 호출."""
    for stmt in CONSTRAINTS + INDEXES:
        await client.run_query(stmt)
