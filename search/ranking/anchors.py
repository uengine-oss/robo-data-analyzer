"""Anchor-cluster merge policy."""
from __future__ import annotations


def merge_clusters(first: list[dict], second: list[dict]) -> list[dict]:
    """Merge clusters by ID while preserving core role and strongest relevance."""
    merged: dict[str, dict] = {}
    for candidate in first + second:
        current = merged.get(candidate["id"])
        if current is None:
            merged[candidate["id"]] = candidate
            continue
        calls = list(dict.fromkeys(current.get("calls", []) + candidate.get("calls", [])))
        if (
            candidate["role"] == "core" and current["role"] != "core"
        ) or (
            candidate["role"] == current["role"]
            and candidate["relevance"] > current["relevance"]
        ):
            merged[candidate["id"]] = candidate
        merged[candidate["id"]]["calls"] = calls
    return sorted(
        merged.values(),
        key=lambda item: (item["role"] != "core", -item["relevance"]),
    )
