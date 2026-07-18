"""INIT_BY 엣지 — 변수/상수 → 출처 노드 (정의 시 참조하는 다른 변수·상수).

MATCH 는 모든 노드 공통 ``id`` 로만 (라벨 무관).
"""
from __future__ import annotations

from typing import Iterable

from graph.schema.graph_edges import InitBy
from graph.neo4j_client import Neo4jClient
from graph.ownership import ANALYZER_GRAPH_OWNER
from graph.schema.rel_types import INIT_BY


MERGE_QUERY = f"""
UNWIND $items AS item
MATCH (f {{id: item.from_id, graph_owner: '{ANALYZER_GRAPH_OWNER}'}})
MATCH (s {{id: item.source_id, graph_owner: '{ANALYZER_GRAPH_OWNER}'}})
MERGE (f)-[:{INIT_BY}]->(s)
"""


async def upsert_init_by(client: Neo4jClient, edges: Iterable[InitBy]) -> None:
    """id 무라벨 MATCH 로 MERGE."""
    items = [{"from_id": e.from_id, "source_id": e.source_id} for e in edges]
    await client.run_batch_unwind(MERGE_QUERY, items)
