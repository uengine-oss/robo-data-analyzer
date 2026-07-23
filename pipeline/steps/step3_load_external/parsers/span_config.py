"""Config-family span adapters (spec 078): YAML, JSON/JSONL, properties, INI, CSV.

All adapters return exact spans over the decoded source (never re-serialized
values) and fail closed on malformed or ambiguous input (duplicate keys).
"""
from __future__ import annotations

import json

import yaml
from json_source_map import calculate as json_source_positions
from json_source_map.errors import BaseError as JsonSourceMapError

from pipeline.steps.step3_load_external.parsers.span_common import (
    AdapterParseError,
    SemanticSpan,
    extend_through_eol,
    is_reference_key,
    line_offsets,
    sorted_unique_keys,
)


# ── YAML ────────────────────────────────────────────────────────────

def _yaml_paths(node: yaml.Node, prefix: str, output: list[str]) -> None:
    if prefix:
        output.append(prefix)
    if isinstance(node, yaml.MappingNode):
        for key_node, value_node in node.value:
            key = str(getattr(key_node, "value", ""))
            if key == "<<":                  # merge indicator, not a reference path
                continue
            child = f"{prefix}.{key}" if prefix else key
            _yaml_paths(value_node, child, output)


def parse_yaml_spans(source: str) -> list[SemanticSpan]:
    """One span per top-level mapping entry, per document."""
    try:
        documents = list(yaml.compose_all(source))
    except yaml.YAMLError as error:
        raise AdapterParseError(f"invalid YAML: {type(error).__name__}") from error
    spans: list[SemanticSpan] = []
    for document in documents:
        if not isinstance(document, yaml.MappingNode):
            continue
        seen_top: set[str] = set()
        for key_node, value_node in document.value:
            top = str(getattr(key_node, "value", ""))
            if top in seen_top:
                raise AdapterParseError(f"duplicate YAML top-level key: {top!r}")
            seen_top.add(top)
            start = key_node.start_mark.index
            end = extend_through_eol(source, value_node.end_mark.index)
            paths: list[str] = []
            _yaml_paths(value_node, top, paths)
            spans.append(SemanticSpan(
                char_start=start,
                char_end=end,
                keys=sorted_unique_keys(paths),
                semantic_kind="yaml_entry",
                name=top,
            ))
    return spans


# ── JSON / JSONL ────────────────────────────────────────────────────

def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise AdapterParseError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _pointer_to_dotted(pointer: str) -> str:
    output = ""
    for segment in pointer.strip("/").split("/"):
        if segment.isdigit():
            output += f"[{segment}]"
        else:
            output = f"{output}.{segment}" if output else segment
    return output


def _json_member_spans(source: str, offset: int, index_prefix: str) -> list[SemanticSpan]:
    try:
        json.loads(source, object_pairs_hook=_reject_duplicate_keys)
        entries = json_source_positions(source)
    except AdapterParseError:
        raise
    except (json.JSONDecodeError, JsonSourceMapError, RecursionError) as error:
        raise AdapterParseError(f"invalid JSON: {type(error).__name__}") from error
    top_level = {
        pointer: entry for pointer, entry in entries.items()
        if pointer and pointer.count("/") == 1
    }
    spans: list[SemanticSpan] = []
    for pointer, entry in sorted(top_level.items(), key=lambda item: item[1].value_start.position):
        start = (entry.key_start or entry.value_start).position
        end = entry.value_end.position
        dotted_prefix = index_prefix + _pointer_to_dotted(pointer)
        paths = [dotted_prefix] + [
            index_prefix + _pointer_to_dotted(child)
            for child in entries
            if child.startswith(pointer + "/")
        ]
        spans.append(SemanticSpan(
            char_start=offset + start,
            char_end=offset + end,
            keys=sorted_unique_keys(paths),
            semantic_kind="json_entry",
            name=dotted_prefix,
        ))
    return spans


def parse_json_spans(source: str) -> list[SemanticSpan]:
    return _json_member_spans(source, 0, "")


def parse_jsonl_spans(source: str) -> list[SemanticSpan]:
    """One span per non-empty line; keys carry the ``[line-index]`` prefix."""
    spans: list[SemanticSpan] = []
    offsets = line_offsets(source)
    lines = source.splitlines()
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            json.loads(line, object_pairs_hook=_reject_duplicate_keys)
            entries = json_source_positions(line)
        except AdapterParseError:
            raise
        except (json.JSONDecodeError, JsonSourceMapError) as error:
            raise AdapterParseError(f"invalid JSONL line {index + 1}: {type(error).__name__}") from error
        paths = [f"[{index}].{_pointer_to_dotted(pointer)}" for pointer in entries if pointer]
        start = offsets[index]
        spans.append(SemanticSpan(
            char_start=start,
            char_end=start + len(line),
            keys=sorted_unique_keys(paths),
            semantic_kind="json_entry",
            name=f"[{index}]",
        ))
    return spans


# ── properties / .env ───────────────────────────────────────────────

def _property_key(record: str) -> str:
    """Key before the first unescaped separator, with escapes resolved."""
    output: list[str] = []
    index = 0
    body = record.lstrip()
    while index < len(body):
        char = body[index]
        if char == "\\" and index + 1 < len(body):
            output.append(body[index + 1])
            index += 2
            continue
        if char in ("=", ":") or char in (" ", "\t"):
            break
        output.append(char)
        index += 1
    return "".join(output)


def _record_continues(line: str) -> bool:
    """java.util.Properties rule: an odd trailing-backslash count escapes the newline."""
    trailing = len(line) - len(line.rstrip("\\"))
    return trailing % 2 == 1


def parse_properties_spans(source: str) -> list[SemanticSpan]:
    """One span per logical record (continuation lines included)."""
    spans: list[SemanticSpan] = []
    offsets = line_offsets(source)
    lines = source.splitlines()
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if not stripped or stripped.startswith(("#", "!")):
            index += 1
            continue
        start_line = index
        while index + 1 < len(lines) and _record_continues(lines[index]):
            index += 1
        end = extend_through_eol(source, offsets[index] + len(lines[index]))
        key = _property_key(lines[start_line])
        spans.append(SemanticSpan(
            char_start=offsets[start_line],
            char_end=end,
            keys=sorted_unique_keys([key]),
            semantic_kind="record",
            name=key,
            # properties 는 형식 자체가 "키로 값을 꺼내는 주소록"이다 (spec 081).
            addressable_keys=sorted_unique_keys([key]),
        ))
        index += 1
    return spans


# ── INI / cfg / conf ────────────────────────────────────────────────

def parse_ini_spans(source: str) -> list[SemanticSpan]:
    """One span per section (header through last non-blank line)."""
    offsets = line_offsets(source)
    lines = source.splitlines()
    sections: list[tuple[int, int, str]] = []   # (start_line, end_line, name)
    current_start = -1
    current_name = ""
    last_content = -1
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]") and len(stripped) > 2:
            if current_start >= 0:
                sections.append((current_start, last_content, current_name))
            current_start = index
            current_name = stripped[1:-1].strip()
            last_content = index
        elif current_start >= 0 and stripped:
            last_content = index
    if current_start >= 0:
        sections.append((current_start, last_content, current_name))
    if not sections:
        return parse_properties_spans(source)
    spans: list[SemanticSpan] = []
    for start_line, end_line, name in sections:
        keys = [name]
        for line in lines[start_line + 1:end_line + 1]:
            stripped = line.strip()
            if not stripped or stripped.startswith((";", "#")):
                continue
            for separator in ("=", ":"):
                head, _, _tail = stripped.partition(separator)
                if _ == separator:
                    keys.append(f"{name}.{head.strip()}")
                    break
        spans.append(SemanticSpan(
            char_start=offsets[start_line],
            char_end=extend_through_eol(source, offsets[end_line] + len(lines[end_line])),
            keys=sorted_unique_keys(keys),
            semantic_kind="record",
            name=name,
            # ini/conf 도 섹션·키로 값을 꺼내는 주소록이다 (spec 081).
            addressable_keys=sorted_unique_keys(keys),
        ))
    return spans


# ── CSV / TSV ───────────────────────────────────────────────────────

def _csv_row_spans(source: str, delimiter: str) -> list[tuple[int, int]]:
    """Quote-aware logical row spans, each including its newline."""
    rows: list[tuple[int, int]] = []
    start = 0
    index = 0
    in_quotes = False
    length = len(source)
    while index < length:
        char = source[index]
        if char == '"':
            if in_quotes and source.startswith('""', index):
                index += 2
                continue
            in_quotes = not in_quotes
        elif not in_quotes and char == "\n":
            rows.append((start, index + 1))
            start = index + 1
        index += 1
    if in_quotes:
        raise AdapterParseError("unterminated quoted CSV field")
    if start < length:
        rows.append((start, length))
    return rows


def parse_csv_spans(source: str, delimiter: str = ",") -> list[SemanticSpan]:
    """One span per data row; the header row defines columns, not a chunk."""
    rows = _csv_row_spans(source, delimiter)
    spans: list[SemanticSpan] = []
    for start, end in rows[1:]:
        text = source[start:end]
        if not text.strip():
            continue
        first_field = text.split(delimiter, 1)[0].strip().strip('"')
        keys = [first_field] if is_reference_key(first_field) else []
        spans.append(SemanticSpan(
            char_start=start,
            char_end=end,
            keys=sorted_unique_keys(keys),
            semantic_kind="row",
            name=first_field,
        ))
    return spans
