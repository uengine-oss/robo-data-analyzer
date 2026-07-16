"""BELONGS_TO 엣지 — 네임스페이스 소속.

DDL: Table → Schema. AST: 그릇(FILE/CLASS) → Package(폴더).
끝점 라벨이 달라 (Table·FILE / Schema·Package) **무라벨 MATCH by id** — id 단일 유니크 키.
"""
from __future__ import annotations

from typing import Iterable

from graph.schema.graph_edges import BelongsTo
from graph.neo4j_client import Neo4jClient
from graph.schema.rel_types import BELONGS_TO


MERGE_QUERY = f"""
UNWIND $items AS item
MATCH (child {{id: item.child_id}})
MATCH (parent {{id: item.parent_id}})
MERGE (child)-[:{BELONGS_TO}]->(parent)
"""


async def upsert_belongs_to(client: Neo4jClient, edges: Iterable[BelongsTo]) -> None:
    items = [{"child_id": e.child_id, "parent_id": e.parent_id} for e in edges]
    await client.run_batch_unwind(MERGE_QUERY, items)
