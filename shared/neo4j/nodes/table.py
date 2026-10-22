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
#
# ## 카탈로그 라벨(`:Table`)을 붙이지 않는다
#
# 예전에는 여기서 `SET t:Table` 을 했다. 의도는 카탈로그 소비자(ontology-studio·text2sql)가
# 분석 결과를 보게 하는 것이었지만, 두 가지가 깨졌다.
#
# 1. **wipe 가 카탈로그를 지운다.** 매 run 시작의 `MATCH (n:TABLE) DETACH DELETE n` 은
#    `DETACH DELETE` 라 노드째 지우므로, 같은 노드에 붙어 있던 `:Table` 정체성도 함께
#    사라진다. 실측(2026-08-05): `legacy` 예제를 분석하자 `manufacturing` 스키마의
#    `:Table` 11개가 0개가 됐다.
# 2. **같은 테이블이 두 노드가 된다.** data-fabric 은 PostgreSQL 이 접은 이름(`tb01`)으로
#    쓰고 분석기는 DDL 표기(`TB01`)로 쓴다. Neo4j 라벨·속성은 대소문자를 구분하므로
#    `:Table` 이 둘 생긴다.
#
# 헌법 원칙 III 이 정한 것도 같다 — **카탈로그 writer 는 data-fabric 하나다.** 분석기는
# 자기 라벨(`:TABLE`)에만 쓰고, 알아낸 설명은 `catalog_projection.py` 가 기존 카탈로그
# 노드에 **투영**한다. 그래야 노드가 하나로 유지되고 wipe 가 카탈로그를 건드리지 않는다.
MERGE_QUERY = """
UNWIND $items AS item
MERGE (t:TABLE {fqn: item.fqn})
SET t.db = item.db,
    t.schema = item.schema,
    t.datasource = item.db,
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
# description_source: DDL description 이 비어 있으면 'source'(분석값이 유효 설명), 있으면 유지.
#
# `logical_name` 도 함께 받는다 — **카드 제목 자리가 그 필드다.** 화면은 논리명 모드에서
# `logical_name` 이 있으면 그것을, 없으면 물리명을 제목에 쓴다(`TableNode.vue` `cardTitle`).
# 설명만 채우고 이것을 비워 두면 컬럼은 논리명으로 바뀌는데 **테이블만 `tb02` 로 남는다**
# (2026-08-05 실측). 항목에 없으면 기존 값을 유지한다.
SET_ANALYZED_QUERY = """
UNWIND $items AS item
MATCH (t:TABLE {fqn: item.fqn})
SET t.analyzed_description = item.analyzed_description,
    t.logical_name = coalesce(item.logical_name, t.logical_name),
    t.description_source = CASE WHEN coalesce(t.description, '') = ''
                                THEN 'code' ELSE t.description_source END,
    t.description = CASE WHEN coalesce(t.description, '') = ''
                         THEN item.analyzed_description ELSE t.description END
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
