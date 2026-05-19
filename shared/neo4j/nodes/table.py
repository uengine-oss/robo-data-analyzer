"""Table 노드 Cypher — DDL 파싱에서만 생성 (Plan §3.1 DDL = 진실 기준).

식별 키: ``fqn = "db.schema.name"`` 단일.
``db`` / ``schema`` / ``name`` 은 그래프 조회 편의용 보조 속성.

description 은 ``enrich_table_description_step`` 이 SET 으로 채움 (별도 쿼리).
"""
from __future__ import annotations

from typing import Iterable

from data_models.graph_nodes import Table
from shared.neo4j.neo4j_client import Neo4jClient


# fqn 단일 식별 (schema_constraints.py 의 UNIQUE 제약과 통일)
MERGE_QUERY = """
UNWIND $items AS item
MERGE (t:Table {fqn: item.fqn})
SET t.db = item.db,
    t.schema = item.schema,
    t.name = item.name,
    t.description = coalesce(t.description, item.description),
    t.table_type  = item.table_type
RETURN count(t) AS created
"""

# description 만 갱신 (enrich_table_description_step 이 호출)
SET_DESCRIPTION_QUERY = """
UNWIND $items AS item
MATCH (t:Table {fqn: item.fqn})
SET t.description = item.description
"""


def _fqn(table: Table) -> str:
    """db.schema.name → fqn (단일 식별자)."""
    return f"{table.db}.{table.schema}.{table.name}"


def to_item(table: Table) -> dict:
    return {
        "fqn": _fqn(table),
        "db": table.db,
        "schema": table.schema,
        "name": table.name,
        "description": table.description,
        "table_type": table.table_type,
    }


async def upsert_tables(client: Neo4jClient, tables: Iterable[Table]) -> None:
    items = [to_item(t) for t in tables]
    await client.run_batch_unwind(MERGE_QUERY, items)


async def update_descriptions(client: Neo4jClient, updates: list[dict]) -> None:
    """``[{"fqn", "description"}]`` 형식 description 일괄 갱신."""
    await client.run_batch_unwind(SET_DESCRIPTION_QUERY, updates)
