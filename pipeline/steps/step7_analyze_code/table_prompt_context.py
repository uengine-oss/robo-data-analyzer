"""Structured serialization of direct table evidence for code prompts."""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping


def dump_table_refs(
    table_ids: Iterable[str],
    contexts_by_table: Mapping[str, dict],
    *,
    include_columns: bool = True,
) -> str:
    """Preserve exact table IDs and evidence in separate JSON fields.

    DDL owns the complete column list, but the current table R/W linker does not
    produce code-node-to-column relations.  Callers that describe direct code
    use therefore set ``include_columns=False`` instead of inferring ownership
    from language syntax or similar names.
    """
    records = []
    for table_id in (str(value) for value in table_ids):
        context = contexts_by_table.get(table_id, {})
        records.append({
            "table_id": table_id,
            "description": str(context.get("description") or ""),
            "columns": (
                [str(name) for name in context.get("columns") or ()]
                if include_columns else []
            ),
            "sample_rows": list(context.get("sample_rows") or ()),
        })
    return json.dumps(records, ensure_ascii=False, separators=(",", ":"))
