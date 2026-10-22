"""COLUMN 노드 Cypher.

description          = DDL COMMENT ON 1차 설명.
analyzed_description = analyze_function 3분할 tableInsights 합성 설명.

카탈로그 라벨(``:Column``)은 붙이지 않는다 — 이유는 ``table.py`` 의 ``MERGE_QUERY`` 주석에
있다(한 줄 요약: wipe 가 카탈로그를 지우고, 대소문자 차이로 노드가 둘이 된다). 알아낸 설명은
``catalog_projection.py`` 가 기존 카탈로그 노드에 투영한다.
"""
from __future__ import annotations

from typing import Iterable

from data_models.graph_nodes import Column
from shared.neo4j.neo4j_client import Neo4jClient


MERGE_QUERY = """
UNWIND $items AS item
MERGE (c:COLUMN {fqn: item.fqn})
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
# description_source: DDL description 이 비어 있으면 'source', 있으면 유지.
SET_ANALYZED_QUERY = """
UNWIND $items AS item
MATCH (c:COLUMN {fqn: item.fqn})
SET c.analyzed_description = item.analyzed_description,
    c.description_source = CASE WHEN coalesce(c.description, '') = ''
                                THEN 'code' ELSE c.description_source END,
    c.description = CASE WHEN coalesce(c.description, '') = ''
                         THEN item.analyzed_description ELSE c.description END
"""


def to_item(col: Column) -> dict:
    return {
        "fqn": col.fqn,
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
    items = [to_item(c) for c in columns]
    await client.run_batch_unwind(MERGE_QUERY, items)


async def update_analyzed_descriptions(client: Neo4jClient, updates: list[dict]) -> None:
    """``[{"fqn", "analyzed_description"}]`` — 분석 합성 컬럼 설명 일괄 SET."""
    await client.run_batch_unwind(SET_ANALYZED_QUERY, updates)
