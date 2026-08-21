"""Persist grammar-owned import binding occurrences as non-semantic evidence."""
from __future__ import annotations

from collections.abc import Iterable

from graph.neo4j_client import Neo4jClient
from graph.ownership import ANALYZER_GRAPH_OWNER
from pipeline.models.import_resolution import (
    import_resolution_storage_item,
    normalize_import_resolution_ledger,
)


IMPORT_OCCURRENCE_LABEL = "IMPORT_OCCURRENCE"

_UPSERT_OCCURRENCES = f"""
UNWIND $items AS item
MERGE (occurrence:ANALYZER_OWNED:{IMPORT_OCCURRENCE_LABEL} {{
  id: item.id,
  graph_owner: '{ANALYZER_GRAPH_OWNER}',
  generation_id: $generation_id
}})
SET occurrence += item,
    occurrence.graph_owner = '{ANALYZER_GRAPH_OWNER}',
    occurrence.generation_id = $generation_id
"""


async def upsert_import_occurrences(
    client: Neo4jClient,
    ledger: Iterable[dict],
) -> int:
    """Write the complete canonical entry ledger for the staging generation."""
    normalized = normalize_import_resolution_ledger(list(ledger))
    if not normalized:
        return 0
    items = [import_resolution_storage_item(row) for row in normalized]

    async def _write(transaction) -> int:
        result = await transaction.run(
            _UPSERT_OCCURRENCES,
            items=items,
            generation_id=client.generation_id,
        )
        await result.consume()
        return len(items)

    return await client.execute_write(_write)
