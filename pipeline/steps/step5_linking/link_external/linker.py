"""Precision-first REFER_TO linker (spec 078): exact evidence only, no inference.

A routine links to an external chunk iff a quoted literal in its body (or a
one-step immutable symbol value) equals a chunk key whole-token and that key
identifies exactly one chunk project-wide.  Duplicate, ambiguous or dynamic
keys link nothing — unlinked chunks are normal data, not errors.
"""
from __future__ import annotations

from dataclasses import dataclass

from graph.schema.graph_edges import ReferTo
from graph.schema.graph_nodes import CodeNode, Constant, ExternalChunk, Variable
from pipeline.steps.step5_linking.link_external.const_propagation import (
    immutable_symbols,
    referenced_immutable_keys,
)
from pipeline.steps.step5_linking.link_external.key_index import match_keys


class ExternalLinkContractError(ValueError):
    pass


@dataclass(frozen=True)
class LinkOutcome:
    external_edges: list[ReferTo]


def link_external(
    *,
    code_nodes: list[CodeNode],
    variables: list[Variable],
    constants: list[Constant],
    chunks: list[ExternalChunk],
    routine_ids: set[str],
) -> LinkOutcome:
    """Link exact whole-token literal evidence to uniquely-keyed chunks."""
    chunks_by_key: dict[str, list[ExternalChunk]] = {}
    for chunk in chunks:
        if chunk.provenance != "source_span":
            continue                          # derived chunks are never link targets
        for key in chunk.keys:
            chunks_by_key.setdefault(key, []).append(chunk)
    # 유일성 규칙: 같은 key 를 노출하는 청크가 2개 이상이면 그 key 로는 연결하지 않는다.
    unique_target: dict[str, ExternalChunk] = {
        key: targets[0] for key, targets in chunks_by_key.items() if len(targets) == 1
    }

    routines = sorted(
        (node for node in code_nodes if node.id in routine_ids),
        key=lambda node: node.id,
    )
    edges: list[ReferTo] = []
    seen: set[tuple[str, str]] = set()

    def add(edge: ReferTo) -> None:
        identity = (edge.source_id, edge.target_id)
        if identity not in seen:
            seen.add(identity)
            edges.append(edge)

    for code in routines:
        for key, relative_line in match_keys(code.code_text, unique_target):
            evidence_line = code.start_line + relative_line - 1 if code.start_line else relative_line
            add(ReferTo(
                source_id=code.id,
                target_id=unique_target[key].id,
                matched_key=key,
                evidence_file=code.file_path,
                evidence_line=max(0, evidence_line),
            ))

    # One-step immutable propagation consumes step2 Constant/Variable facts.
    symbols = immutable_symbols(variables, constants, set(unique_target))
    for code in routines:
        for key, symbol_name, evidence_line in referenced_immutable_keys(code, symbols):
            add(ReferTo(
                source_id=code.id,
                target_id=unique_target[key].id,
                matched_key=key,
                evidence_file=code.file_path,
                evidence_line=max(0, evidence_line),
                via_symbol=symbol_name,
            ))

    return LinkOutcome(external_edges=edges)
