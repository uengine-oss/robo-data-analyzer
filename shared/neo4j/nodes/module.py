"""MODULE 노드 Cypher — Function 들의 컨테이너.

라벨: ``:MODULE`` + Framework sub-label (``:MODULE:CLASS`` 등 — ``Module.sub_label``).
summary / stereotype / logical_name 은 summarize_module_step 이 갱신.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from data_models.graph_nodes import Module
from shared.neo4j.neo4j_client import Neo4jClient


def _merge_query(label: str) -> str:
    """주어진 라벨로 MERGE 쿼리 생성. label 은 ``Module.sub_label`` 통제값 — 주입 안전."""
    return f"""
UNWIND $items AS item
MERGE (m:{label} {{module_id: item.module_id}})
SET m.name = item.name,
    m.file_path = item.file_path,
    m.start_line = item.start_line,
    m.end_line = item.end_line,
    m.comment = item.comment,
    m.annotations = item.annotations,
    m.summary = coalesce(item.summary, m.summary),
    m.stereotype = coalesce(item.stereotype, m.stereotype),
    m.logical_name = coalesce(item.logical_name, m.logical_name)
RETURN count(m) AS created
"""


def to_item(mod: Module) -> dict:
    return {
        "module_id": mod.module_id,
        "name": mod.name,
        "file_path": mod.file_path,
        "start_line": mod.start_line,
        "end_line": mod.end_line,
        "comment": mod.comment,
        "annotations": mod.annotations,
        "summary": mod.summary or None,
        "stereotype": mod.stereotype or None,
        "logical_name": mod.logical_name,
    }


async def upsert_modules(client: Neo4jClient, modules: Iterable[Module]) -> None:
    """sub_label 별로 묶어 MERGE (멀티라벨 ``:MODULE:CLASS`` 등)."""
    by_sub: dict[str, list[dict]] = defaultdict(list)
    for m in modules:
        by_sub[m.sub_label].append(to_item(m))
    for sub, items in by_sub.items():
        label = f"MODULE:{sub}" if sub else "MODULE"
        await client.run_batch_unwind(_merge_query(label), items)
