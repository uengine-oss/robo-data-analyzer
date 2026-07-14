"""노드 MERGE 의 **단일 정의처** (헌법 III / spec 046 FR-004).

종전엔 ``nodes/*.py`` 마다 아래 4줄이 라벨만 바꿔 복붙돼 있었다 (노드 13파일 + 엣지 14파일에
``UNWIND $items AS item`` 이 **32번**). 새 노드 종류를 추가할 때마다 그중 하나를 복붙해야 했고,
한 곳만 빠뜨리면 조용히 안 저장된다.

    UNWIND $items AS item
    MERGE (n:<LABEL> {id: item.id})
    SET n += item

**빈값 미저장**(spec 020): ``SET n += item`` 이라 ``props.nonempty()`` 가 빼둔 키는 애초에
노드에 안 박힌다 → 노드가 자기 종류에 의미 있는 속성만 갖는다.

**라벨은 통제값**: ``CodeNode.label`` 등 antlr type 에서 온 값이라 주입 안전(사용자 입력 아님).
그리고 이 라벨 직결이 프론트의 색·필터·카드 라우팅의 근거다 — 끊으면 전부 회귀한다.

★ **정당한 예외**: 테이블 노드(``nodes/table.py``)는 이 경로를 쓰지 않는다. 속성을 하나씩
명시 SET 하며 ``coalesce`` 로 **DDL 1차 설명을 보존**해야 하기 때문 (``SET n += item`` 은
덮어쓴다). 예외임을 여기와 그쪽 docstring 양쪽에 못박는다.

향후: analyzer 소유 노드만 지우는 삭제(소유 마커 라벨)가 필요해지면 **이 파일 1줄**
(``SET n:ANALYZED, n += item``)로 전 노드에 도장이 찍힌다 — 종전엔 13곳을 고쳐야 했다.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Callable, Iterable

from shared.neo4j.neo4j_client import Neo4jClient


def merge_query(label: str) -> str:
    """``:label`` 노드를 id 로 MERGE + 빈값 제외 속성 SET. 라벨은 통제값(주입 안전)."""
    return f"""
UNWIND $items AS item
MERGE (n:{label} {{id: item.id}})
SET n += item
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
