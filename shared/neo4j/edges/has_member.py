"""HAS_MEMBER 엣지 — DataType → Variable (struct / union 멤버)."""
from __future__ import annotations

from typing import Iterable

from data_models.graph_edges import HasMember
from shared.neo4j.neo4j_client import Neo4jClient
from shared.neo4j.rel_types import HAS_MEMBER


MERGE_QUERY = f"""
UNWIND $items AS item
MATCH (d:DATA_TYPE {{datatype_id: item.datatype_id}})
MATCH (v:VARIABLE {{variable_id: item.variable_id}})
MERGE (d)-[:{HAS_MEMBER}]->(v)
"""


async def upsert_has_member(client: Neo4jClient, edges: Iterable[HasMember]) -> None:
    items = [{"datatype_id": e.datatype_id, "variable_id": e.variable_id} for e in edges]
    await client.run_batch_unwind(MERGE_QUERY, items)
