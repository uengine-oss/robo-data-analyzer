"""TABLE 노드 Cypher — DDL 파싱에서만 생성 (Plan §3.1 DDL = 진실 기준).

식별 키: ``id = "db.schema.name"`` 단일. ``db`` / ``schema`` / ``name`` 은 보조 속성.

description          = DDL COMMENT ON / 카탈로그 1차 설명.
analyzed_description = analyze_code 3분할 합성 설명 (enrich step 이 SET).
"""
from __future__ import annotations

from typing import Iterable

from data_models.graph_nodes import Table
from shared.neo4j.neo4j_client import Neo4jClient
from shared.neo4j.props import nonempty


# id 단일 식별 (schema_constraints.py 의 UNIQUE 제약과 통일) — 값 = "db.schema.name"
MERGE_QUERY = """
UNWIND $items AS item
MERGE (t:TABLE {id: item.id})
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

# 분석 합성 설명 → analyzed_description SET (description=DDL/카탈로그 1차설명 은 안 건드림).
# description_source: DDL description 이 비어 있으면 'code'(소스코드 분석값이 유효 설명),
# 있으면 유지. 옛 'source' 값을 'code' 로 명명 변경 — db prefix "source" 와 단어 충돌
# 회피 + "어디서 왔는지" 의미를 더 명확히 (ddl / code / catalog).
SET_ANALYZED_QUERY = """
UNWIND $items AS item
MATCH (t:TABLE {id: item.id})
SET t.analyzed_description = item.analyzed_description,
    t.logical_name = CASE WHEN coalesce(item.logical_name, '') <> ''
                          THEN item.logical_name ELSE t.logical_name END,
    t.description_source = CASE WHEN coalesce(t.description, '') = ''
                                THEN 'code' ELSE t.description_source END
"""


def to_item(table: Table) -> dict:
    return {
        "id": table.id,
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
    # 빈값미저장(spec 020/024): 빈 db·comment 등은 키로 저장 안 함. id 는 항상 비어있지 않음.
    items = [nonempty(to_item(t)) for t in tables]
    await client.run_batch_unwind(MERGE_QUERY, items)


async def update_analyzed_descriptions(client: Neo4jClient, updates: list[dict]) -> int:
    """``[{"id", "analyzed_description"}]`` — 분석 합성 설명 일괄 SET. 반환=실제 매칭된 노드 수.
    매칭 수 < 입력 수면 id 불일치로 유실(호출측이 표면화, spec 024)."""
    await client.run_batch_unwind(SET_ANALYZED_QUERY, updates)
    ids = [u["id"] for u in updates]
    rows = await client.run_read_query(
        "MATCH (t:TABLE) WHERE t.id IN $ids RETURN count(t) AS matched", {"ids": ids})
    return int(rows[0]["matched"]) if rows else 0
