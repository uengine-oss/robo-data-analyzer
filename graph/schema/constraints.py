"""Neo4j 인덱스 / 제약 일괄 생성.

run 시작 시 1회 호출 (``ensure_schema()``).
``IF NOT EXISTS`` — 이미 있으면 무시. 스키마는 안정적이라 매 run 새로 만들지 않는다.

라벨은 UPPER_SNAKE (plan §4 명명 규칙).
새 노드 종류 추가 시: 이 파일에 CONSTRAINT 1줄 + INDEX (필요 시) 추가.
"""
from __future__ import annotations

from graph.neo4j_client import Neo4jClient
from graph.ownership import ANALYZER_GRAPH_OWNER


# 노드 유일성 제약 (UNIQUE) — 모든 노드 식별 키 = ``id`` 단일 (종류 무관).
#   값은 노드별 고유 문자열 (DB=db.schema.name / 코드=fqn형태 / EM=해시·합성키).
# 제약 이름은 ``uniq_<label>`` — 옛 버전이 ``module_id`` 등 동일 이름을 옛 속성(m.module_id)에
# 걸어둔 더티 DB 에서도 ``IF NOT EXISTS`` 가 (이름 충돌로) 새 ``.id`` 제약 생성을 막지 않도록
# 새 이름 사용. (이미 .id 에 동등 제약이 있으면 IF NOT EXISTS 가 no-op — clean·dirty 양쪽 안전.)
# ★코드 노드 라벨은 antlr type **열린 집합**이라 종류별 제약 열거 불가 →
#   전 노드 공통 마커 ``:ANALYZER_OWNED`` 에 id 유니크 제약을 건다. 이 인덱스가 없으면
#   무라벨 MATCH by id 가 AllNodesScan — AST 적재 159초의 원인이었다 (run6 실측).
CONSTRAINTS: list[str] = [
    "CREATE CONSTRAINT uniq_analyzer_owned IF NOT EXISTS "
    "FOR (n:ANALYZER_OWNED) REQUIRE n.id IS UNIQUE",
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
    *[
        f"CREATE CONSTRAINT uniq_{label.lower()} IF NOT EXISTS FOR (n:{label}) REQUIRE n.id IS UNIQUE"
        for label in ("PROJECT_FILE", "EXTERNAL_CHUNK")
    ],
    # 컴포넌트는 합성 노드(:COMPONENT)가 없으므로 제약 불필요 — 멤버십은 component_id 속성.
]

# 조회 인덱스 (안정 라벨만 — 코드 노드는 열린 antlr type 라벨이라 id 키로 조회).
INDEXES: list[str] = [
    "CREATE INDEX table_schema IF NOT EXISTS FOR (t:TABLE) ON (t.schema)",
]

# 무소유 레거시를 자동 migration할 수 있는 Analyzer 고유 라벨만 열거한다. Architect의 ``UI``처럼
# 대문자 라벨도 존재하므로 "대문자면 Analyzer"라는 포괄 규칙은 금지한다. 열린 집합 코드 라벨은
# Analyzer 원문 좌표 3종(file_path/start_line/end_line)이 모두 있을 때만 분류한다.
# 뒤쪽 외부 라벨 10종은 spec 075 시절 산출물 — 새 계약(spec 078)은 PROJECT_FILE·
# EXTERNAL_CHUNK 만 생산하지만, 옛 run 이 남긴 무소유 데이터를 owner-scoped wipe 로
# 지울 수 있도록 legacy 데이터 라벨로만 유지한다 (active producer/consumer 아님).
LEGACY_ANALYZER_STABLE_LABELS: tuple[str, ...] = (
    "COLUMN", "EMBED_META", "EXAMPLE", "GLOSSARY", "PACKAGE",
    "QUESTION", "RULE", "SCHEMA", "TABLE",
    "PROJECT_FILE", "EXTERNAL_CHUNK",
    "SQL_STATEMENT", "CONFIG_SECTION", "ARTIFACT_CHUNK", "CHUNK",
    "DOCUMENT", "DOC_SECTION", "DOC_CHUNK", "UNRESOLVED_REF", "CANDIDATE_LINK",
)

LEGACY_OWNER_MIGRATION_QUERY = """
MATCH (n)
WHERE n.graph_owner IS NULL
  AND (
    any(label IN labels(n) WHERE label IN $stable_labels)
    OR 'EXTERNAL' IN labels(n)
    OR (n.file_path IS NOT NULL AND n.start_line IS NOT NULL AND n.end_line IS NOT NULL)
  )
SET n.graph_owner = $graph_owner
RETURN count(n) AS migrated
"""


async def ensure_schema(client: Neo4jClient) -> None:
    """제약 + 인덱스 생성 (``IF NOT EXISTS`` — 이미 있으면 무시). run 시작 시 1회.

    스키마는 안정적 — 매 run drop-recreate 하지 않는다 (다른 프로젝트와 DB 를 공유할 경우
    drop-all 은 남의 스키마까지 파괴). 단 옛 버전 잔재 인덱스가 제약 자리를 차지하면
    충돌하므로 — analyzer 는 깨끗한 전용 DB 를 써야 한다.
    """
    for stmt in CONSTRAINTS + INDEXES:
        await client.run_query(stmt)


async def wipe_graph(client: Neo4jClient) -> None:
    """현재 Analyzer가 소유한 분석 그래프만 초기화한다.

    공유 Neo4j의 Fabric 연결 registry와 Architect 설계 그래프는 보존한다. 무소유 레거시
    분석 노드는 별도 검증된 migration에서 owner를 부여한 뒤 이 경계로 삭제한다.
    """
    params = {
        "graph_owner": ANALYZER_GRAPH_OWNER,
        "stable_labels": list(LEGACY_ANALYZER_STABLE_LABELS),
    }
    await client.run_query(LEGACY_OWNER_MIGRATION_QUERY, params)
    await client.run_query(
        "MATCH (n) WHERE n.graph_owner = $graph_owner DETACH DELETE n",
        {"graph_owner": ANALYZER_GRAPH_OWNER},
    )
