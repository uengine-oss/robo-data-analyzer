"""stereotype(역할)을 노드 '라벨'로도 부여 (spec 033, 032 재적용).

배경: 라벨=antlr 타입 단일(spec 019), stereotype=속성(spec 020) 이었으나, 소비처(프론트
필터·색상, neo4j 쿼리)가 역할로 필터/색칠하기 쉽게 stereotype 을 **라벨**로 올린다.

- stereotype 값 = 동적 response schema와 ``require_stereotype``이
  ``ontology_roles.yaml`` 풀에서 검증한 값 → 식별자
  형태(Service/Command/DataMutator…)라 라벨 안전. 추가 방어로 백틱(``:`X```) 사용.
- ★무중단: 속성(``stereotype``)도 당분간 **유지**(노드패널·module_retriever 가 아직 읽음).
  소비처를 위치기반 라벨로 이관한 뒤 속성 제거(후속 spec, 회귀 0).
- 동적 라벨이라 stereotype 값별로 묶어 ``SET node:`<값>` `` (Cypher 는 파라미터 라벨 불가).
- 적재 시점: 타입 라벨(step2 load_ast) 이후(step6/step7) → ``labels[0]``=antlr 타입 보장.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Iterable

from graph.neo4j_client import Neo4jClient
from graph.ownership import ANALYZER_GRAPH_OWNER


def _label_of(stereotype: str) -> str:
    """stereotype 값 → Neo4j 라벨용 UPPER_SNAKE (spec 036).

    architect 의 DDD/CQRS 라벨(:Command/:Event…, PascalCase)과 전역 네임스페이스를
    공유하면 architect 삭제에 휩쓸린다 → 표기를 분리. 'Command'→'COMMAND',
    'ApplicationService'→'APPLICATION_SERVICE'. 속성 ``stereotype`` 은 PascalCase 유지.
    """
    return re.sub(r"(?<!^)(?=[A-Z])", "_", stereotype).upper()


def _role_labels() -> tuple[str, ...]:
    """Return every Analyzer-owned ontology role label.

    The list is generated from the language contracts so adding a role cannot
    silently leave an old graph label behind.
    """
    from pipeline.languages import module_stereotype_values, stereotype_values

    return tuple(sorted({
        _label_of(value)
        for strategy in ("framework", "dbms")
        for value in (
            *stereotype_values(strategy),
            *module_stereotype_values(strategy),
        )
    }))


def _role_label_removals() -> str:
    return ", ".join(f"node:`{label}`" for label in _role_labels())


def _set_label_query(label: str) -> str:
    # label 은 검증풀 파생 UPPER_SNAKE — 백틱으로 한 번 더 방어(특수문자 시에도 안전).
    return f"""
UNWIND $items AS item
MATCH (node:ANALYZER_OWNED {{id: item.id, graph_owner: '{ANALYZER_GRAPH_OWNER}', generation_id: $generation_id}})
REMOVE {_role_label_removals()}
SET node:`{label}`
"""


def _clear_role_labels_query() -> str:
    """Remove every ontology-owned role label before applying the current one."""
    return f"""
UNWIND $items AS item
MATCH (node:ANALYZER_OWNED {{id: item.id, graph_owner: '{ANALYZER_GRAPH_OWNER}', generation_id: $generation_id}})
REMOVE {_role_label_removals()}
"""


async def apply_stereotype_labels(
    client: Neo4jClient, pairs: Iterable[tuple[str, str]]
) -> None:
    """``(node_id, stereotype)`` 쌍에 stereotype 라벨 부여. 빈 stereotype 은 건너뜀.

    라벨은 UPPER_SNAKE(``_label_of``, spec 036) — 속성 ``stereotype`` 은 PascalCase 유지.
    같은 라벨끼리 묶어 1 배치로 ``SET node:`<라벨>` `` (값별 동적 라벨).
    """
    # A streaming caller can accidentally present the same node more than once.
    # One storage boundary must have one current role, so the last observation wins.
    latest_by_id: dict[str, str] = {}
    for node_id, stereotype in pairs:
        latest_by_id[node_id] = stereotype
    materialized = list(latest_by_id.items())
    by_label: dict[str, list[dict]] = defaultdict(list)
    empty_items: list[dict] = []
    for node_id, stereotype in materialized:
        if stereotype:
            by_label[_label_of(stereotype)].append({"id": node_id})
        else:
            empty_items.append({"id": node_id})
    for label, items in by_label.items():
        await client.run_batch_unwind(_set_label_query(label), items)
    if empty_items:
        await client.run_batch_unwind(_clear_role_labels_query(), empty_items)
