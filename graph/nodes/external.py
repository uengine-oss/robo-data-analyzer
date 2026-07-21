"""Closed-label persistence for external-file graph nodes."""
from __future__ import annotations

from dataclasses import asdict
from typing import Iterable

from graph.neo4j_client import Neo4jClient
from graph.node_upsert import nonempty, upsert_by_node_label, upsert_fixed_label
from graph.schema.graph_nodes import CandidateLink, DocumentNode, ExternalChunk, ProjectFile, UnresolvedRef


EXTERNAL_NODE_LABELS = frozenset({
    "PROJECT_FILE", "SQL_STATEMENT", "CONFIG_SECTION", "ARTIFACT_CHUNK", "CHUNK",
    "DOCUMENT", "DOC_SECTION", "DOC_CHUNK", "UNRESOLVED_REF", "CANDIDATE_LINK",
})
_FIXED_TYPES = (ProjectFile, UnresolvedRef, CandidateLink)
_DYNAMIC_TYPES = (ExternalChunk, DocumentNode)


class ExternalNodeContractError(ValueError):
    pass


def _to_item(node: object) -> dict:
    item = asdict(node)
    item.pop("label", None)
    return nonempty(item)


async def upsert_external_nodes(client: Neo4jClient, nodes: Iterable[object]) -> None:
    """Persist external nodes using only the closed labels declared by spec 075."""
    fixed: dict[str, list[dict]] = {}
    dynamic: list[ExternalChunk | DocumentNode] = []
    for node in nodes:
        if not isinstance(node, _FIXED_TYPES + _DYNAMIC_TYPES):
            raise ExternalNodeContractError(f"unsupported external node type: {type(node).__name__}")
        if node.label not in EXTERNAL_NODE_LABELS:
            raise ExternalNodeContractError(f"unsupported external node label: {node.label!r}")
        if isinstance(node, _FIXED_TYPES):
            fixed.setdefault(node.label, []).append(_to_item(node))
        else:
            dynamic.append(node)
    for label, items in fixed.items():
        await upsert_fixed_label(client, label, items)
    if dynamic:
        await upsert_by_node_label(client, dynamic, _to_item)
