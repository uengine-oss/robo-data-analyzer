"""PACKAGE 노드 Cypher — Module 들의 상위 네임스페이스 (주로 Java packageName)."""
from __future__ import annotations

from typing import Iterable

from data_models.graph_nodes import Package
from shared.neo4j.neo4j_client import Neo4jClient


MERGE_QUERY = """
UNWIND $items AS item
MERGE (p:PACKAGE {package_id: item.package_id})
SET p.name = item.name
RETURN count(p) AS created
"""


def to_item(pkg: Package) -> dict:
    return {"package_id": pkg.package_id, "name": pkg.name}


async def upsert_packages(client: Neo4jClient, packages: Iterable[Package]) -> None:
    items = [to_item(p) for p in packages]
    await client.run_batch_unwind(MERGE_QUERY, items)
