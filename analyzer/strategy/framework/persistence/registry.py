"""Persistence adapter registry - Run all adapters and merge results."""

from __future__ import annotations

from typing import Any

from analyzer.strategy.framework.persistence.adapters import JpaAdapter, SqlMapAdapter
from analyzer.strategy.framework.persistence.models import (
    ColumnSpec,
    PersistenceRelation,
    PersistenceResult,
    TableSpec,
)

_DEFAULT_ADAPTERS = [SqlMapAdapter(), JpaAdapter()]


def run_all(
    chunks: list[Any],
    ast_context: list[Any],
    db: str = "",
    adapters: list[Any] | None = None,
) -> PersistenceResult:
    """모든 어댑터를 실행하고 결과를 병합.

    MERGE 키: (db, schema, name) for tables, fqn for columns.
    동일 키는 첫 발견값 유지 (덮어쓰지 않음).
    """
    adapters = adapters or _DEFAULT_ADAPTERS
    tables: dict[tuple[str, str, str], TableSpec] = {}
    columns: dict[str, ColumnSpec] = {}
    relations: list[PersistenceRelation] = []

    for adapter in adapters:
        try:
            res = adapter.extract(chunks, ast_context, db)
        except Exception:
            continue
        for t in res.tables:
            key = (t.db, t.schema, t.name)
            if key not in tables:
                tables[key] = t
        for c in res.columns:
            fqn = c.fqn or ".".join(
                filter(None, [c.table_schema, c.table_name, c.name])
            ).lower()
            if fqn not in columns:
                columns[fqn] = c
        relations.extend(res.relations)

    return PersistenceResult(
        tables=list(tables.values()),
        columns=list(columns.values()),
        relations=relations,
    )
