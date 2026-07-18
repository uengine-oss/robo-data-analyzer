"""Stable response-row construction for retrieval results."""
from __future__ import annotations


def table_row(table: dict, anchor_id: str, anchor_relevance: float) -> dict:
    return {
        "id": table["id"],
        "element_id": table.get("eid"),
        "name": table.get("logical_name") or table.get("name") or "Unknown",
        "physical_name": table.get("name") or "",
        "label": "TABLE",
        "stereotype": "",
        "summary": (table.get("summary") or "").replace("\n", " "),
        "search_text": "",
        "writes": [],
        "reads": [],
        "calls": [],
        "rules": [],
        "relevance": round(anchor_relevance, 3),
        "role": "evidence",
        "evidence": [{
            "anchor_id": anchor_id,
            "relation": table.get("relation") or "",
            "direction": "out",
        }],
    }
