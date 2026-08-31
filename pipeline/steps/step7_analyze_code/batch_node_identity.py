"""Compact request-local keys for DBMS batch concerns."""
from __future__ import annotations

from collections.abc import Iterable


def batch_node_keys(
    node_ids: Iterable[str],
) -> dict[str, str]:
    """Map sequential wire keys to full node IDs without exposing their identity."""
    ids = tuple(str(node_id) for node_id in node_ids)
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("DBMS batch node ids must be non-empty and unique")
    return {
        f"N{index}": node_id
        for index, node_id in enumerate(ids, start=1)
    }
