"""COLUMN 노드 Cypher.

description          = DDL COMMENT ON 1차 설명.
analyzed_description = analyze_code 3분할 tableInsights 합성 설명.
"""
from __future__ import annotations

from typing import Iterable

from graph.schema.graph_nodes import Column
from graph.neo4j_client import Neo4jClient
from graph.node_upsert import nonempty


MERGE_QUERY = """
UNWIND $items AS item
MERGE (c:COLUMN {id: item.id})
SET c.name = item.name,
    c.dtype = item.dtype,
    c.nullable = item.nullable,
    c.is_primary_key = item.is_primary_key,
    c.is_unique = item.is_unique,
    c.default_value = item.default_value,
    c.description = coalesce(c.description, item.description),
    c.description_source = coalesce(c.description_source, item.description_source),
    c.comment = coalesce(c.comment, item.comment),
    c.analyzed_description = coalesce(c.analyzed_description, item.analyzed_description)
RETURN count(c) AS created
"""

# 분석 합성 컬럼 설명 → analyzed_description SET (description=DDL 1차설명 은 안 건드림).
# description_source: DDL description 이 비어 있으면 'code'(소스코드 분석값이 유효 설명),
# 있으면 유지. 옛 'source' → 'code' (db prefix 와 단어 충돌 회피).
SET_ANALYZED_QUERY = """
UNWIND $items AS item
MATCH (c:COLUMN {id: item.id})
SET c.analyzed_description = item.analyzed_description,
    c.logical_name = CASE WHEN coalesce(item.logical_name, '') <> ''
                          THEN item.logical_name ELSE c.logical_name END,
    c.description_source = CASE WHEN coalesce(c.description, '') = ''
                                THEN 'code' ELSE c.description_source END
"""


def to_item(col: Column) -> dict:
    return {
        "id": col.id,
        "name": col.name,
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
    """``[{"id", "analyzed_description"}]`` — 분석 합성 컬럼 설명 일괄 SET. 반환=실제 매칭 노드 수."""
    await client.run_batch_unwind(SET_ANALYZED_QUERY, updates)
    ids = [u["id"] for u in updates]
    rows = await client.run_read_query(
        "MATCH (c:COLUMN) WHERE c.id IN $ids RETURN count(c) AS matched", {"ids": ids})
    return int(rows[0]["matched"]) if rows else 0
