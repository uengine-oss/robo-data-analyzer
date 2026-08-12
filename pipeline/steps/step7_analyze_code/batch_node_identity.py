"""Collision-safe request-local keys for DBMS batch concerns."""
from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Iterable, Mapping

from pipeline.models.analysis_context import AnalysisContext


def batch_node_keys(
    node_ids: Iterable[str],
    contexts: Mapping[str, AnalysisContext],
) -> dict[str, str]:
    """Map compact wire keys to full node IDs without treating coordinates as identity."""
    ids = tuple(str(node_id) for node_id in node_ids)
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("DBMS batch node ids must be non-empty and unique")
    ranges = {
        node_id: f"{contexts[node_id].start_line}-{contexts[node_id].end_line}"
        for node_id in ids
    }
    counts = Counter(ranges.values())
    result: dict[str, str] = {}
    for node_id in ids:
        coordinate = ranges[node_id]
        key = coordinate
        if counts[coordinate] > 1:
            digest = hashlib.sha256(node_id.encode("utf-8")).hexdigest()[:16]
            key = f"{coordinate}#{digest}"
        if key in result:
            raise ValueError(f"DBMS request-local node key collision: {key!r}")
        result[key] = node_id
    return result
