"""Project Parser-owned call structure into Analyzer's internal call ledger.

This module joins exact Parser ranges to existing AST ``CodeNode`` identities.  It
does not parse source text or decide binding, linkage, or internal/external status.
"""
from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict, deque
from collections.abc import Callable

from graph.schema.graph_nodes import CodeNode
from pipeline.steps.step2_load_ast.evidence_ir import (
    CallEvidenceFact,
    CommonEvidenceContractError,
    ValidatedCommonEvidence,
)
from pipeline.steps.step2_load_ast.node_builders import ParseResult


def physical_line_starts(source: str) -> tuple[int, ...]:
    """Return code-point offsets for every physical line in decoded source."""
    starts = [0]
    index = 0
    while index < len(source):
        if source[index] == "\r":
            if index + 1 < len(source) and source[index + 1] == "\n":
                index += 1
            starts.append(index + 1)
        elif source[index] == "\n":
            starts.append(index + 1)
        index += 1
    return tuple(starts)


def physical_line(line_starts: tuple[int, ...], offset: int) -> int:
    """Convert a zero-based code-point offset to a one-based physical line."""
    return bisect_right(line_starts, offset)


def build_call_occurrence_record(
    fact: CallEvidenceFact,
    evidence: ValidatedCommonEvidence,
    *,
    caller_id: str,
    file_path: str,
    line_starts: tuple[int, ...] | None = None,
) -> dict:
    """Build one exact occurrence record without deciding its final target."""
    starts = line_starts or physical_line_starts(evidence.decoded_text)
    source = evidence.decoded_text

    def exact_source(source_range) -> str:
        return source[source_range.char_offset:source_range.end]

    return {
        "call_id": fact.fact_id,
        "caller_id": caller_id,
        "owner_status": "resolved" if caller_id else "unresolved_no_routine",
        "call_text": fact.terminal_name or "",
        "callee_kind": fact.callee_kind,
        "source_id": evidence.source_id,
        "file": file_path,
        "line": physical_line(starts, fact.call_range.char_offset),
        "range": fact.call_range.as_dict(),
        "call_source": exact_source(fact.call_range),
        "callee_range": fact.callee_range.as_dict(),
        "callee_source": exact_source(fact.callee_range),
        "arguments": [
            {**item.as_record(), "source": exact_source(item.range)}
            for item in fact.arguments
        ],
        "receiver_range": (
            fact.receiver_range.as_dict()
            if fact.receiver_range is not None else None
        ),
        "receiver_source": (
            exact_source(fact.receiver_range)
            if fact.receiver_range is not None else None
        ),
        "scope_path": [item.as_record() for item in fact.scope_path],
        "callee_path_components": [
            exact_source(item) for item in fact.callee_path_ranges
        ],
        "database_link": (
            exact_source(fact.database_link_range)
            if fact.database_link_range is not None else None
        ),
        "grammar_rule": fact.grammar_rule,
        "presence_status": fact.presence["status"],
    }


def project_callable_definitions(
    result: ParseResult,
    evidence: ValidatedCommonEvidence,
    *,
    is_routine_node: Callable[[CodeNode], bool],
) -> dict[tuple[int, int], str]:
    """Bind callable definitions and index owners by the definition's own range."""
    line_starts = physical_line_starts(evidence.decoded_text)
    nodes_by_coordinate: dict[tuple[str, int], deque[CodeNode]] = defaultdict(deque)
    for node in result.code_nodes:
        if node.name and is_routine_node(node):
            nodes_by_coordinate[(node.name, node.start_line)].append(node)

    records: list[dict] = []
    owner_id_by_definition_range: dict[tuple[int, int], str] = {}
    for fact in evidence.callable_facts:
        code_node_id: str | None = None
        if fact.role == "definition":
            line = physical_line(line_starts, fact.callable_range.char_offset)
            matches = nodes_by_coordinate[(fact.terminal_name, line)]
            if not matches:
                raise CommonEvidenceContractError(
                    "common evidence callable definition projection: "
                    f"factId={fact.fact_id}, name={fact.terminal_name!r}, "
                    f"line={line}, remainingCodeNodes=0"
                )
            code_node_id = matches.popleft().id
            definition_key = (
                fact.callable_range.char_offset,
                fact.callable_range.char_length,
            )
            previous = owner_id_by_definition_range.setdefault(
                definition_key, code_node_id,
            )
            if previous != code_node_id:
                raise CommonEvidenceContractError(
                    "common evidence callable definition owner projection: "
                    f"range={definition_key}, owners={previous!r},{code_node_id!r}"
                )

        record = fact.as_record(code_node_id=code_node_id)
        record["name_path_components"] = [
            evidence.decoded_text[item.char_offset:item.end]
            for item in fact.name_path_ranges
        ]
        records.append(record)

    fact_ids = [record["fact_id"] for record in records]
    if len(fact_ids) != len(set(fact_ids)):
        raise CommonEvidenceContractError(
            f"common evidence callable population: duplicate fact IDs in {evidence.source_id}"
        )
    result.callable_facts.extend(records)
    return owner_id_by_definition_range


def project_scope_owned_calls(
    result: ParseResult,
    evidence: ValidatedCommonEvidence,
    *,
    owner_id_by_definition_range: dict[tuple[int, int], str],
    file_path: str,
) -> None:
    """Assign DBMS call occurrences to their innermost Parser function scope."""
    line_starts = physical_line_starts(evidence.decoded_text)
    records: list[dict] = []
    for fact in evidence.call_facts:
        function_scopes = [
            item for item in fact.scope_path if item.kind == "function"
        ]
        caller_id = ""
        for scope in reversed(function_scopes):
            scope_key = (scope.range.char_offset, scope.range.char_length)
            caller_id = owner_id_by_definition_range.get(scope_key, "")
            if caller_id:
                break
        if function_scopes and not caller_id:
            innermost = function_scopes[-1].range
            raise CommonEvidenceContractError(
                "common evidence call owner projection: "
                f"factId={fact.fact_id}, functionScope="
                f"{innermost.char_offset}:{innermost.char_length}, owner=missing"
            )
        records.append(build_call_occurrence_record(
            fact,
            evidence,
            caller_id=caller_id,
            file_path=file_path,
            line_starts=line_starts,
        ))

    fact_ids = [record["call_id"] for record in records]
    if len(fact_ids) != len(set(fact_ids)):
        raise CommonEvidenceContractError(
            f"common evidence call population: duplicate fact IDs in {evidence.source_id}"
        )
    result.call_sites[:] = records
