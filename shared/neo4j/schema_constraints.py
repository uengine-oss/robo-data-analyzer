"""Neo4j 인덱스 / 제약 일괄 생성.

run 시작 시 1회 호출 (``ensure_schema()``).
``IF NOT EXISTS`` — 이미 있으면 무시. 스키마는 안정적이라 매 run 새로 만들지 않는다.

라벨은 UPPER_SNAKE (plan §4 명명 규칙).
새 노드 종류 추가 시: 이 파일에 CONSTRAINT 1줄 + INDEX (필요 시) 추가.
"""
from __future__ import annotations

from shared.neo4j.neo4j_client import Neo4jClient


# 노드 유일성 제약 (UNIQUE) — 모든 노드 식별 키 = ``id`` 단일 (종류 무관).
#   값은 노드별 고유 문자열 (DB=db.schema.name / 코드=fqn형태 / EM=해시·합성키).
# 제약 이름은 ``uniq_<label>`` — 옛 버전이 ``module_id`` 등 동일 이름을 옛 속성(m.module_id)에
# 걸어둔 더티 DB 에서도 ``IF NOT EXISTS`` 가 (이름 충돌로) 새 ``.id`` 제약 생성을 막지 않도록
# 새 이름 사용. (이미 .id 에 동등 제약이 있으면 IF NOT EXISTS 가 no-op — clean·dirty 양쪽 안전.)
# ★코드 노드(파일/클래스/함수/메서드/구문/변수/상수/데이터타입)는 라벨 = 자기 antlr type
#   (METHOD/FUNCTION/SELECT/FIELD/STRUCT…) = **열린 집합**이라 종류별 제약 열거 불가.
#   식별 키 ``id`` 가 전역 유일이고 MERGE 가 id 로 dedup → 코드 노드는 제약 없이 안전.
#   안정 라벨(DB / EM / PACKAGE)만 id-유일 제약을 둔다.
CONSTRAINTS: list[str] = [
    "CREATE CONSTRAINT uniq_schema IF NOT EXISTS "
    "FOR (s:SCHEMA) REQUIRE s.id IS UNIQUE",
    "CREATE CONSTRAINT uniq_table IF NOT EXISTS "
    "FOR (t:TABLE) REQUIRE t.id IS UNIQUE",
    "CREATE CONSTRAINT uniq_column IF NOT EXISTS "
    "FOR (c:COLUMN) REQUIRE c.id IS UNIQUE",
    "CREATE CONSTRAINT uniq_package IF NOT EXISTS "
    "FOR (p:PACKAGE) REQUIRE p.id IS UNIQUE",
    # Example Mapping 노드 (analyze_code 3분할 → EM 빌더)
    "CREATE CONSTRAINT uniq_rule IF NOT EXISTS "
    "FOR (r:RULE) REQUIRE r.id IS UNIQUE",
    "CREATE CONSTRAINT uniq_example IF NOT EXISTS "
    "FOR (e:EXAMPLE) REQUIRE e.id IS UNIQUE",
    "CREATE CONSTRAINT uniq_question IF NOT EXISTS "
    "FOR (q:QUESTION) REQUIRE q.id IS UNIQUE",
    # 컴포넌트는 합성 노드(:COMPONENT)가 없으므로 제약 불필요 — 멤버십은 component_id 속성.
]

# 조회 인덱스 (안정 라벨만 — 코드 노드는 열린 antlr type 라벨이라 id 키로 조회).
INDEXES: list[str] = [
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
