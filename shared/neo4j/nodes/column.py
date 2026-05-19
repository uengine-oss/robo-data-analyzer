"""Column 노드 Cypher.

description 은 analyze_function 의 3 분할 ``tableInsights`` 가 SET 으로 채움.
"""
from __future__ import annotations

from typing import Iterable

from data_models.graph_nodes import Column
from shared.neo4j.neo4j_client import Neo4jClient


MERGE_QUERY = """
UNWIND $items AS item
MERGE (c:Column {fqn: item.fqn})
SET c.name = item.name,
    c.dtype = item.dtype,
    c.nullable = item.nullable,
    c.is_primary_key = item.is_primary_key,
    c.is_unique = item.is_unique,
    c.default_value = item.default_value,
    c.description = coalesce(c.description, item.description)
RETURN count(c) AS created
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
    }


async def upsert_columns(client: Neo4jClient, columns: Iterable[Column]) -> None:
    items = [to_item(c) for c in columns]
    await client.run_batch_unwind(MERGE_QUERY, items)
