"""Project structurally owned column coordinates for the table-insight concern.

This module never writes natural-language meaning. It preserves parser-qualified columns and
projects grammar-owned unqualified Oracle identifiers only when the DDL/query scope proves one
physical owner. Ambiguous dialect, multi-table, overlapping-child, and same-range cases fail
closed instead of assigning a column by name similarity.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from typing import Any

from graph.schema.graph_nodes import CodeNode
from pipeline.models.analysis_context import AnalysisContext
from pipeline.models.data_object_usage import exact_direct_table_ids
from shared.files.cache import SourceFileCache


def build_column_usage_projections(
    contexts: Mapping[str, AnalysisContext],
    code_by_id: Mapping[str, CodeNode],
    *,
    strategy_name: str,
    source_cache: SourceFileCache,
) -> dict[str, dict[str, dict[str, dict[str, Any]]]]:
    """Return ``node -> table -> column -> immutable coordinate evidence``."""
    direct_tables = {
        node_id: tuple(dict.fromkeys((
            *exact_direct_table_ids(context, "READ"),
            *exact_direct_table_ids(context, "WRITE"),
        )))
        for node_id, context in contexts.items()
    }
    blocked_by_descendant: set[str] = set()
    for node_id, tables in direct_tables.items():
        if not tables:
            continue
        current = code_by_id.get(node_id)
        parent_id = current.parent_id if current is not None else None
        while parent_id:
            blocked_by_descendant.add(parent_id)
            parent = code_by_id.get(parent_id)
            parent_id = parent.parent_id if parent is not None else None

    coordinate_counts: dict[tuple[str, int, int], int] = defaultdict(int)
    for context in contexts.values():
        coordinate_counts[(context.file_path, context.start_line, context.end_line)] += 1

    raw_result: dict[str, dict[str, dict[str, dict[str, Any]]]] = {}
    for node_id, context in contexts.items():
        tables = direct_tables[node_id]
        projected: dict[str, dict[str, dict[str, Any]]] = {
            table_id: {} for table_id in tables
        }
        ddl_columns = context.extra.get("ddl_columns_by_table") or {}

        for usage in context.extra.get("data_object_usages") or ():
            table_id = str(getattr(usage, "table_id", "") or "")
            if (
                table_id not in projected
                or getattr(usage, "resolution", "") != "exact"
            ):
                continue
            allowed = _allowed_columns(ddl_columns.get(table_id) or ())
            for column in getattr(usage, "qualified_columns", ()) or ():
                actual_name = allowed.get(_identifier_key(
                    getattr(column, "name", ""),
                    bool(getattr(column, "name_quoted", False)),
                ))
                line = int(getattr(column, "evidence_line", 0) or 0)
                if actual_name and context.start_line <= line <= context.end_line:
                    _merge_candidate(
                        projected[table_id],
                        actual_name,
                        line,
                        "qualified",
                        context.file_path,
                        source_cache,
                        source_id=node_id,
                    )

        coordinate = (context.file_path, context.start_line, context.end_line)
        allow_unqualified_projection = (
            strategy_name == "dbms"
            and context.extra.get("source_language") == "oracle"
            and bool(tables)
            and node_id not in blocked_by_descendant
            and coordinate_counts[coordinate] == 1
        )
        if allow_unqualified_projection:
            allowed_by_table = {
                table_id: _allowed_columns(ddl_columns.get(table_id) or ())
                for table_id in tables
            }
            for name, name_quoted, line in _parser_unqualified_identifiers(context):
                identity = _identifier_key(name, name_quoted)
                owners = [
                    (table_id, allowed[identity])
                    for table_id, allowed in allowed_by_table.items()
                    if identity in allowed
                ]
                if len(owners) == 1:
                    table_id, actual_name = owners[0]
                    _merge_candidate(
                        projected[table_id],
                        actual_name,
                        line,
                        (
                            "sole_direct_table"
                            if len(tables) == 1
                            else "ddl_unique_in_scope"
                        ),
                        context.file_path,
                        source_cache,
                        source_id=node_id,
                    )

        raw_result[node_id] = projected

    _project_derived_output_uses(
        raw_result,
        contexts,
        code_by_id,
        direct_tables,
        coordinate_counts,
        source_cache,
    )

    return {
        node_id: {
            table_id: {
                column_name: _finalize_candidate(candidate)
                for column_name, candidate in columns.items()
            }
            for table_id, columns in projected.items()
        }
        for node_id, projected in raw_result.items()
    }


def _project_derived_output_uses(
    raw_result: dict[str, dict[str, dict[str, dict[str, Any]]]],
    contexts: Mapping[str, AnalysisContext],
    code_by_id: Mapping[str, CodeNode],
    direct_tables: Mapping[str, tuple[str, ...]],
    coordinate_counts: Mapping[tuple[str, int, int], int],
    source_cache: SourceFileCache,
) -> None:
    """Attach an outer SELECT's exact use to one uniquely projected child column.

    Oracle derived-table output is a structural def-use edge.  A child SELECT proves which
    physical column it projected even when the parent also reads another physical table.  We
    link only an exact identifier that resolves to one parent/child node-table-column owner;
    aliases, duplicate owners and same-range nodes fail closed.  No natural-language meaning
    is created.
    """
    descendants: dict[str, list[str]] = defaultdict(list)
    for child_id, child in code_by_id.items():
        parent_id = child.parent_id
        while parent_id:
            descendants[parent_id].append(child_id)
            parent = code_by_id.get(parent_id)
            parent_id = parent.parent_id if parent is not None else None

    for parent_id, child_ids in descendants.items():
        parent = code_by_id.get(parent_id)
        context = contexts.get(parent_id)
        if (
            parent is None
            or context is None
            or parent.label != "SELECT"
            or context.extra.get("source_language") != "oracle"
            or coordinate_counts[(context.file_path, context.start_line, context.end_line)] != 1
        ):
            continue
        owned_children = [
            child_id for child_id in child_ids
            if code_by_id.get(child_id) is not None
            and code_by_id[child_id].file_path == parent.file_path
            and any(raw_result.get(child_id, {}).values())
        ]
        if not owned_children:
            continue
        owner_by_column: dict[tuple[str, str], list[tuple[str, str, str]]] = defaultdict(list)
        parent_ddl = context.extra.get("ddl_columns_by_table") or {}
        for table_id in direct_tables.get(parent_id, ()):
            for identity, column_name in _allowed_columns(
                parent_ddl.get(table_id) or (),
            ).items():
                owner_by_column[identity].append(
                    (parent_id, table_id, column_name)
                )
        for child_id in owned_children:
            for table_id, columns in raw_result[child_id].items():
                child_ddl = contexts[child_id].extra.get("ddl_columns_by_table") or {}
                for identity, column_name in _allowed_columns(
                    child_ddl.get(table_id) or (),
                ).items():
                    if column_name in columns:
                        owner_by_column[identity].append(
                            (child_id, table_id, column_name)
                        )
        for name, name_quoted, line in _parser_unqualified_identifiers(context):
            owners = owner_by_column.get(_identifier_key(name, name_quoted), ())
            if len(owners) != 1:
                continue
            child_id, table_id, column_name = owners[0]
            ownership = (
                "ddl_unique_in_scope"
                if child_id == parent_id else "derived_output_unique"
            )
            _merge_candidate(
                raw_result[child_id][table_id],
                column_name,
                line,
                ownership,
                context.file_path,
                source_cache,
                source_id=parent_id,
            )


def _allowed_columns(columns: object) -> dict[tuple[str, str], str]:
    result: dict[tuple[str, str], str] = {}
    for column in columns:
        name = str(getattr(column, "name", "") or "").strip()
        if not name:
            raise ValueError(f"DDL column identity is missing name: {column!r}")
        identity = _identifier_key(name, bool(getattr(column, "name_quoted", False)))
        if identity in result:
            raise ValueError(
                f"duplicate canonical DDL column identity: {identity!r}"
            )
        result[identity] = name
    return result


def _identifier_key(value: object, quoted: bool) -> tuple[str, str]:
    text = str(value or "").strip()
    if quoted:
        if len(text) >= 2 and text.startswith('"') and text.endswith('"'):
            text = text[1:-1].replace('""', '"')
        return "Q", text
    return "U", text.upper()


def _parser_unqualified_identifiers(
    context: AnalysisContext,
) -> tuple[tuple[str, bool, int], ...]:
    """Return evidence-v2 syntax or reject an incomplete Oracle AST.

    An absent key means the Parser did not account for unqualified SQL identifiers. Empty is a
    valid, explicit v2 result. Treating both states as ``()`` would recreate C-028.
    """
    if "unqualified_identifier_references" not in context.extra:
        raise ValueError(
            "Oracle column projection requires parser data-object evidence v2: "
            f"{context.id} at {context.file_path}:{context.start_line}-{context.end_line}"
        )
    result: list[tuple[str, bool, int]] = []
    for reference in context.extra["unqualified_identifier_references"] or ():
        name = str(getattr(reference, "name", "") or "").strip()
        line = int(getattr(reference, "evidence_line", 0) or 0)
        if not name or not context.start_line <= line <= context.end_line:
            raise ValueError(
                "invalid parser unqualified identifier evidence: "
                f"{context.id} name={name!r} line={line}"
            )
        result.append((name, bool(getattr(reference, "name_quoted", False)), line))
    return tuple(result)


def _merge_candidate(
    columns: dict[str, dict[str, Any]],
    name: str,
    line: int,
    ownership: str,
    file_path: str,
    source_cache: SourceFileCache,
    *,
    source_id: str,
) -> None:
    candidate = columns.setdefault(name, {
        "ownership": ownership,
        "evidence_lines": set(),
        "source_lines": {},
    })
    if ownership == "qualified":
        candidate["ownership"] = "qualified"
    candidate["evidence_lines"].add(line)
    lines = source_cache.lines(file_path)
    if 0 < line <= len(lines):
        text = lines[line - 1].strip()
        if text:
            candidate["source_lines"][line] = {
                "text": text,
                "source_id": source_id,
                "ownership": ownership,
            }


def _finalize_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    lines = sorted(int(line) for line in candidate["evidence_lines"])
    source = candidate["source_lines"]
    return {
        "ownership": candidate["ownership"],
        "evidence_lines": lines,
        "source_lines": [
            {
                "line": line,
                "text": source[line]["text"],
                "source_id": source[line]["source_id"],
                "ownership": source[line]["ownership"],
            }
            for line in lines if line in source
        ],
    }
