"""REFERENCES 엣지 — CodeNode → Variable / Constant.

MATCH 는 ``:ANALYZER_OWNED`` + ``id`` (변수/상수 종류 라벨이 antlr type 열린집합이라 마커로 시크).
"""
from __future__ import annotations

from typing import Iterable

from graph.schema.graph_edges import References
from graph.neo4j_client import Neo4jClient
from graph.ownership import ANALYZER_GRAPH_OWNER
from graph.schema.rel_types import REFERENCES


MERGE_QUERY = f"""
UNWIND $items AS item
MATCH (node:ANALYZER_OWNED {{id: item.source_id, graph_owner: '{ANALYZER_GRAPH_OWNER}'}})
MATCH (t:ANALYZER_OWNED {{id: item.target_id, graph_owner: '{ANALYZER_GRAPH_OWNER}'}})
MERGE (node)-[r:{REFERENCES}]->(t)
SET r.evidence_line = item.evidence_line
"""


async def upsert_references(client: Neo4jClient, edges: Iterable[References]) -> None:
    """``:ANALYZER_OWNED`` + id MATCH 로 MERGE."""
    items = [
        {"source_id": e.source_id, "target_id": e.target_id, "evidence_line": e.evidence_line}
        for e in edges
    ]
    await client.run_batch_unwind(MERGE_QUERY, items)
