"""CALLS 엣지 — Function → Function (호출 관계).

internal 콜리는 그래프에 정의 노드가 이미 있어 ``MATCH`` 로 연결한다.
external 콜리(분석 범위 밖 실 프로시저/메소드)는 정의 노드가 없으므로 — legacy 와
동일하게 — ``:FUNCTION:EXTERNAL`` stub 노드를 그 모듈(``:MODULE:EXTERNAL``)·스키마
(``:PACKAGE``) 와 함께 생성해 internal 구조와 대칭으로 호출을 연결한다.

분해 정보(모듈/스키마/이름)는 strategy 별 linker 가 **언어 의미**로 채운다
(DBMS=스키마·프로시저, Framework=패키지·클래스). 여기선 그 값으로 노드를 균일하게 박는다.
Framework / DBMS 공유 — 호출 그래프 쓰기는 이 한 곳뿐.
"""
from __future__ import annotations

from typing import Iterable

from data_models.graph_edges import Calls
from shared.neo4j.neo4j_client import Neo4jClient
from shared.neo4j.rel_types import BELONGS_TO_PACKAGE, CALLS, HAS_FUNCTION


# internal — 콜리 정의 노드가 이미 존재 → MATCH 로 연결 (없으면 자연 제외).
INTERNAL_QUERY = f"""
UNWIND $items AS item
MATCH (caller:FUNCTION {{function_id: item.caller_function_id}})
MATCH (callee:FUNCTION {{function_id: item.callee_function_id}})
MERGE (caller)-[r:{CALLS}]->(callee)
SET r.scope = item.scope
"""

# external — 콜리 정의 노드 없음 → stub(:FUNCTION:EXTERNAL) + 모듈(:MODULE:EXTERNAL)
# + 스키마(:PACKAGE, 있을 때) 를 internal 과 대칭으로 생성하고 호출 연결.
EXTERNAL_QUERY = f"""
UNWIND $items AS item
MATCH (caller:FUNCTION {{function_id: item.caller_function_id}})
MERGE (callee:FUNCTION:EXTERNAL {{function_id: item.callee_function_id}})
  ON CREATE SET callee.signature = item.callee_name,
                callee.module_id = item.callee_module_id,
                callee.package_id = item.callee_package_id
MERGE (m:MODULE:EXTERNAL {{module_id: item.callee_module_id}})
  ON CREATE SET m.name = item.callee_name
MERGE (m)-[:{HAS_FUNCTION}]->(callee)
FOREACH (_ IN CASE WHEN item.callee_package_id <> '' THEN [1] ELSE [] END |
  MERGE (p:PACKAGE {{package_id: item.callee_package_id}})
  MERGE (m)-[:{BELONGS_TO_PACKAGE}]->(p)
)
MERGE (caller)-[r:{CALLS}]->(callee)
SET r.scope = item.scope
"""


async def upsert_calls(client: Neo4jClient, edges: Iterable[Calls]) -> None:
    """CALLS 업서트 — internal 은 기존 노드 연결, external 은 stub 노드 생성·연결.

    external 의 모듈/이름은 linker 가 비웠으면 function_id 로 보충 (스키마는 빈 채 →
    PACKAGE 연결 생략). MERGE 가 중복 dedup.
    """
    edges = list(edges)
    internal = [{
        "caller_function_id": e.caller_function_id,
        "callee_function_id": e.callee_function_id,
        "scope": e.scope,
    } for e in edges if e.scope == "internal"]
    external = [{
        "caller_function_id": e.caller_function_id,
        "callee_function_id": e.callee_function_id,
        "callee_module_id": e.callee_module_id or e.callee_function_id,
        "callee_package_id": e.callee_package_id,
        "callee_name": e.callee_name or e.callee_function_id,
        "scope": e.scope,
    } for e in edges if e.scope == "external"]
    if internal:
        await client.run_batch_unwind(INTERNAL_QUERY, internal)
    if external:
        await client.run_batch_unwind(EXTERNAL_QUERY, external)
