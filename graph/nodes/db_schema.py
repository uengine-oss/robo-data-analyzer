"""Schema 노드 Cypher — UNWIND 배치 MERGE.

새 Schema 필드 추가 시 손대는 곳:
1. ``graph/schema/graph_nodes.py`` 의 Schema dataclass
2. 이 파일의 MERGE_QUERY + to_item
"""
from __future__ import annotations

from typing import Iterable

from graph.schema.graph_nodes import Schema
from graph.neo4j_client import Neo4jClient
from graph.ownership import ANALYZER_GRAPH_OWNER


MERGE_QUERY = f"""
UNWIND $items AS item
MERGE (s:ANALYZER_OWNED:SCHEMA {{id: item.id, graph_owner: '{ANALYZER_GRAPH_OWNER}', generation_id: $generation_id}})
SET s:ANALYZER_OWNED,
    s.db = item.db,
    s.name = item.name,
    s.name_quoted = item.name_quoted,
    s.graph_owner = '{ANALYZER_GRAPH_OWNER}'
RETURN count(s) AS created
"""


def to_item(schema: Schema) -> dict:
    return {
        "id": schema.id, "db": schema.db, "name": schema.name,
        "name_quoted": schema.name_quoted,
    }


async def upsert_schemas(client: Neo4jClient, schemas: Iterable[Schema]) -> None:
    """Schema 노드 일괄 MERGE."""
    items = [to_item(s) for s in schemas]
    await client.run_batch_unwind(MERGE_QUERY, items)
