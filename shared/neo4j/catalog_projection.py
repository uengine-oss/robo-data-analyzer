"""분석으로 알아낸 설명을 **카탈로그 노드에 투영**한다.

## 왜 필요한가

분석기는 자기 라벨(``:TABLE`` / ``:COLUMN``)에만 쓴다. 카탈로그를 읽는 쪽
(ontology-studio 의 역바인딩·전략3, neo4j-text2sql)은 ``:Table`` / ``:Column`` 을 읽는다.
두 라벨은 대소문자가 달라 서로 다른 라벨이므로, 분석 결과는 그대로 두면 **카탈로그 소비자에게
보이지 않는다.**

예전에는 노드에 두 라벨을 함께 붙여(`SET t:Table`) 이 문제를 피했는데 두 가지가 깨졌다 —
wipe 가 카탈로그를 지웠고, data-fabric 이 쓴 이름(`tb01`)과 DDL 표기(`TB01`)가 달라 같은
테이블이 두 노드가 됐다. 자세한 경위는 ``nodes/table.py`` 의 ``MERGE_QUERY`` 주석에 있다.

## 무엇을 투영하는가

**설명뿐이다.** 구조(테이블·컬럼의 존재, 타입, 키)는 data-fabric 이 소스 DB 에서 읽어 쓴 것이
진실이고, 분석기가 DDL 텍스트에서 읽은 것은 그보다 낡을 수 있다. 헌법 원칙 I·III —
카탈로그의 writer 는 data-fabric 하나다.

## 이름 대조는 대소문자를 무시한다

PostgreSQL 은 따옴표 없는 ``TB01`` 을 ``tb01`` 로 접는다. DDL 텍스트에는 ``TB01`` 로 적혀
있으므로, 대소문자를 구분해 맞추면 **한 건도 맞지 않는다.**

## 덮어쓰지 않는 것

사람이 손으로 넣은 설명(``description_source`` 가 ``manual:`` 로 시작)은 건드리지 않는다.
그러지 않으면 사용자가 고쳐 둔 설명이 다음 분석에서 조용히 사라진다.
"""
from __future__ import annotations

from shared.neo4j.neo4j_client import Neo4jClient


# 분석기 노드 → 카탈로그 노드. 이름·스키마를 소문자로 접어 맞춘다.
#
# `datasource`/`db` 를 함께 보는 이유는 헌법 원칙 II — `:Table` 의 MERGE 키가 `(name, schema)`
# 2-튜플이라, 데이터소스로 함께 거르지 않으면 다른 DB 의 같은 `schema.table` 과 섞인다.
PROJECT_TABLES = """
MATCH (src:TABLE)
WHERE coalesce(src.description, '') <> ''
MATCH (dst:Table)
WHERE (dst.datasource = src.datasource OR dst.db = src.db)
  AND toLower(coalesce(dst.schema, '')) = toLower(coalesce(src.schema, ''))
  AND toLower(coalesce(dst.name, ''))   = toLower(coalesce(src.name, ''))
  AND NOT dst:TABLE
  AND NOT coalesce(dst.description_source, '') STARTS WITH 'manual:'
SET dst.description = src.description,
    dst.description_source = coalesce(src.description_source, 'code'),
    // 카드 제목 자리. 이것을 옮기지 않으면 논리명 모드에서 컬럼만 바뀌고 테이블은
    // 물리명(`tb02`)으로 남는다 — 실측으로 확인한 증상이다.
    dst.logical_name = coalesce(src.logical_name, dst.logical_name)
RETURN count(dst) AS projected
"""

# 컬럼은 테이블을 거쳐 맞춘다. `:COLUMN.fqn` 은 `db.schema.table.column` 이지만 표기가
# 갈릴 수 있어 신뢰하지 않고, 관계로 소속 테이블을 찾은 뒤 이름으로 맞춘다.
PROJECT_COLUMNS = """
MATCH (st:TABLE)-[:HAS_COLUMN]->(sc:COLUMN)
WHERE coalesce(sc.description, '') <> ''
MATCH (dt:Table)-[:HAS_COLUMN]->(dc:Column)
WHERE (dt.datasource = st.datasource OR dt.db = st.db)
  AND toLower(coalesce(dt.schema, '')) = toLower(coalesce(st.schema, ''))
  AND toLower(coalesce(dt.name, ''))   = toLower(coalesce(st.name, ''))
  AND toLower(coalesce(dc.name, ''))   = toLower(coalesce(sc.name, ''))
  AND NOT dc:COLUMN
  AND NOT coalesce(dc.description_source, '') STARTS WITH 'manual:'
SET dc.description = sc.description,
    dc.description_source = coalesce(sc.description_source, 'code')
RETURN count(dc) AS projected
"""


async def project_to_catalog(client: Neo4jClient) -> dict[str, int]:
    """분석 종료 후 1회 호출. 투영한 건수를 돌려준다.

    건수가 0이면 **조용히 넘기지 말아야 한다** — 카탈로그에 대응 노드가 없다는 뜻이고,
    그러면 온톨로지 구축이 분석 결과를 영영 못 본다. 호출부가 로그로 남긴다.
    """
    # `run_query` 는 레코드가 아니라 요약(ResultSummary)을 돌려준다. 그것으로 건수를 읽으면
    # 투영이 실제로 됐는데도 늘 0 이 나와 "카탈로그에 대응 노드가 없다"는 거짓 경고가 떴다.
    tables = await client.run_read_query(PROJECT_TABLES)
    columns = await client.run_read_query(PROJECT_COLUMNS)

    def first(rows, key):
        try:
            return int(rows[0][key]) if rows else 0
        except (KeyError, IndexError, TypeError, ValueError):
            return 0

    return {"tables": first(tables, "projected"), "columns": first(columns, "projected")}
