"""BELONGS_TO_PACKAGE 엣지 — Module → Package (모듈이 든 폴더)."""
from __future__ import annotations

from typing import Iterable

from data_models.graph_edges import BelongsToPackage
from shared.neo4j.neo4j_client import Neo4jClient
from shared.neo4j.rel_types import BELONGS_TO_PACKAGE


MERGE_QUERY = f"""
UNWIND $items AS item
MATCH (m:MODULE {{module_id: item.module_id}})
MATCH (p:PACKAGE {{package_id: item.package_id}})
MERGE (m)-[:{BELONGS_TO_PACKAGE}]->(p)
"""


async def upsert_belongs_to_package(client: Neo4jClient, edges: Iterable[BelongsToPackage]) -> None:
    items = [{"module_id": e.module_id, "package_id": e.package_id} for e in edges]
    await client.run_batch_unwind(MERGE_QUERY, items)
