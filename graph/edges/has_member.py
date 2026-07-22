"""HAS_MEMBER 엣지 — 그릇 → 멤버 ("가지다").

그릇 = FILE/CLASS/PACKAGE/DataType, 멤버 = 루틴·데이터·struct 멤버변수.
끝점 종류 라벨이 달라 (그릇 FILE/CLASS·멤버 PROCEDURE/VARIABLE…) ``:ANALYZER_OWNED`` + id 로 MATCH —
id 가 단일 유니크 키라 라벨 제약 없이 안전 (parent_of 와 동일 방식).
"""
from __future__ import annotations

from typing import Iterable

from graph.schema.graph_edges import HasMember
from graph.neo4j_client import Neo4jClient
from graph.ownership import ANALYZER_GRAPH_OWNER
from graph.schema.rel_types import HAS_MEMBER


MERGE_QUERY = f"""
UNWIND $items AS item
MATCH (parent:ANALYZER_OWNED {{id: item.parent_id, graph_owner: '{ANALYZER_GRAPH_OWNER}'}})
MATCH (member:ANALYZER_OWNED {{id: item.child_id, graph_owner: '{ANALYZER_GRAPH_OWNER}'}})
MERGE (parent)-[:{HAS_MEMBER}]->(member)
"""


async def upsert_has_member(client: Neo4jClient, edges: Iterable[HasMember]) -> None:
    items = [{"parent_id": e.parent_id, "child_id": e.child_id} for e in edges]
    await client.run_batch_unwind(MERGE_QUERY, items)
