"""FK 엣지 — Table → Table (외래키).

DDL 파싱에서만 생성. ``is_candidate=True`` 는 추론된 FK (사람 검토 큐).
"""
from __future__ import annotations

from typing import Iterable

from data_models.graph_edges import Fk
from shared.neo4j.neo4j_client import Neo4jClient
from shared.neo4j.rel_types import FK


MERGE_QUERY = f"""
UNWIND $items AS item
MATCH (from:TABLE {{fqn: item.from_table_fqn}})
MATCH (to:TABLE {{fqn: item.to_table_fqn}})
MERGE (from)-[r:{FK} {{from_column: item.from_column, to_column: item.to_column}}]->(to)
SET r.fk_type = item.fk_type,
    r.is_candidate = item.is_candidate
"""


async def upsert_fk(client: Neo4jClient, edges: Iterable[Fk]) -> None:
    items = [{
        "from_table_fqn": e.from_table_fqn,
        "to_table_fqn": e.to_table_fqn,
        "from_column": e.from_column,
        "to_column": e.to_column,
        "fk_type": e.fk_type,
        "is_candidate": e.is_candidate,
    } for e in edges]
    await client.run_batch_unwind(MERGE_QUERY, items)
