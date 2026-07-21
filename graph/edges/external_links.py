"""Fail-closed persistence for typed external-file relationships."""
from __future__ import annotations

from dataclasses import asdict
from typing import Iterable

from graph.neo4j_client import Neo4jClient
from graph.ownership import ANALYZER_GRAPH_OWNER
from graph.schema import rel_types as rt
from graph.schema.graph_edges import CallsSql, ExternalEdge, HasChunk, HasPart, HasSection, Mentions, ReferTo, UsesConfig


_EDGE_SPECS: dict[type, tuple[str, tuple[str, ...]]] = {
    CallsSql: (rt.CALLS_SQL, ("evidence_file", "evidence_line", "via_const")),
    UsesConfig: (rt.USES_CONFIG, ("evidence_file", "evidence_line", "via_const")),
    ReferTo: (rt.REFER_TO, ("evidence_file", "evidence_line", "matched_fqn")),
    Mentions: (rt.MENTIONS, ("matched_text", "evidence_file", "evidence_line")),
    HasChunk: (rt.HAS_CHUNK, ()),
    HasPart: (rt.HAS_PART, ("part_index",)),
    HasSection: (rt.HAS_SECTION, ("order",)),
}

_ENDPOINT_QUERY = f"""
UNWIND $ids AS id
MATCH (node {{id: id, graph_owner: '{ANALYZER_GRAPH_OWNER}'}})
RETURN node.id AS id
"""


class ExternalGraphContractError(ValueError):
    pass


def _merge_query(rel_type: str, properties: tuple[str, ...]) -> str:
    assignments = ",\n    ".join(f"r.{name} = item.{name}" for name in properties)
    set_clause = f"\nSET {assignments}" if assignments else ""
    return f"""
UNWIND $items AS item
MATCH (source {{id: item.source_id, graph_owner: '{ANALYZER_GRAPH_OWNER}'}})
MATCH (target {{id: item.target_id, graph_owner: '{ANALYZER_GRAPH_OWNER}'}})
MERGE (source)-[r:{rel_type}]->(target){set_clause}
"""


async def _require_owned_endpoints(client: Neo4jClient, endpoint_ids: set[str]) -> None:
    if any(not endpoint_id for endpoint_id in endpoint_ids):
        raise ExternalGraphContractError("external relationship has a blank endpoint")
    rows = await client.run_read_query(_ENDPOINT_QUERY, {"ids": sorted(endpoint_ids)})
    matched = {str(row["id"]) for row in rows}
    missing = sorted(endpoint_ids - matched)
    if missing:
        raise ExternalGraphContractError(f"missing graph endpoints: {missing}")


async def upsert_external_edges(client: Neo4jClient, edges: Iterable[ExternalEdge]) -> None:
    """Validate endpoint existence, then idempotently merge each closed relationship type."""
    grouped: dict[type, list[dict]] = {}
    endpoints: set[str] = set()
    for edge in edges:
        spec = _EDGE_SPECS.get(type(edge))
        if spec is None:
            raise ExternalGraphContractError(f"unsupported external edge type: {type(edge).__name__}")
        item = asdict(edge)
        source_id = item.get("source_id", "")
        target_id = item.get("target_id", "")
        if not source_id or not target_id:
            raise ExternalGraphContractError("external relationship has a blank endpoint")
        endpoints.update((source_id, target_id))
        grouped.setdefault(type(edge), []).append(item)
    if not grouped:
        return
    await _require_owned_endpoints(client, endpoints)
    for edge_type, items in grouped.items():
        rel_type, properties = _EDGE_SPECS[edge_type]
        await client.run_batch_unwind(_merge_query(rel_type, properties), items)
