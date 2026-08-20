"""PACKAGE 노드 Cypher — Module 들의 상위 네임스페이스 (주로 Java packageName)."""
from __future__ import annotations

from typing import Iterable

from graph.schema.graph_nodes import Package
from graph.neo4j_client import Neo4jClient
from graph.ownership import ANALYZER_GRAPH_OWNER


MERGE_QUERY = f"""
UNWIND $items AS item
MERGE (p:ANALYZER_OWNED:PACKAGE {{id: item.id, graph_owner: '{ANALYZER_GRAPH_OWNER}', generation_id: $generation_id}})
SET p:ANALYZER_OWNED,
    p.name = item.name,
    p.graph_owner = '{ANALYZER_GRAPH_OWNER}'
RETURN count(p) AS created
"""


def to_item(pkg: Package) -> dict:
    return {"id": pkg.id, "name": pkg.name}


async def upsert_packages(client: Neo4jClient, packages: Iterable[Package]) -> None:
    items = [to_item(p) for p in packages]
    await client.run_batch_unwind(MERGE_QUERY, items)
