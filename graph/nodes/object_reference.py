"""Unresolved OBJECT_REFERENCE node persistence."""
from __future__ import annotations

from typing import Iterable

from graph.neo4j_client import Neo4jClient
from graph.node_upsert import nonempty
from graph.ownership import ANALYZER_GRAPH_OWNER
from graph.schema.graph_nodes import ObjectReference


MERGE_QUERY = f"""
UNWIND $items AS item
MERGE (n:ANALYZER_OWNED:OBJECT_REFERENCE {{id: item.id, graph_owner: '{ANALYZER_GRAPH_OWNER}', generation_id: $generation_id}})
SET n:ANALYZER_OWNED,
    n += item,
    n.graph_owner = '{ANALYZER_GRAPH_OWNER}'
"""


async def upsert_object_references(
    client: Neo4jClient, nodes: Iterable[ObjectReference],
) -> None:
    items = [nonempty({
        "id": node.id,
        "datasource": node.datasource,
        "raw_reference": node.raw_reference,
        "name": node.name,
        "schema": node.schema,
        "schema_quoted": node.schema_quoted,
        "name_quoted": node.name_quoted,
        "database_link": node.database_link,
        "status": node.status,
    }) for node in nodes]
    if items:
        await client.run_batch_unwind(MERGE_QUERY, items)
