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

from graph.schema.graph_edges import Calls
from graph.neo4j_client import Neo4jClient
from graph.schema.rel_types import BELONGS_TO, CALLS, HAS_MEMBER
from graph.schema.node_ids import child_id, external_id
from graph.node_upsert import ANALYZER_GRAPH_OWNER


# internal — 콜리 정의 노드가 이미 존재 → MATCH 로 연결 (없으면 자연 제외).
INTERNAL_QUERY = f"""
UNWIND $items AS item
MATCH (caller {{id: item.caller_id}})
MATCH (callee {{id: item.callee_id}})
MERGE (caller)-[r:{CALLS}]->(callee)
SET r.scope = item.scope,
    r.recursive = item.recursive
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
  SET callee.name = coalesce(callee.name, item.callee_name),
      callee.type = coalesce(callee.type, 'FUNCTION'),
      callee.graph_owner = '{ANALYZER_GRAPH_OWNER}'
MERGE (m:MODULE:EXTERNAL {{id: item.callee_module}})
  SET m.name = coalesce(m.name, item.callee_name),
      m.type = coalesce(m.type, 'MODULE'),
      m.graph_owner = '{ANALYZER_GRAPH_OWNER}'
MERGE (m)-[:{HAS_MEMBER}]->(callee)
FOREACH (_ IN CASE WHEN item.callee_package <> '' THEN [1] ELSE [] END |
  MERGE (p:PACKAGE {{id: item.callee_package}})
  SET p.graph_owner = '{ANALYZER_GRAPH_OWNER}'
  MERGE (m)-[:{BELONGS_TO}]->(p)
)
MERGE (caller)-[r:{CALLS}]->(callee)
SET r.scope = item.scope
"""


def _split_calls(edges: Iterable[Calls]) -> tuple[list[dict], list[dict]]:
    """edges → (internal items, external items). 저장(upsert)·라이브 발신(emit) 공유 단일출처.

    외부 = 컨테이너(MODULE) + 엔트리 함수(FUNCTION). 둘의 id 구분(식별키 유일성):
    모듈 id = 호출 모듈명, 엔트리 함수 id = "{module}.{name}" (내부 owner.name 패턴과 동일).
    모듈/이름은 linker 가 비웠으면 콜리 id 로 보충.
    """
    internal, external = [], []
    for e in edges:
        if e.scope == "internal":
            internal.append({
                "caller_id": e.caller_id,
                "callee_id": e.callee_id,
                "scope": e.scope,
                "recursive": e.caller_id == e.callee_id,
            })
        elif e.scope == "external":
            module_name = e.callee_module or e.callee_id
            name = e.callee_name or e.callee_id
            module_key = f"{e.callee_package}/{module_name}" if e.callee_package else module_name
            module_id = external_id("module", module_key)
            package_id = external_id("package", e.callee_package) if e.callee_package else ""
            external.append({
                "caller_id": e.caller_id,
                "callee_id": child_id(module_id, name),
                "callee_module": module_id,
                "callee_package": package_id,
                "callee_name": name,
                "scope": e.scope,
            })
    return internal, external


async def upsert_calls(client: Neo4jClient, edges: Iterable[Calls]) -> None:
    """CALLS 업서트 — internal 은 기존 노드 연결, external 은 stub 노드 생성·연결. MERGE 가 dedup."""
    internal, external = _split_calls(list(edges))
    if internal:
        await client.run_batch_unwind(INTERNAL_QUERY, internal)
    if external:
        await client.run_batch_unwind(EXTERNAL_QUERY, external)


def emit_calls(stream, edges: Iterable[Calls]) -> None:
    """CALLS 라이브 발신 — 저장(upsert_calls)과 **같은 그래프**를 스트림으로(_split_calls 공유 = 드리프트 0).

    internal=CALLS 엣지. external=stub 노드(:FUNCTION:EXTERNAL / :MODULE:EXTERNAL) + HAS_MEMBER +
    CALLS(+PACKAGE/BELONGS_TO). 끝점 id 가 저장과 동일해 라이브=neo4j. (과거엔 raw callee_id 만
    보내 외부 CALLS 가 끝점 노드 없이 떠다니고 stub 노드가 라이브에 안 떴음.)
    """
    if stream is None:
        return
    internal, external = _split_calls(list(edges))
    for it in internal:
        stream.emit({
            "type": "relationship_event", "op": "merge", "rel_type": CALLS,
            "from": {"id": it["caller_id"]}, "to": {"id": it["callee_id"]},
            "props": {"recursive": it["recursive"]},
        })
    for it in external:
        cid, mid, pkg, nm = it["callee_id"], it["callee_module"], it["callee_package"], it["callee_name"]
        stream.emit({"type": "node_event", "op": "merge", "label": "FUNCTION",
                     "labels": ["FUNCTION", "EXTERNAL"], "id": cid, "props": {"name": nm}})
        stream.emit({"type": "node_event", "op": "merge", "label": "MODULE",
                     "labels": ["MODULE", "EXTERNAL"], "id": mid, "props": {"name": nm}})
        stream.emit({"type": "relationship_event", "op": "merge", "rel_type": HAS_MEMBER,
                     "from": {"id": mid}, "to": {"id": cid}, "props": {}})
        if pkg:
            stream.emit({"type": "node_event", "op": "merge", "label": "PACKAGE",
                         "labels": ["PACKAGE"], "id": pkg, "props": {}})
            stream.emit({"type": "relationship_event", "op": "merge", "rel_type": BELONGS_TO,
                         "from": {"id": mid}, "to": {"id": pkg}, "props": {}})
        stream.emit({"type": "relationship_event", "op": "merge", "rel_type": CALLS,
                     "from": {"id": it["caller_id"]}, "to": {"id": cid}, "props": {}})
