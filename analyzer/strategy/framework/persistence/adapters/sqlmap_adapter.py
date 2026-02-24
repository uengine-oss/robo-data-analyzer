"""SqlMap adapter - Extract Table/Column from iBATIS/MyBatis SQL mapper chunks."""

from __future__ import annotations

from typing import Any

from util.sql_analyzer import extract_tables_from_sql, extract_columns_from_sql

from analyzer.strategy.framework.persistence.adapters.base import BasePersistenceAdapter
from analyzer.strategy.framework.persistence.models import (
    ColumnSpec,
    PersistenceResult,
    TableSpec,
)


class SqlMapAdapter(BasePersistenceAdapter):
    """iBATIS/MyBatis XML mapper SQL에서 테이블/컬럼 추출."""

    def extract(
        self,
        chunks: list[Any],
        ast_context: list[Any],
        db: str = "",
    ) -> PersistenceResult:
        tables: list[TableSpec] = []
        columns: list[ColumnSpec] = []
        seen_tables: set[tuple[str, str, str]] = set()
        seen_columns: set[str] = set()

        for chunk in chunks:
            if getattr(chunk, "metadata", None) is None:
                continue
            if getattr(chunk.metadata, "chunk_type", "") != "sql_statement":
                continue

            content = getattr(chunk, "content", "") or ""
            props = getattr(chunk.metadata, "properties", {}) or {}
            sql_type = props.get("type", "select")

            table_list = extract_tables_from_sql(content)
            for t in table_list:
                schema = t.get("schema", "") or "public"
                table_name = t.get("table", "")
                if not table_name:
                    continue
                key = (db, schema, table_name)
                if key not in seen_tables:
                    seen_tables.add(key)
                    tables.append(TableSpec(
                        db=db,
                        schema=schema,
                        name=table_name,
                        description_source="sqlmap",
                    ))

            if not table_list:
                continue
            t0 = table_list[0]
            schema = t0.get("schema", "") or "public"
            table_name = t0.get("table", "")
            if not table_name:
                continue
            for col in extract_columns_from_sql(content, sql_type):
                if not col:
                    continue
                fqn = ".".join(filter(None, [schema, table_name, col])).lower()
                if fqn not in seen_columns:
                    seen_columns.add(fqn)
                    columns.append(ColumnSpec(
                        table_db=db,
                        table_schema=schema,
                        table_name=table_name,
                        name=col,
                        fqn=fqn,
                        description_source="sqlmap",
                    ))

        return PersistenceResult(tables=tables, columns=columns)
