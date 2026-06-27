"""stereotype(역할)을 노드 '라벨'로도 부여 (spec 032).

배경: 라벨=antlr 타입 단일(spec 019), stereotype=속성(spec 020) 이었으나, 소비처(프론트
필터·색상, neo4j 쿼리)가 역할로 필터/색칠하기 쉽게 stereotype 을 **라벨**로 올린다.

- stereotype 값 = ``ontology_roles.yaml`` 풀에서 검증된 값(``coerce_stereotype``) → 식별자
  형태(Service/Command/DataMutator…)라 라벨 안전. 추가 방어로 백틱(``:`X```) 사용.
- ★무중단: 속성(``stereotype``)도 당분간 **유지**(노드패널·module_retriever 가 아직 읽음).
  소비처를 라벨 기반으로 이관한 뒤 속성 제거(spec 038, 회귀 0).
- 동적 라벨이라 stereotype 값별로 묶어 ``SET node:`<값>` `` (Cypher 는 파라미터 라벨 불가).
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from shared.neo4j.neo4j_client import Neo4jClient


def _set_label_query(stereotype: str) -> str:
    # stereotype 은 검증풀 통제값 — 백틱으로 한 번 더 방어(공백/특수문자 시에도 안전).
    return f"""
UNWIND $items AS item
MATCH (node {{id: item.id}})
SET node:`{stereotype}`
"""


async def apply_stereotype_labels(
    client: Neo4jClient, pairs: Iterable[tuple[str, str]]
) -> None:
    """``(node_id, stereotype)`` 쌍에 stereotype 라벨 부여. 빈 stereotype 은 건너뜀.

    같은 stereotype 끼리 묶어 1 배치로 ``SET node:`<값>` `` (값별 동적 라벨).
    """
    by_st: dict[str, list[dict]] = defaultdict(list)
    for node_id, stereotype in pairs:
        if stereotype:
            by_st[stereotype].append({"id": node_id})
    for stereotype, items in by_st.items():
        await client.run_batch_unwind(_set_label_query(stereotype), items)
