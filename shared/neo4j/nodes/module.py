"""Module 노드 Cypher — Function 들의 컨테이너.

summary / module_stereotype 는 summarize_module_step 이 별도 쿼리로 갱신.
"""
from __future__ import annotations

from typing import Iterable

from data_models.graph_nodes import Module
from shared.neo4j.neo4j_client import Neo4jClient


MERGE_QUERY = """
UNWIND $items AS item
MERGE (m:Module {module_id: item.module_id})
SET m.name = item.name,
    m.file_path = item.file_path,
    m.summary = coalesce(item.summary, m.summary),
    m.module_stereotype = coalesce(item.module_stereotype, m.module_stereotype)
RETURN count(m) AS created
"""


def to_item(mod: Module) -> dict:
    return {
        "module_id": mod.module_id,
        "name": mod.name,
        "file_path": mod.file_path,
        "summary": mod.summary or None,
        "module_stereotype": mod.module_stereotype or None,
    }


async def upsert_modules(client: Neo4jClient, modules: Iterable[Module]) -> None:
    items = [to_item(m) for m in modules]
    await client.run_batch_unwind(MERGE_QUERY, items)
