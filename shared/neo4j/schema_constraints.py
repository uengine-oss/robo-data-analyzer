"""Neo4j 인덱스 / 제약 일괄 생성.

run 시작 시 1회 호출 (``ensure_schema()``).
``IF NOT EXISTS`` — 이미 있으면 무시. 스키마는 안정적이라 매 run 새로 만들지 않는다.

라벨은 UPPER_SNAKE (plan §4 명명 규칙).
새 노드 종류 추가 시: 이 파일에 CONSTRAINT 1줄 + INDEX (필요 시) 추가.
"""
from __future__ import annotations

from shared.neo4j.neo4j_client import Neo4jClient


# 노드 유일성 제약 (UNIQUE)
CONSTRAINTS: list[str] = [
    # DB 노드 — 키 = fqn
    "CREATE CONSTRAINT schema_db_name IF NOT EXISTS "
    "FOR (s:SCHEMA) REQUIRE (s.db, s.name) IS UNIQUE",
    "CREATE CONSTRAINT table_fqn IF NOT EXISTS "
    "FOR (t:TABLE) REQUIRE t.fqn IS UNIQUE",
    "CREATE CONSTRAINT column_fqn IF NOT EXISTS "
    "FOR (c:COLUMN) REQUIRE c.fqn IS UNIQUE",

    # 코드 노드 — 키 = <타입>_id
    "CREATE CONSTRAINT function_id IF NOT EXISTS "
    "FOR (f:FUNCTION) REQUIRE f.function_id IS UNIQUE",
    "CREATE CONSTRAINT module_id IF NOT EXISTS "
    "FOR (m:MODULE) REQUIRE m.module_id IS UNIQUE",
    "CREATE CONSTRAINT package_id IF NOT EXISTS "
    "FOR (p:PACKAGE) REQUIRE p.package_id IS UNIQUE",
    "CREATE CONSTRAINT variable_id IF NOT EXISTS "
    "FOR (v:VARIABLE) REQUIRE v.variable_id IS UNIQUE",
    "CREATE CONSTRAINT constant_id IF NOT EXISTS "
    "FOR (c:CONSTANT) REQUIRE c.constant_id IS UNIQUE",
    "CREATE CONSTRAINT data_type_id IF NOT EXISTS "
    "FOR (d:DATA_TYPE) REQUIRE d.datatype_id IS UNIQUE",

    # refer_to 엣지의 target 노드 (architect_chunk 링킹은 보류 — 계획 §2.8 Issue I)
    "CREATE CONSTRAINT architect_chunk_key IF NOT EXISTS "
    "FOR (c:ARCHITECT_CHUNK) REQUIRE c.chunk_key IS UNIQUE",

    # Example Mapping 노드 (analyze_function 3분할 → EM 빌더)
    "CREATE CONSTRAINT rule_hash IF NOT EXISTS "
    "FOR (r:RULE) REQUIRE r.rule_hash IS UNIQUE",
    "CREATE CONSTRAINT example_id IF NOT EXISTS "
    "FOR (e:EXAMPLE) REQUIRE e.example_id IS UNIQUE",
    "CREATE CONSTRAINT question_id IF NOT EXISTS "
    "FOR (q:QUESTION) REQUIRE q.question_id IS UNIQUE",
]

# 조회 인덱스
INDEXES: list[str] = [
    "CREATE INDEX function_file_path IF NOT EXISTS FOR (f:FUNCTION) ON (f.file_path)",
    "CREATE INDEX function_parent_id IF NOT EXISTS FOR (f:FUNCTION) ON (f.parent_id)",
    "CREATE INDEX table_schema IF NOT EXISTS FOR (t:TABLE) ON (t.schema)",
]


async def ensure_schema(client: Neo4jClient) -> None:
    """제약 + 인덱스 생성 (``IF NOT EXISTS`` — 이미 있으면 무시). run 시작 시 1회.

    스키마는 안정적 — 매 run drop-recreate 하지 않는다 (다른 프로젝트와 DB 를 공유할 경우
    drop-all 은 남의 스키마까지 파괴). 단 옛 버전 잔재 인덱스가 제약 자리를 차지하면
    충돌하므로 — analyzer 는 깨끗한 전용 DB 를 써야 한다.
    """
    for stmt in CONSTRAINTS + INDEXES:
        await client.run_query(stmt)


async def wipe_graph(client: Neo4jClient) -> None:
    """모든 노드 + 엣지 삭제 — 매 run 시작 시 호출 (테스트 정책: 깨끗한 상태에서 분석).

    옛 run 의 잔재 (노드/엣지) 가 새 분석 결과와 섞이면 검증 사각지대.
    ``DETACH DELETE`` = 노드 + 그 노드의 모든 엣지 동시 삭제.
    제약 / 인덱스 (schema) 는 보존 — ``ensure_schema`` 가 idempotent.
    """
    await client.run_query("MATCH (n) DETACH DELETE n")
