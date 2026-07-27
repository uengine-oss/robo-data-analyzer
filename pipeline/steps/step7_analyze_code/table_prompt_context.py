"""R/W 테이블 프롬프트 입력의 구조화 직렬화."""
from __future__ import annotations

import json
import re
from typing import Iterable

from pipeline.quality_gates.lexing import split_source


def direct_column_names(
    column_names: Iterable[str],
    source_text: str,
    lang: str,
) -> list[str]:
    """Keep DDL columns that occur as identifiers in active target source."""
    live = split_source(source_text, lang).live()
    result: list[str] = []
    for value in column_names:
        name = str(value)
        if not name:
            continue
        pattern = re.compile(
            rf"(?<![A-Za-z0-9_$#]){re.escape(name)}(?![A-Za-z0-9_$#])",
            re.IGNORECASE,
        )
        if pattern.search(live):
            result.append(name)
    return result


def dump_table_refs(
    table_ids: Iterable[str],
    contexts_by_table: dict[str, dict],
    *,
    direct_source: str | None = None,
    lang: str | None = None,
) -> str:
    """정확한 table_id와 설명/컬럼/샘플을 서로 다른 JSON 필드로 보존한다."""
    records = []
    for table_id in table_ids:
        exact_id = str(table_id)
        context = contexts_by_table.get(exact_id, {})
        columns = [str(name) for name in context.get("columns") or ()]
        if direct_source is not None:
            if not lang:
                raise ValueError("lang is required when direct_source is supplied")
            columns = direct_column_names(columns, direct_source, lang)
        records.append({
            "table_id": exact_id,
            "description": str(context.get("description") or ""),
            "columns": columns,
            "sample_rows": list(context.get("sample_rows") or ()),
        })
    return json.dumps(records, ensure_ascii=False, separators=(",", ":"))
