"""Literal constant facts for deterministic external linking.

This is deliberately not value tracking.  It accepts only a Constant whose
declaration value is one complete quoted literal and a unique REFERENCES edge
from the routine to that constant.  Local assignments, mutable variables,
concatenation, control flow, and recursive INIT_BY evaluation are outside the
contract.
"""
from __future__ import annotations

from collections import defaultdict

from graph.schema.graph_edges import References
from graph.schema.graph_nodes import Constant
from pipeline.steps.step5_linking.link_external.key_index import quoted_literals


class LiteralConstantIndex:
    """Resolve a referenced name only when one literal declaration proves it."""

    def __init__(
        self,
        constants: list[Constant],
        references: list[References] | None = None,
    ) -> None:
        literal_by_id: dict[str, str] = {}
        name_by_id: dict[str, str] = {}
        for constant in constants:
            literal = _complete_literal(constant.value)
            if literal is not None:
                literal_by_id[constant.id] = literal
                name_by_id[constant.id] = constant.name

        candidates: dict[str, dict[str, set[str]]] = defaultdict(
            lambda: defaultdict(set)
        )
        for edge in references or ():
            name = name_by_id.get(edge.target_id)
            if name is not None:
                candidates[edge.source_id][name].add(edge.target_id)

        self._values: dict[str, dict[str, str]] = {}
        for routine_id, by_name in candidates.items():
            resolved: dict[str, str] = {}
            for name, node_ids in by_name.items():
                if len(node_ids) == 1:
                    resolved[name] = literal_by_id[next(iter(node_ids))]
            self._values[routine_id] = resolved

    def value_for(self, routine_id: str, name: str) -> str | None:
        return self._values.get(routine_id, {}).get(name)


def _complete_literal(expression: str) -> str | None:
    raw = expression.strip()
    literals = quoted_literals(raw)
    if len(literals) != 1 or literals[0].start != 0 or literals[0].end != len(raw):
        return None
    value = literals[0].value
    return value if value else None
