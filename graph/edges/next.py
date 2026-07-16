"""NEXT 엣지 (구문 흐름) — 이전 구문 → 다음 구문 (DBMS 실행 순서).

같은 부모 아래 형제 구문을 등장(실행) 순서로 연결해 절차적 로직 흐름을 추적한다.
끝점이 구문 노드라 **무라벨 MATCH by id** (id 단일 유니크 키).
"""
from __future__ import annotations

from typing import Iterable

from graph.schema.graph_edges import StatementNext
from graph.neo4j_client import Neo4jClient
from graph.schema.rel_types import NEXT


MERGE_QUERY = f"""
UNWIND $items AS item
MATCH (prev {{id: item.prev_id}})
MATCH (next {{id: item.next_id}})
MERGE (prev)-[:{NEXT}]->(next)
"""


async def upsert_statement_next(client: Neo4jClient, edges: Iterable[StatementNext]) -> None:
    items = [{"prev_id": e.prev_id, "next_id": e.next_id} for e in edges]
    await client.run_batch_unwind(MERGE_QUERY, items)
