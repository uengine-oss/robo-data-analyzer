"""FK 엣지 — Table → Table (외래키).

DDL 파싱에서만 생성. ``is_candidate=True`` 는 추론된 FK (사람 검토 큐).
"""
from __future__ import annotations

from typing import Iterable

from graph.schema.graph_edges import Fk
from graph.neo4j_client import Neo4jClient
from graph.ownership import ANALYZER_GRAPH_OWNER
from graph.schema.rel_types import FK


MERGE_QUERY = f"""
UNWIND $items AS item
MATCH (from:TABLE {{id: item.from_id, graph_owner: '{ANALYZER_GRAPH_OWNER}'}})
MATCH (to:TABLE {{id: item.to_id, graph_owner: '{ANALYZER_GRAPH_OWNER}'}})
MERGE (from)-[r:{FK} {{from_column: item.from_column, to_column: item.to_column}}]->(to)
SET r.fk_type = item.fk_type,
    r.is_candidate = item.is_candidate
"""


async def upsert_fk(client: Neo4jClient, edges: Iterable[Fk]) -> None:
    items = [{
        "from_id": e.from_id,
        "to_id": e.to_id,
        "from_column": e.from_column,
        "to_column": e.to_column,
        "fk_type": e.fk_type,
        "is_candidate": e.is_candidate,
    } for e in edges]
    await client.run_batch_unwind(MERGE_QUERY, items)
