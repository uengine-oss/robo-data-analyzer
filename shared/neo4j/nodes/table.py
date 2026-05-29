"""TABLE 노드 Cypher — DDL 파싱에서만 생성 (Plan §3.1 DDL = 진실 기준).

식별 키: ``fqn = "db.schema.name"`` 단일. ``db`` / ``schema`` / ``name`` 은 보조 속성.

description          = DDL COMMENT ON / 카탈로그 1차 설명.
analyzed_description = analyze_function 3분할 합성 설명 (enrich step 이 SET).
"""
from __future__ import annotations

from typing import Iterable

from data_models.graph_nodes import Table
from shared.neo4j.neo4j_client import Neo4jClient


# fqn 단일 식별 (schema_constraints.py 의 UNIQUE 제약과 통일)
MERGE_QUERY = """
UNWIND $items AS item
MERGE (t:TABLE {fqn: item.fqn})
SET t.db = item.db,
    t.schema = item.schema,
    t.name = item.name,
    t.description = coalesce(t.description, item.description),
    t.description_source = coalesce(t.description_source, item.description_source),
    t.comment = coalesce(t.comment, item.comment),
    t.analyzed_description = coalesce(t.analyzed_description, item.analyzed_description),
    t.table_type = item.table_type
RETURN count(t) AS created
"""

# 카탈로그 1차 description 갱신 (enrich_metadata_from_catalog_step 이 호출)
SET_DESCRIPTION_QUERY = """
UNWIND $items AS item
MATCH (t:TABLE {fqn: item.fqn})
SET t.description = item.description
"""

# 분석 합성 설명 → analyzed_description SET (description=DDL/카탈로그 1차설명 은 안 건드림).
# description_source: DDL description 이 비어 있으면 'code'(소스코드 분석값이 유효 설명),
# 있으면 유지. 옛 'source' 값을 'code' 로 명명 변경 — db prefix "source" 와 단어 충돌
# 회피 + "어디서 왔는지" 의미를 더 명확히 (ddl / code / catalog).
SET_ANALYZED_QUERY = """
UNWIND $items AS item
MATCH (t:TABLE {fqn: item.fqn})
SET t.analyzed_description = item.analyzed_description,
    t.description_source = CASE WHEN coalesce(t.description, '') = ''
                                THEN 'code' ELSE t.description_source END
"""


def to_item(table: Table) -> dict:
    return {
        "fqn": table.fqn,
        "db": table.db,
        "schema": table.schema,
        "name": table.name,
        "description": table.description,
        "description_source": table.description_source,
        "comment": table.comment,
        "analyzed_description": table.analyzed_description,
        "table_type": table.table_type,
    }


async def upsert_tables(client: Neo4jClient, tables: Iterable[Table]) -> None:
    items = [to_item(t) for t in tables]
    await client.run_batch_unwind(MERGE_QUERY, items)


async def update_descriptions(client: Neo4jClient, updates: list[dict]) -> None:
    """``[{"fqn", "description"}]`` 형식 description 일괄 갱신 (카탈로그 1차 설명)."""
    await client.run_batch_unwind(SET_DESCRIPTION_QUERY, updates)


async def update_analyzed_descriptions(client: Neo4jClient, updates: list[dict]) -> None:
    """``[{"fqn", "analyzed_description"}]`` — 분석 합성 설명 일괄 SET."""
    await client.run_batch_unwind(SET_ANALYZED_QUERY, updates)
