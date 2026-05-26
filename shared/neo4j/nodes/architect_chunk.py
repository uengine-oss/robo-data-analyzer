"""ARCHITECT_CHUNK 노드 Cypher — 구조화 파일(XML/YAML 등)의 키-값 청크.

라벨: ``:ARCHITECT_CHUNK``. 키 = ``chunk_key``.
linking(step4)이 코드가 실제로 참조하는 청크만 생성 — Function 이 REFER_TO 로 가리킨다.
"""
from __future__ import annotations

from typing import Iterable

from data_models.graph_nodes import ArchitectChunk
from shared.neo4j.neo4j_client import Neo4jClient


MERGE_QUERY = """
UNWIND $items AS item
MERGE (c:ARCHITECT_CHUNK {chunk_key: item.chunk_key})
SET c.body = item.body,
    c.file_path = item.file_path
RETURN count(c) AS created
"""


def to_item(c: ArchitectChunk) -> dict:
    return {"chunk_key": c.chunk_key, "body": c.body, "file_path": c.file_path}


async def upsert_architect_chunks(
    client: Neo4jClient, chunks: Iterable[ArchitectChunk],
) -> None:
    await client.run_batch_unwind(MERGE_QUERY, [to_item(c) for c in chunks])
