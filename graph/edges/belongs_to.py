"""BELONGS_TO 엣지 — 네임스페이스 소속.

DDL: Table → Schema. AST: 그릇(FILE/CLASS) → Package(폴더).
끝점 종류 라벨이 달라 (Table·FILE / Schema·Package) 공통 마커 ``:ANALYZER_OWNED`` + id 로 MATCH.
"""
from __future__ import annotations

from typing import Iterable

from graph.schema.graph_edges import BelongsTo
from graph.neo4j_client import Neo4jClient
from graph.ownership import ANALYZER_GRAPH_OWNER
from graph.schema.rel_types import BELONGS_TO


MERGE_QUERY = f"""
UNWIND $items AS item
MATCH (child:ANALYZER_OWNED {{id: item.child_id, graph_owner: '{ANALYZER_GRAPH_OWNER}', generation_id: $generation_id}})
MATCH (parent:ANALYZER_OWNED {{id: item.parent_id, graph_owner: '{ANALYZER_GRAPH_OWNER}', generation_id: $generation_id}})
MERGE (child)-[rel:{BELONGS_TO}]->(parent)
SET rel.generation_id = $generation_id
"""


async def upsert_belongs_to(client: Neo4jClient, edges: Iterable[BelongsTo]) -> None:
    items = [{"child_id": e.child_id, "parent_id": e.parent_id} for e in edges]
    await client.run_batch_unwind(MERGE_QUERY, items)
