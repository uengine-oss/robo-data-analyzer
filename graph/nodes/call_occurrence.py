"""Persist Framework call occurrences as evidence, never as meaning owners."""
from __future__ import annotations

from collections.abc import Iterable

from graph.neo4j_client import Neo4jClient
from graph.ownership import ANALYZER_GRAPH_OWNER
from graph.schema.rel_types import HAS_CALL_OCCURRENCE
from pipeline.models.call_resolution import (
    call_resolution_storage_item,
    normalize_call_resolution_ledger,
)


CALL_OCCURRENCE_LABEL = "CALL_OCCURRENCE"

_OWNER_GUARD = f"""
MATCH (owner:ANALYZER_OWNED {{graph_owner: '{ANALYZER_GRAPH_OWNER}', generation_id: $generation_id}})
WHERE owner.id IN $owner_ids
RETURN count(DISTINCT owner) AS matched
"""

_UPSERT_OCCURRENCES = f"""
UNWIND $items AS item
MERGE (occurrence:ANALYZER_OWNED:{CALL_OCCURRENCE_LABEL} {{
  id: item.id,
  graph_owner: '{ANALYZER_GRAPH_OWNER}',
  generation_id: $generation_id
}})
SET occurrence += item,
    occurrence.graph_owner = '{ANALYZER_GRAPH_OWNER}',
    occurrence.generation_id = $generation_id
"""

_LINK_OWNERS = f"""
UNWIND $items AS item
WITH item WHERE item.caller_id <> ''
MATCH (owner:ANALYZER_OWNED {{
  id: item.caller_id,
  graph_owner: '{ANALYZER_GRAPH_OWNER}',
  generation_id: $generation_id
}})
MATCH (occurrence:ANALYZER_OWNED:{CALL_OCCURRENCE_LABEL} {{
  id: item.id,
  graph_owner: '{ANALYZER_GRAPH_OWNER}',
  generation_id: $generation_id
}})
MERGE (owner)-[relation:{HAS_CALL_OCCURRENCE}]->(occurrence)
SET relation.call_id = item.call_id,
    relation.ordinal = item.ordinal,
    relation.generation_id = $generation_id
"""


async def upsert_call_occurrences(
    client: Neo4jClient,
    ledger: Iterable[dict],
) -> int:
    """Atomically guard owners then write the complete occurrence evidence ledger."""
    normalized = normalize_call_resolution_ledger(list(ledger))
    if not normalized:
        return 0
    items = [call_resolution_storage_item(row) for row in normalized]
    owner_ids = sorted({row["caller_id"] for row in normalized if row["caller_id"]})

    async def _write(transaction) -> int:
        if owner_ids:
            result = await transaction.run(
                _OWNER_GUARD,
                owner_ids=owner_ids,
                generation_id=client.generation_id,
            )
            record = await result.single(strict=True)
            matched = int(record["matched"])
            if matched != len(owner_ids):
                raise ValueError(
                    f"call occurrence owner guard matched {matched} of "
                    f"{len(owner_ids)} canonical owners"
                )
        result = await transaction.run(
            _UPSERT_OCCURRENCES,
            items=items,
            generation_id=client.generation_id,
        )
        await result.consume()
        result = await transaction.run(
            _LINK_OWNERS,
            items=items,
            generation_id=client.generation_id,
        )
        await result.consume()
        return len(items)

    return await client.execute_write(_write)
