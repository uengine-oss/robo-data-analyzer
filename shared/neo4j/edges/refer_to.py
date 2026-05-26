"""REFER_TO 엣지 — Function → 구조화 파일의 키-값 청크 (ARCHITECT_CHUNK).

``architect_chunk`` → ``:ARCHITECT_CHUNK {chunk_key}`` 노드 + Function 의 REFER_TO 엣지.

※ 보류 — architect 청크 링킹은 재설계 전까지 파이프라인에서 비활성
  (계획 §2.8 Issue I). 이 upsert 인프라는 휴면 상태로 보존 — 삭제 금지.
"""
from __future__ import annotations

from typing import Iterable

from data_models.graph_edges import ReferTo
from shared.neo4j.neo4j_client import Neo4jClient
from shared.neo4j.rel_types import REFER_TO


_CHUNK_QUERY = f"""
UNWIND $items AS item
MATCH (f:FUNCTION {{function_id: item.function_id}})
MERGE (e:ARCHITECT_CHUNK {{chunk_key: item.target_key}})
MERGE (f)-[r:{REFER_TO}]->(e)
SET r.evidence_file = item.evidence_file,
    r.evidence_line = item.evidence_line
"""


async def upsert_refer_to(client: Neo4jClient, edges: Iterable[ReferTo]) -> None:
    """ARCHITECT_CHUNK 노드 + Function→청크 REFER_TO 엣지 일괄 업서트."""
    items = [
        {
            "function_id": e.function_id,
            "target_key": e.target_key,
            "evidence_file": e.evidence_file,
            "evidence_line": e.evidence_line,
        }
        for e in edges
    ]
    if items:
        await client.run_batch_unwind(_CHUNK_QUERY, items)
