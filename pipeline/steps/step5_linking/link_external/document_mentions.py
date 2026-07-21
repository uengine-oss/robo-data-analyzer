"""Exact, ambiguity-safe document-to-code mention facts."""
from __future__ import annotations

from collections import defaultdict
import re

from graph.schema.graph_edges import Mentions
from graph.schema.graph_nodes import CodeNode, DocumentNode


_IDENTIFIER = re.compile(r"[A-Za-z0-9_$]")


def _contains_identifier(text: str, value: str) -> bool:
    if not value:
        return False
    start = 0
    while True:
        index = text.find(value, start)
        if index < 0:
            return False
        before = text[index - 1] if index else ""
        after_index = index + len(value)
        after = text[after_index] if after_index < len(text) else ""
        if (not before or not _IDENTIFIER.match(before)) and (
            not after or not _IDENTIFIER.match(after)
        ):
            return True
        start = index + 1


def link_exact_document_mentions(
    document_nodes: list[DocumentNode],
    code_nodes: list[CodeNode],
    routine_ids: set[str],
) -> list[Mentions]:
    """Link only exact unique routine identifiers from document chunks.

    The fact target is a routine because per-node analysis context is the
    consumer. Ambiguous short routine names are intentionally ignored and left
    to embedding candidates.
    """
    routines = sorted((node for node in code_nodes if node.id in routine_ids), key=lambda node: node.id)
    by_name: dict[str, list[CodeNode]] = defaultdict(list)
    for node in routines:
        name = node.name.strip()
        if len(name) >= 3:
            by_name[name].append(node)

    edges: list[Mentions] = []
    for document in sorted(
        (node for node in document_nodes if node.label == "DOC_CHUNK" and node.text),
        key=lambda node: (node.file_path, node.order, node.id),
    ):
        for name in sorted(by_name):
            targets = by_name[name]
            if len(targets) != 1 or not _contains_identifier(document.text, name):
                continue
            edges.append(Mentions(
                document.id,
                targets[0].id,
                name,
                document.file_path,
                document.start_line,
            ))
    return edges
