"""COLUMN 노드 Cypher.

description          = DDL COMMENT ON 1차 설명.
analyzed_description = analyze_code 3분할 tableInsights 합성 설명.
"""
from __future__ import annotations

from typing import Iterable

from graph.schema.graph_nodes import Column
from graph.neo4j_client import Neo4jClient
from graph.ownership import ANALYZER_GRAPH_OWNER
from graph.node_upsert import nonempty


MERGE_QUERY = f"""
UNWIND $items AS item
MERGE (c:ANALYZER_OWNED:COLUMN {{id: item.id, graph_owner: '{ANALYZER_GRAPH_OWNER}', generation_id: $generation_id}})
SET c:ANALYZER_OWNED,
    c.name = item.name,
    c.name_quoted = item.name_quoted,
    c.dtype = item.dtype,
    c.nullable = item.nullable,
    c.is_primary_key = item.is_primary_key,
    c.is_unique = item.is_unique,
    c.default_value = item.default_value,
    c.description = coalesce(c.description, item.description),
    c.description_source = coalesce(c.description_source, item.description_source),
    c.comment = coalesce(c.comment, item.comment),
    c.analyzed_description = coalesce(c.analyzed_description, item.analyzed_description),
    c.graph_owner = '{ANALYZER_GRAPH_OWNER}'
RETURN count(c) AS created
"""

# 분석 합성 컬럼 설명 → analyzed_description SET (description=DDL 1차설명 은 안 건드림).
# description_source: DDL description 이 비어 있으면 'code'(소스코드 분석값이 유효 설명),
# 있으면 유지. 옛 'source' → 'code' (db prefix 와 단어 충돌 회피).
SET_ANALYZED_QUERY = f"""
UNWIND $items AS item
MATCH (c:COLUMN {{id: item.id, graph_owner: '{ANALYZER_GRAPH_OWNER}', generation_id: $generation_id}})
SET c.analyzed_description = item.analyzed_description,
    c.logical_name = item.logical_name,
    c.semantic_evidence_status = item.semantic_evidence_status,
    c.semantic_missing_context = item.semantic_missing_context,
    c.semantic_frame_schema = item.semantic_frame_schema,
    c.semantic_frame_json = item.semantic_frame_json,
    c.description_source = CASE
        WHEN coalesce(c.description, '') <> '' THEN c.description_source
        WHEN coalesce(item.analyzed_description, '') <> '' THEN 'code'
        ELSE '' END
"""

MATCH_ANALYZED_TARGETS_QUERY = f"""
MATCH (c:COLUMN {{graph_owner: '{ANALYZER_GRAPH_OWNER}', generation_id: $generation_id}})
WHERE c.id IN $ids
RETURN count(c) AS matched
"""


def to_item(col: Column) -> dict:
    return {
        "id": col.id,
        "name": col.name,
        "name_quoted": col.name_quoted,
        "dtype": col.dtype,
        "nullable": col.nullable,
        "is_primary_key": col.is_primary_key,
        "is_unique": col.is_unique,
        "default_value": col.default_value,
        "description": col.description,
        "description_source": col.description_source,
        "comment": col.comment,
        "analyzed_description": col.analyzed_description,
    }


async def upsert_columns(client: Neo4jClient, columns: Iterable[Column]) -> None:
    # 빈값미저장(spec 020/024): 빈 comment·default_value 등은 키로 저장 안 함.
    items = [nonempty(to_item(c)) for c in columns]
    await client.run_batch_unwind(MERGE_QUERY, items)


async def update_analyzed_descriptions(client: Neo4jClient, updates: list[dict]) -> int:
    """분석 의미를 모든 COLUMN 대상이 존재할 때만 원자적으로 SET한다."""
    return await client.run_guarded_batch_unwind(
        guard_query=MATCH_ANALYZED_TARGETS_QUERY,
        write_query=SET_ANALYZED_QUERY,
        items=updates,
    )
