"""HAS_RULE 엣지 — CodeNode → Rule.

Rule 노드 id 는 ``<function id>::R<anchor>`` (anchor 좌표 정체성, spec 116) — 함수별 고유다.
"""
from __future__ import annotations

from typing import Iterable

from graph.schema.graph_edges import HasRule
from graph.neo4j_client import Neo4jClient
from graph.ownership import ANALYZER_GRAPH_OWNER
from graph.schema.rel_types import HAS_RULE

_UPSERT_QUERY = f"""
UNWIND $items AS item
MATCH (node:ANALYZER_OWNED {{id: item.source_id, graph_owner: '{ANALYZER_GRAPH_OWNER}'}})
MATCH (r:RULE {{id: item.target_id, graph_owner: '{ANALYZER_GRAPH_OWNER}'}})
MERGE (node)-[rel:{HAS_RULE} {{occurrence_id: item.occurrence_id}}]->(r)
SET rel.ordinal = item.ordinal,
    rel.anchor_line = item.anchor_line
REMOVE rel.coupled_domains
"""


async def upsert_has_rule(client: Neo4jClient, edges: Iterable[HasRule]) -> None:
    """CodeNode → Rule 엣지. 폐기된 coupled_domains 속성은 기존 관계에서도 제거한다."""
    items = [
        {
            "source_id": e.source_id,
            "target_id": e.target_id,
            "occurrence_id": e.occurrence_id,
            "ordinal": e.ordinal,
            "anchor_line": e.anchor_line,
        }
        for e in edges
    ]
    await client.run_batch_unwind(_UPSERT_QUERY, items)
