"""REFER_TO 엣지 — Function → 외부 자원.

target_type 별 노드 라벨 분기 (단순 추상화 X):
- ``sql_statement``  → ``:SqlStatement {sql_key}``
- ``config_section`` → ``:ConfigSection {config_key}``
- ``unresolved``     → ``:UnresolvedRef {target_key}`` (사람 검토 큐)

각 타입별 unique 키 + Function 와의 REFER_TO 엣지.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from data_models.graph_edges import ReferTo
from shared.neo4j.neo4j_client import Neo4jClient


_SQL_QUERY = """
UNWIND $items AS item
MATCH (f:Function {function_id: item.function_id})
MERGE (e:SqlStatement {sql_key: item.target_key})
MERGE (f)-[r:REFER_TO]->(e)
SET r.evidence_file = item.evidence_file,
    r.evidence_line = item.evidence_line
"""

_CONFIG_QUERY = """
UNWIND $items AS item
MATCH (f:Function {function_id: item.function_id})
MERGE (e:ConfigSection {config_key: item.target_key})
MERGE (f)-[r:REFER_TO]->(e)
SET r.evidence_file = item.evidence_file,
    r.evidence_line = item.evidence_line
"""

_UNRESOLVED_QUERY = """
UNWIND $items AS item
MATCH (f:Function {function_id: item.function_id})
MERGE (e:UnresolvedRef {target_key: item.target_key})
MERGE (f)-[r:REFER_TO]->(e)
SET r.evidence_file = item.evidence_file,
    r.evidence_line = item.evidence_line
"""

_QUERIES = {
    "sql_statement": _SQL_QUERY,
    "config_section": _CONFIG_QUERY,
    "unresolved": _UNRESOLVED_QUERY,
}


async def upsert_refer_to(client: Neo4jClient, edges: Iterable[ReferTo]) -> None:
    """target_type 별 분기 후 각 라벨 노드 + REFER_TO 엣지."""
    by_type: dict[str, list[dict]] = defaultdict(list)
    for e in edges:
        item = {
            "function_id": e.function_id,
            "target_key": e.target_key,
            "evidence_file": e.evidence_file,
            "evidence_line": e.evidence_line,
        }
        by_type[e.target_type].append(item)

    for target_type, items in by_type.items():
        query = _QUERIES.get(target_type)
        if query is None:
            continue
        await client.run_batch_unwind(query, items)
