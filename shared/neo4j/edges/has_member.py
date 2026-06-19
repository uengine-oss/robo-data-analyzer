"""HAS_MEMBER 엣지 — 그릇 → 멤버 ("가지다").

그릇 = FILE/CLASS/PACKAGE/DataType, 멤버 = 루틴·데이터·struct 멤버변수.
끝점 라벨이 종류별로 달라 (그릇 FILE/CLASS·멤버 PROCEDURE/VARIABLE…) **무라벨 MATCH by id** —
id 가 단일 유니크 키라 라벨 제약 없이 안전 (parent_of 와 동일 방식).
"""
from __future__ import annotations

from typing import Iterable

from data_models.graph_edges import HasMember
from shared.neo4j.neo4j_client import Neo4jClient
from shared.neo4j.rel_types import HAS_MEMBER


MERGE_QUERY = f"""
UNWIND $items AS item
MATCH (parent {{id: item.parent_id}})
MATCH (member {{id: item.child_id}})
MERGE (parent)-[:{HAS_MEMBER}]->(member)
"""


async def upsert_has_member(client: Neo4jClient, edges: Iterable[HasMember]) -> None:
    items = [{"parent_id": e.parent_id, "child_id": e.child_id} for e in edges]
    await client.run_batch_unwind(MERGE_QUERY, items)
