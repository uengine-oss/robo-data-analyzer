"""COMPONENT 노드 + 엣지 Cypher (합본) — 함수 호출 군집 '부품'.

``build_components_step`` 이 적재. 한 응집 단위라 노드/엣지를 한 파일에 모은다:
- ``:COMPONENT`` 노드 (MERGE 키 = component_id)
- ``HAS_COMPONENT_MEMBER`` : Component → Function
- ``COMPONENT_CALLS``      : Component → Component (count 부속)

MATCH 양끝 후 MERGE rel — endpoint(Component/Function) 가 먼저 적재돼 있어야 한다
(노드 → member → calls 순서 보장). robo 의 ``nodes/``·``edges/`` 분리 관례 대신
응집(부품=노드+그 엣지)으로 1파일 합본.
"""
from __future__ import annotations

from typing import Iterable

from data_models.graph_edges import ComponentCalls, HasComponentMember
from data_models.graph_nodes import Component
from shared.neo4j.neo4j_client import Neo4jClient
from shared.neo4j.rel_types import COMPONENT_CALLS, HAS_COMPONENT_MEMBER


_NODE_QUERY = """
UNWIND $items AS item
MERGE (c:COMPONENT {component_id: item.component_id})
SET c.name = item.name,
    c.description = item.description,
    c.stereotype = item.stereotype,
    c.size = item.size
"""

_MEMBER_QUERY = f"""
UNWIND $items AS item
MATCH (c:COMPONENT {{component_id: item.component_id}})
MATCH (f:FUNCTION {{function_id: item.function_id}})
MERGE (c)-[:{HAS_COMPONENT_MEMBER}]->(f)
"""

_CALLS_QUERY = f"""
UNWIND $items AS item
MATCH (a:COMPONENT {{component_id: item.caller_component_id}})
MATCH (b:COMPONENT {{component_id: item.callee_component_id}})
MERGE (a)-[r:{COMPONENT_CALLS}]->(b)
SET r.count = item.count
"""


async def upsert_components(
    client: Neo4jClient,
    components: Iterable[Component],
    member_edges: Iterable[HasComponentMember],
    calls_edges: Iterable[ComponentCalls],
) -> None:
    """노드 → 멤버 엣지 → 부품간 호출 엣지 순서로 MERGE (endpoint 선적재 보장)."""
    nodes = [
        {"component_id": c.component_id, "name": c.name, "description": c.description,
         "stereotype": c.stereotype, "size": c.size}
        for c in components
    ]
    if nodes:
        await client.run_batch_unwind(_NODE_QUERY, nodes)

    members = [{"component_id": e.component_id, "function_id": e.function_id} for e in member_edges]
    if members:
        await client.run_batch_unwind(_MEMBER_QUERY, members)

    calls = [
        {"caller_component_id": e.caller_component_id,
         "callee_component_id": e.callee_component_id, "count": e.count}
        for e in calls_edges
    ]
    if calls:
        await client.run_batch_unwind(_CALLS_QUERY, calls)
