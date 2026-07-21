"""R/W 테이블 프롬프트 입력의 구조화 직렬화."""
from __future__ import annotations

import json
from typing import Iterable


def dump_table_refs(
    table_ids: Iterable[str],
    contexts_by_table: dict[str, dict],
) -> str:
    """정확한 table_id와 설명/컬럼/샘플을 서로 다른 JSON 필드로 보존한다."""
    records = []
    for table_id in table_ids:
        exact_id = str(table_id)
        context = contexts_by_table.get(exact_id, {})
        records.append({
            "table_id": exact_id,
            "description": str(context.get("description") or ""),
            "columns": [str(name) for name in context.get("columns") or ()],
            "sample_rows": list(context.get("sample_rows") or ()),
        })
    return json.dumps(records, ensure_ascii=False, separators=(",", ":"))
