"""Collision-safe filenames for run audit artifacts."""
from __future__ import annotations

import hashlib


def audit_file_stem(node_id: str) -> str:
    """Keep a readable prefix while making the full node identity collision-safe."""
    if not node_id:
        raise ValueError("audit artifact node_id must be non-empty")
    readable = "".join(
        character if character.isalnum() or character in "._-" else "_"
        for character in node_id
    )[:80]
    digest = hashlib.sha256(node_id.encode("utf-8")).hexdigest()[:16]
    return f"{readable}-{digest}"
