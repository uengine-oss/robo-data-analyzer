"""TABLE 노드 Cypher — DDL 파싱에서만 생성 (Plan §3.1 DDL = 진실 기준).

식별 키: ``id = "db.schema.name"`` 단일. ``db`` / ``schema`` / ``name`` 은 보조 속성.

description          = DDL COMMENT ON / 카탈로그 1차 설명.
analyzed_description = analyze_code 3분할 합성 설명 (enrich step 이 SET).
"""
from __future__ import annotations

import json
from typing import Iterable

from graph.schema.graph_nodes import Table
from graph.neo4j_client import Neo4jClient
from graph.ownership import ANALYZER_GRAPH_OWNER
from graph.node_upsert import nonempty


# id 단일 식별 (schema_constraints.py 의 UNIQUE 제약과 통일) — 값 = "db.schema.name"
MERGE_QUERY = f"""
UNWIND $items AS item
MERGE (t:ANALYZER_OWNED:TABLE {{id: item.id, graph_owner: '{ANALYZER_GRAPH_OWNER}', generation_id: $generation_id}})
SET t:ANALYZER_OWNED,
    t.db = item.db,
    t.schema = item.schema,
    t.schema_quoted = item.schema_quoted,
    t.name = item.name,
    t.name_quoted = item.name_quoted,
    t.description = coalesce(t.description, item.description),
    t.description_source = coalesce(t.description_source, item.description_source),
    t.comment = coalesce(t.comment, item.comment),
    t.analyzed_description = coalesce(t.analyzed_description, item.analyzed_description),
    t.table_type = item.table_type,
    t.view_dependency_ids = item.view_dependency_ids,
    t.graph_owner = '{ANALYZER_GRAPH_OWNER}'
RETURN count(t) AS created
"""

# 분석 합성 설명 → analyzed_description SET (description=DDL/카탈로그 1차설명 은 안 건드림).
# description_source: DDL description 이 비어 있으면 'code'(소스코드 분석값이 유효 설명),
# 있으면 유지. 옛 'source' 값을 'code' 로 명명 변경 — db prefix "source" 와 단어 충돌
# 회피 + "어디서 왔는지" 의미를 더 명확히 (ddl / code / catalog).
SET_ANALYZED_QUERY = f"""
UNWIND $items AS item
MATCH (t:TABLE {{id: item.id, graph_owner: '{ANALYZER_GRAPH_OWNER}', generation_id: $generation_id}})
SET t.analyzed_description = item.analyzed_description,
    t.logical_name = item.logical_name,
    t.semantic_evidence_status = item.semantic_evidence_status,
    t.semantic_missing_context = item.semantic_missing_context,
    t.semantic_frame_schema = item.semantic_frame_schema,
    t.semantic_frame_json = item.semantic_frame_json,
    t.description_source = CASE
        WHEN coalesce(t.description, '') <> '' THEN t.description_source
        WHEN coalesce(item.analyzed_description, '') <> '' THEN 'code'
        ELSE '' END
"""

MATCH_ANALYZED_TARGETS_QUERY = f"""
MATCH (t:TABLE {{graph_owner: '{ANALYZER_GRAPH_OWNER}', generation_id: $generation_id}})
WHERE t.id IN $ids
RETURN count(t) AS matched
"""

SET_SAMPLE_SNAPSHOT_QUERY = f"""
UNWIND $items AS item
MATCH (t:TABLE {{id: item.id, graph_owner: '{ANALYZER_GRAPH_OWNER}', generation_id: $generation_id}})
SET t.sample_snapshot_json = item.sample_snapshot_json,
    t.sample_snapshot_source = 'datasource'
"""

MATCH_SAMPLE_TARGETS_QUERY = f"""
MATCH (t:TABLE {{graph_owner: '{ANALYZER_GRAPH_OWNER}', generation_id: $generation_id}})
WHERE t.id IN $ids
RETURN count(t) AS matched
"""


def to_item(table: Table) -> dict:
    """저장 속성.

    ``nonempty()`` 를 **쓰지 않는다** — 다른 노드는 ``SET n += item`` 이라 키가 빠지면
    "미저장"이지만, 여기 ``MERGE_QUERY`` 는 속성을 **하나씩 명시 SET** 한다(``coalesce`` 로
    DDL 설명 보존). 키를 빼면 ``item.x`` 가 null 이 되어 **기존 속성이 삭제**된다 → 의도적 예외.

    ``logical_name`` 도 여기 없다 — DDL 파싱 시점엔 항상 빈값이고, 값은 step7
    (``enrich_table_description``) 이 ``SET_ANALYZED_QUERY`` 로 넣는다. 그 값이 state/artifact
    에도 반영되도록 하는 것이 spec 045 FR-013 (수정 위치 = step8의 state 반영, 여기가 아님).
    """
    return {
        "id": table.id,
        "db": table.db,
        "schema": table.schema,
        "schema_quoted": table.schema_quoted,
        "name": table.name,
        "name_quoted": table.name_quoted,
        "description": table.description,
        "description_source": table.description_source,
        "comment": table.comment,
        "analyzed_description": table.analyzed_description,
        "table_type": table.table_type,
        "view_dependency_ids": list(table.view_dependency_ids),
    }


async def upsert_tables(client: Neo4jClient, tables: Iterable[Table]) -> None:
    # 빈값미저장(spec 020/024): 빈 db·comment 등은 키로 저장 안 함. id 는 항상 비어있지 않음.
    items = [nonempty(to_item(t)) for t in tables]
    await client.run_batch_unwind(MERGE_QUERY, items)


async def update_analyzed_descriptions(client: Neo4jClient, updates: list[dict]) -> int:
    """분석 의미를 모든 TABLE 대상이 존재할 때만 원자적으로 SET한다."""
    return await client.run_guarded_batch_unwind(
        guard_query=MATCH_ANALYZED_TARGETS_QUERY,
        write_query=SET_ANALYZED_QUERY,
        items=updates,
    )


async def update_sample_snapshots(
    client: Neo4jClient, snapshots: dict[str, dict],
) -> int:
    """Persist the already-read analysis sample once; Architect never re-queries the DB."""
    items = [
        {
            "id": table_id,
            "sample_snapshot_json": json.dumps(
                payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            ),
        }
        for table_id, payload in sorted(snapshots.items())
    ]
    if not items:
        return 0
    return await client.run_guarded_batch_unwind(
        guard_query=MATCH_SAMPLE_TARGETS_QUERY,
        write_query=SET_SAMPLE_SNAPSHOT_QUERY,
        items=items,
    )
