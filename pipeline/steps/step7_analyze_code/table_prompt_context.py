"""Structured serialization of direct table evidence for code prompts."""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping


def dump_linked_table_descriptions(
    table_ids: Iterable[str],
    contexts_by_table: Mapping[str, dict],
) -> str:
    """Serialize only schema context that the Table meaning call cannot derive.

    Access, operation and used columns already live in the structural profile.
    Complete DDL columns and sample rows do not determine this call's table-use
    meaning, so repeating them would add unrelated model input.
    """
    return json.dumps(
        {
            table_id: str(
                contexts_by_table.get(table_id, {}).get("description") or ""
            )
            for table_id in (str(value) for value in table_ids)
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
