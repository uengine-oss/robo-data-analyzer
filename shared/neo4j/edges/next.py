"""NEXT 엣지 (구문 흐름) — 이전 구문 → 다음 구문 (DBMS 실행 순서).

같은 부모 아래 형제 구문을 등장(실행) 순서로 연결해 절차적 로직 흐름을 추적한다.
끝점이 구문 노드라 **무라벨 MATCH by id** (id 단일 유니크 키).

★Rule→Rule NEXT(``edges/rule_flow.py``)와 rel 타입 문자열(NEXT)만 공유 — 끝점 라벨로
구분된다 (구문↔구문 vs RULE↔RULE). 생성 시점·MATCH 가 전혀 달라 별개 파일.
"""
from __future__ import annotations

from typing import Iterable

from data_models.graph_edges import StatementNext
from shared.neo4j.neo4j_client import Neo4jClient
from shared.neo4j.rel_types import NEXT


MERGE_QUERY = f"""
UNWIND $items AS item
MATCH (prev {{id: item.prev_id}})
MATCH (next {{id: item.next_id}})
MERGE (prev)-[:{NEXT}]->(next)
"""


async def upsert_statement_next(client: Neo4jClient, edges: Iterable[StatementNext]) -> None:
    items = [{"prev_id": e.prev_id, "next_id": e.next_id} for e in edges]
    await client.run_batch_unwind(MERGE_QUERY, items)
