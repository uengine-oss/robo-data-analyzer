"""HAS_RULE 엣지 — CodeNode → Rule.

Rule 노드는 코드노드 간 공유(rule_hash) 되므로 per-function 정보(coupled_domains)는
이 엣지 속성에 둔다. 정체성 = (node, rule) — rule_hash 가 노드당 유일.
"""
from __future__ import annotations

from typing import Iterable

from graph.schema.graph_edges import HasRule
from graph.neo4j_client import Neo4jClient
from graph.schema.rel_types import HAS_RULE

_UPSERT_QUERY = f"""
UNWIND $items AS item
MATCH (node {{id: item.source_id}})
MATCH (r:RULE {{id: item.target_id}})
MERGE (node)-[rel:{HAS_RULE}]->(r)
SET rel.coupled_domains = item.coupled_domains
"""


async def upsert_has_rule(client: Neo4jClient, edges: Iterable[HasRule]) -> None:
    """CodeNode → Rule 엣지 + coupled_domains 속성."""
    items = [
        {
            "source_id": e.source_id,
            "target_id": e.target_id,
            "coupled_domains": list(e.coupled_domains),
        }
        for e in edges
    ]
    await client.run_batch_unwind(_UPSERT_QUERY, items)
