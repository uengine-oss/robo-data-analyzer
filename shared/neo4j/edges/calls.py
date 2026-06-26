"""CALLS 엣지 — CodeNode → CodeNode (호출 관계).

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
from shared.neo4j.rel_types import BELONGS_TO, CALLS, HAS_MEMBER


# internal — 콜리 정의 노드가 이미 존재 → MATCH 로 연결 (없으면 자연 제외).
INTERNAL_QUERY = f"""
UNWIND $items AS item
MATCH (caller {{id: item.caller_id}})
MATCH (callee {{id: item.callee_id}})
MERGE (caller)-[r:{CALLS}]->(callee)
SET r.scope = item.scope
"""

# external — 콜리 정의 노드 없음 → stub(:FUNCTION:EXTERNAL) + 소유 컨테이너(:MODULE:EXTERNAL)
# + 스키마(:PACKAGE, 있을 때) 를 internal 과 대칭으로 생성하고 호출 연결.
# 소속은 HAS_MEMBER / BELONGS_TO 엣지로만 표현 (노드에 소속 속성 안 박음).
# ★컨테이너 라벨 = :MODULE:EXTERNAL 유지 (의도적): 외부 콜리의 소유 컨테이너는 종류미상
#   (분석 범위 밖) — 실제 파싱한 그릇만 정체대로(FILE/CLASS), 외부 stub 은 중립 합성 라벨.
EXTERNAL_QUERY = f"""
UNWIND $items AS item
MATCH (caller {{id: item.caller_id}})
MERGE (callee:FUNCTION:EXTERNAL {{id: item.callee_id}})
  ON CREATE SET callee.name = item.callee_name
MERGE (m:MODULE:EXTERNAL {{id: item.callee_module}})
  ON CREATE SET m.name = item.callee_name
MERGE (m)-[:{HAS_MEMBER}]->(callee)
FOREACH (_ IN CASE WHEN item.callee_package <> '' THEN [1] ELSE [] END |
  MERGE (p:PACKAGE {{id: item.callee_package}})
  MERGE (m)-[:{BELONGS_TO}]->(p)
)
MERGE (caller)-[r:{CALLS}]->(callee)
SET r.scope = item.scope
"""


async def upsert_calls(client: Neo4jClient, edges: Iterable[Calls]) -> None:
    """CALLS 업서트 — internal 은 기존 노드 연결, external 은 stub 노드 생성·연결.

    external 의 모듈/이름은 linker 가 비웠으면 콜리 id 로 보충 (스키마는 빈 채 →
    PACKAGE 연결 생략). MERGE 가 중복 dedup.
    """
    edges = list(edges)
    internal = [{
        "caller_id": e.caller_id,
        "callee_id": e.callee_id,
        "scope": e.scope,
    } for e in edges if e.scope == "internal"]
    # 외부 = 컨테이너(MODULE) + 엔트리 함수(FUNCTION). 둘의 id 를 구분한다(식별키 유일성):
    # 모듈 id = 호출 모듈명, 엔트리 함수 id = "{module}.{name}" (내부 owner.name 패턴과 동일)
    # → 같은-id 충돌 없이 모듈·함수가 별개 노드로 공존(화면에 "모듈 → 엔트리 함수").
    external = []
    for e in edges:
        if e.scope != "external":
            continue
        module_id = e.callee_module or e.callee_id
        name = e.callee_name or e.callee_id
        external.append({
            "caller_id": e.caller_id,
            "callee_id": f"{module_id}.{name}",
            "callee_module": module_id,
            "callee_package": e.callee_package,
            "callee_name": name,
            "scope": e.scope,
        })
    if internal:
        await client.run_batch_unwind(INTERNAL_QUERY, internal)
    if external:
        await client.run_batch_unwind(EXTERNAL_QUERY, external)
