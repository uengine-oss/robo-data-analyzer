"""HAS_RULE 엣지 — CodeNode → Rule.

Rule 노드는 코드노드 간 공유(rule_hash) 되므로 per-function 정보(local rule_id /
flow_id / coupled_domains)는 이 엣지 속성에 둔다.
엣지는 ``local_rule_id`` 로 구분 — 한 코드노드의 여러 규칙이 각각의 HAS_RULE.
"""
from __future__ import annotations

from typing import Iterable

from data_models.graph_edges import HasRule
from shared.neo4j.neo4j_client import Neo4jClient
from shared.neo4j.rel_types import HAS_RULE

_UPSERT_QUERY = f"""
UNWIND $items AS item
MATCH (node {{id: item.source_id}})
MATCH (r:RULE {{id: item.target_id}})
MERGE (node)-[rel:{HAS_RULE} {{local_rule_id: item.local_rule_id}}]->(r)
SET rel.flow_id = item.flow_id,
    rel.coupled_domains = item.coupled_domains
"""


async def upsert_has_rule(client: Neo4jClient, edges: Iterable[HasRule]) -> None:
    """CodeNode → Rule 엣지 + per-function 속성."""
    items = [
        {
            "source_id": e.source_id,
            "target_id": e.target_id,
            "local_rule_id": e.local_rule_id,
            "flow_id": e.flow_id,
            "coupled_domains": list(e.coupled_domains),
        }
        for e in edges
    ]
    await client.run_batch_unwind(_UPSERT_QUERY, items)
