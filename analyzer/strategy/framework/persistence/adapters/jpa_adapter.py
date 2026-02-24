"""JPA adapter - Extract Table/Column from JPA Entity annotations in Java AST nodes."""

from __future__ import annotations

import re
from typing import Any

from analyzer.strategy.framework.persistence.adapters.base import BasePersistenceAdapter
from analyzer.strategy.framework.persistence.models import (
    ColumnSpec,
    PersistenceResult,
    TableSpec,
)

_TABLE_NAME_RE = re.compile(r'name\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)
_TABLE_SCHEMA_RE = re.compile(r'schema\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)
_TABLE_BLOCK_RE = re.compile(r'@Table\s*\([^)]*\)', re.IGNORECASE)
_ENTITY_RE = re.compile(r'@Entity\b', re.IGNORECASE)
_COLUMN_NAME_RE = re.compile(r'@Column\s*\(\s*name\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)


class JpaAdapter(BasePersistenceAdapter):
    """JPA Entity 클래스의 @Entity, @Table, @Column 어노테이션에서 테이블/컬럼 추출."""

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

        for node in ast_context or []:
            if getattr(node, "node_type", None) not in ("CLASS", "INTERFACE"):
                continue
            ann = getattr(node, "annotations", None) or ""
            if not _ENTITY_RE.search(ann):
                continue

            class_name = getattr(node, "unit_name", None) or getattr(node, "name", None) or ""
            schema = "public"
            table_name = class_name

            table_block = _TABLE_BLOCK_RE.search(ann)
            if table_block:
                block = table_block.group(0)
                m = _TABLE_SCHEMA_RE.search(block)
                if m:
                    schema = m.group(1)
                m = _TABLE_NAME_RE.search(block)
                if m:
                    table_name = m.group(1)

            key = (db, schema, table_name)
            if key not in seen_tables:
                seen_tables.add(key)
                tables.append(TableSpec(
                    db=db,
                    schema=schema,
                    name=table_name,
                    description_source="jpa",
                ))

            for child in getattr(node, "children", []) or []:
                if getattr(child, "node_type", None) != "FIELD":
                    continue
                field_name = getattr(child, "name", None) or ""
                col_ann = getattr(child, "annotations", None) or ""
                col_name = field_name
                m = _COLUMN_NAME_RE.search(col_ann)
                if m:
                    col_name = m.group(1)
                if not col_name:
                    continue
                fqn = ".".join(filter(None, [schema, table_name, col_name])).lower()
                if fqn not in seen_columns:
                    seen_columns.add(fqn)
                    columns.append(ColumnSpec(
                        table_db=db,
                        table_schema=schema,
                        table_name=table_name,
                        name=col_name,
                        fqn=fqn,
                        description_source="jpa",
                    ))

        return PersistenceResult(tables=tables, columns=columns)
