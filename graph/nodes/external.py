"""Closed-label persistence for external-file graph nodes (spec 078)."""
from __future__ import annotations

from dataclasses import asdict
from typing import Iterable

from graph.neo4j_client import Neo4jClient
from graph.node_upsert import nonempty, upsert_fixed_label
from graph.schema.graph_nodes import ExternalChunk, ProjectFile


EXTERNAL_NODE_LABELS = frozenset({"PROJECT_FILE", "EXTERNAL_CHUNK"})
_ALLOWED_TYPES = (ProjectFile, ExternalChunk)


class ExternalNodeContractError(ValueError):
    pass


def _to_item(node: object) -> dict:
    item = asdict(node)
    item.pop("label", None)
    item = {key: list(value) if isinstance(value, tuple) else value for key, value in item.items()}
    return nonempty(item)


async def upsert_external_nodes(client: Neo4jClient, nodes: Iterable[object]) -> None:
    """Persist external nodes using only the closed labels declared by spec 078."""
    grouped: dict[str, list[dict]] = {}
    for node in nodes:
        if not isinstance(node, _ALLOWED_TYPES):
            raise ExternalNodeContractError(f"unsupported external node type: {type(node).__name__}")
        if node.label not in EXTERNAL_NODE_LABELS:
            raise ExternalNodeContractError(f"unsupported external node label: {node.label!r}")
        grouped.setdefault(node.label, []).append(_to_item(node))
    for label, items in grouped.items():
        await upsert_fixed_label(client, label, items)
