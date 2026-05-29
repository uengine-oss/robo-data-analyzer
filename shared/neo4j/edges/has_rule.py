"""HAS_RULE 엣지 — Function → Rule.

Rule 노드는 함수 간 공유(rule_hash) 되므로 per-function 정보(local rule_id /
flow_id / coupled_domains)는 이 엣지 속성에 둔다.
엣지는 ``local_rule_id`` 로 구분 — 한 함수의 여러 규칙이 각각의 HAS_RULE.
"""
from __future__ import annotations

from typing import Iterable

from data_models.graph_edges import HasRule
from shared.neo4j.neo4j_client import Neo4jClient
from shared.neo4j.rel_types import HAS_RULE

_UPSERT_QUERY = f"""
UNWIND $items AS item
MATCH (f:FUNCTION {{function_id: item.function_id}})
MATCH (r:RULE {{rule_hash: item.rule_hash}})
MERGE (f)-[rel:{HAS_RULE} {{local_rule_id: item.local_rule_id}}]->(r)
SET rel.flow_id = item.flow_id,
    rel.coupled_domains = item.coupled_domains
"""


async def upsert_has_rule(client: Neo4jClient, edges: Iterable[HasRule]) -> None:
    """Function → Rule 엣지 + per-function 속성."""
    items = [
        {
            "function_id": e.function_id,
            "rule_hash": e.rule_hash,
            "local_rule_id": e.local_rule_id,
            "flow_id": e.flow_id,
            "coupled_domains": list(e.coupled_domains),
        }
        for e in edges
    ]
    await client.run_batch_unwind(_UPSERT_QUERY, items)
