"""Project structurally owned column coordinates for the table-insight concern.

This module never writes natural-language meaning.  It preserves parser-qualified columns and,
for a parser-confirmed Oracle leaf node, exact DDL identifiers tokenized from the node's own SQL
source.  Ambiguous dialect, multi-table, overlapping-child, and same-range cases fail closed
instead of assigning a column by name similarity.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from typing import Any

from sqlglot import Tokenizer, TokenType

from graph.schema.graph_nodes import CodeNode
from pipeline.models.analysis_context import AnalysisContext
from pipeline.models.data_object_usage import exact_direct_table_ids
from shared.files.cache import SourceFileCache


_IDENTIFIER_TOKENS = frozenset({TokenType.VAR, TokenType.IDENTIFIER})


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
                actual_name = allowed.get(_canonical(getattr(column, "name", "")))
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
        allow_lexical_projection = (
            strategy_name == "dbms"
            and context.extra.get("source_language") == "oracle"
            and bool(tables)
            and node_id not in blocked_by_descendant
            and coordinate_counts[coordinate] == 1
        )
        if allow_lexical_projection:
            allowed_by_table = {
                table_id: _allowed_columns(ddl_columns.get(table_id) or ())
                for table_id in tables
            }
            for name, line in _sql_identifiers(context, source_cache):
                canonical = _canonical(name)
                owners = [
                    (table_id, allowed[canonical])
                    for table_id, allowed in allowed_by_table.items()
                    if canonical in allowed
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
        excluded = [
            (contexts[child_id].start_line, contexts[child_id].end_line)
            for child_id in owned_children
        ]
        owner_by_column: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
        parent_ddl = context.extra.get("ddl_columns_by_table") or {}
        for table_id in direct_tables.get(parent_id, ()):
            for column_name in _allowed_columns(parent_ddl.get(table_id) or ()).values():
                owner_by_column[_canonical(column_name)].append(
                    (parent_id, table_id, column_name)
                )
        for child_id in owned_children:
            for table_id, columns in raw_result[child_id].items():
                for column_name in columns:
                    owner_by_column[_canonical(column_name)].append(
                        (child_id, table_id, column_name)
                    )
        for name, line in _sql_identifiers(context, source_cache, excluded=excluded):
            owners = owner_by_column.get(_canonical(name), ())
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


def _allowed_columns(columns: object) -> dict[str, str]:
    return {
        _canonical(name): str(name)
        for name in columns if str(name).strip()
    }


def _canonical(value: object) -> str:
    return str(value or "").strip().casefold()


def _sql_identifiers(
    context: AnalysisContext,
    source_cache: SourceFileCache,
    *,
    excluded: list[tuple[int, int]] | None = None,
) -> tuple[tuple[str, int], ...]:
    source = source_cache.slice(context.file_path, context.start_line, context.end_line)
    if not source.strip():
        return ()
    try:
        tokens = Tokenizer().tokenize(source)
    except Exception:
        return ()
    excluded = excluded or []
    alias_indexes = _select_output_alias_indexes(tokens)
    return tuple(
        (token.text, context.start_line + token.line - 1)
        for index, token in enumerate(tokens)
        if token.token_type in _IDENTIFIER_TOKENS
        # A SELECT output alias is a new result name, not a read of a same-named
        # physical column.  DDL-unique fallback must not turn ``COUNT(*) AS CNT``
        # or ``1 AS CNT`` into table.CNT evidence.
        and index not in alias_indexes
        and context.start_line <= context.start_line + token.line - 1 <= context.end_line
        and not any(
            start <= context.start_line + token.line - 1 <= end
            for start, end in excluded
        )
    )


def _select_output_alias_indexes(tokens: list[Any]) -> set[int]:
    """Return explicit and Oracle implicit SELECT-output alias token positions.

    Oracle permits ``expression alias`` without ``AS``.  These names define the
    derived result row and must never be attributed to a same-named DDL column.
    The scan is lexical only: it marks the last identifier of each top-level
    SELECT-list item when the preceding token already completes an expression.
    """
    aliases = {
        index for index, token in enumerate(tokens)
        if token.token_type in _IDENTIFIER_TOKENS
        and index > 0
        and tokens[index - 1].token_type == TokenType.ALIAS
    }
    expression_end_types = {
        TokenType.NUMBER,
        TokenType.STRING,
        TokenType.R_PAREN,
        TokenType.VAR,
        TokenType.IDENTIFIER,
        TokenType.END,
    }
    for select_index, token in enumerate(tokens):
        if token.token_type != TokenType.SELECT:
            continue
        depth = 0
        item_start = select_index + 1
        index = item_start
        while index < len(tokens):
            current = tokens[index]
            if current.token_type == TokenType.L_PAREN:
                depth += 1
            elif current.token_type == TokenType.R_PAREN:
                if depth == 0:
                    break
                depth -= 1
            boundary = depth == 0 and current.token_type in {
                TokenType.COMMA, TokenType.FROM,
            }
            if boundary:
                segment = list(range(item_start, index))
                if len(segment) >= 2:
                    alias_index = segment[-1]
                    previous_index = segment[-2]
                    if (
                        tokens[alias_index].token_type in _IDENTIFIER_TOKENS
                        and tokens[previous_index].token_type != TokenType.DOT
                        and tokens[previous_index].token_type in expression_end_types
                    ):
                        aliases.add(alias_index)
                if current.token_type == TokenType.FROM:
                    break
                item_start = index + 1
            index += 1
    return aliases


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
