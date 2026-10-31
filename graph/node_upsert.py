"""노드 MERGE와 저장 속성 정규화의 **단일 정의처** (헌법 III / spec 046 FR-004).

종전엔 ``nodes/*.py`` 마다 아래 4줄이 라벨만 바꿔 복붙돼 있었다 (노드 13파일 + 엣지 14파일에
``UNWIND $items AS item`` 이 **32번**). 새 노드 종류를 추가할 때마다 그중 하나를 복붙해야 했고,
한 곳만 빠뜨리면 조용히 안 저장된다.

    UNWIND $items AS item
    MERGE (n:<LABEL> {id: item.id, graph_owner: 'analyzer'})
    SET n += item

**빈값 미저장**(spec 020): ``SET n += item`` 이라 ``props.nonempty()`` 가 빼둔 키는 애초에
노드에 안 박힌다 → 노드가 자기 종류에 의미 있는 속성만 갖는다.

**라벨은 통제값**: ``CodeNode.label`` 등 antlr type 에서 온 값이라 주입 안전(사용자 입력 아님).
그리고 이 라벨 직결이 프론트의 색·필터·카드 라우팅의 근거다 — 끊으면 전부 회귀한다.

★ **정당한 예외**: 테이블 노드(``nodes/table.py``)는 이 경로를 쓰지 않는다. 속성을 하나씩
명시 SET 하며 ``coalesce`` 로 **DDL 1차 설명을 보존**해야 하기 때문 (``SET n += item`` 은
덮어쓴다). 예외임을 여기와 그쪽 docstring 양쪽에 못박는다.

★ **:ANALYZER_OWNED 마커 라벨**: 모든 analyzer 노드에 찍는다. 코드 노드 라벨은 열린 집합이라
개별 인덱스를 못 걸므로, 이 공통 라벨의 id 유니크 제약(``constraints.py``)이 유일한
인덱스 경로다 — 무라벨 ``MATCH {id}`` 는 AllNodesScan(엣지 1건당 전 노드 스캔)이 되어
AST 적재 159초의 원인이었다 (run6 실측 + EXPLAIN 확인).

★ **라벨은 만들 때 한 번에 다 준다** (``:ANALYZER_OWNED:<종류>``). Ontological DB 는 라벨을
단일 상속 타입 사슬로 본다 — ``CREATE (n:A:B)`` 는 "B 는 A 의 하위 타입"이고, 만들어진 노드는
나중에 라벨을 얻지 못한다(``SET n:X`` → "a node's type is part of its identifier"). 그래서 예전처럼
``MERGE (n:ANALYZER_OWNED) SET n:<종류>`` 로 뒤에 붙이면 거기서 첫 쓰기부터 죽는다.
사슬 순서도 이 방향이어야 한다 — 공통 부모(ANALYZER_OWNED) 아래에 종류가 매달려야
``MATCH (n:ANALYZER_OWNED {id})`` 가 모든 종류를 찾는다. Neo4j 에서는 라벨 집합이 같으므로
의미가 같다. 나중에 붙던 표지(역할·외부 stub·임베딩 보유)는 전부 **속성**이다.
단, Ontological 로 보낼 때 ``MATCH (n:ANALYZER_OWNED {id})`` 는 무라벨로 바뀐다 — 부모 라벨 조회가
하위 타입 합집합 뷰를 타서 깨지기 때문이다 (``graph/neo4j_client.py`` 의 ``for_ontological``).
"""
from __future__ import annotations

from collections import defaultdict
from typing import Callable, Iterable

from graph.neo4j_client import Neo4jClient


from graph.ownership import ANALYZER_GRAPH_OWNER


def nonempty(props: dict) -> dict:
    """빈 문자열·None 키를 제거하고 0·False 같은 의미값은 유지한다."""
    return {key: value for key, value in props.items() if value not in (None, "")}


def join_notes(*parts: str) -> str:
    """비어 있지 않은 원본 부가 텍스트를 하나의 notes 값으로 합친다."""
    return "\n".join(part.strip() for part in parts if part and part.strip())


def merge_query(label: str) -> str:
    """id 로 MERGE + 빈값 제외 속성 SET. 라벨은 통제값(주입 안전).

    MERGE 는 ``:ANALYZER_OWNED`` 유니크 제약 인덱스를 타고, 종류 라벨은 **MERGE 패턴에서 함께**
    준다 — 만든 뒤 ``SET n:<종류>`` 로 붙이면 Ontological DB 가 거절한다(모듈 docstring).
    id 가 전역 유일(step2 가드)이라 같은 id 가 다른 종류로 다시 오지 않는다.
    """
    return f"""
UNWIND $items AS item
MERGE (n:ANALYZER_OWNED:{label} {{id: item.id, graph_owner: '{ANALYZER_GRAPH_OWNER}'}})
SET n += item,
    n.graph_owner = '{ANALYZER_GRAPH_OWNER}'
RETURN count(n) AS created
"""


async def upsert_fixed_label(
    client: Neo4jClient, label: str, items: Iterable[dict],
) -> None:
    """라벨이 **고정**인 노드 종류 (TABLE/COLUMN/PACKAGE/SCHEMA/RULE/EXAMPLE/QUESTION …)."""
    payload = list(items)
    if not payload:
        return
    await client.run_batch_unwind(merge_query(label), payload)


async def upsert_by_node_label(
    client: Neo4jClient, nodes: Iterable, to_item: Callable[[object], dict],
) -> None:
    """라벨이 **노드마다 다른** 종류 (코드노드·컨테이너·변수·상수·데이터타입).

    라벨 = 자기 antlr type (``node.label``) → 라벨별로 묶어 배치 MERGE.
    """
    by_label: dict[str, list[dict]] = defaultdict(list)
    for node in nodes:
        by_label[node.label].append(to_item(node))
    for label, items in by_label.items():
        await client.run_batch_unwind(merge_query(label), items)
