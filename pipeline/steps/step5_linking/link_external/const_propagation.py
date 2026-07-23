"""One-step immutable symbol propagation using step2 Variable/Constant nodes."""
from __future__ import annotations

import re

from graph.schema.graph_nodes import CodeNode, Constant, Variable
from pipeline.steps.step5_linking.link_external.key_index import mask_comments_and_literals, quoted_literals


def _pure_literal(expression: str) -> str | None:
    occurrences = quoted_literals(expression)
    if len(occurrences) != 1:
        return None
    occurrence = occurrences[0]
    remainder = expression[:occurrence.start] + expression[occurrence.end:]
    if remainder.strip().rstrip(";").strip():
        return None
    return occurrence.value


def _immutable_variable(variable: Variable) -> bool:
    markers = f"{variable.label} {variable.annotations}".upper()
    if "FINAL" in markers or "IMMUTABLE" in markers or "CONSTANT" in markers:
        return True
    return bool(re.search(r"\b(?:final|const)\b", variable.code_text, re.IGNORECASE))


def immutable_symbols(
    variables: list[Variable], constants: list[Constant], known_keys: set[str],
) -> dict[str, dict[str, tuple[str, str]]]:
    """Return owner→name→(key, symbol-id), rejecting duplicate/impure declarations."""
    candidates: dict[str, dict[str, list[tuple[str, str]]]] = {}
    for constant in constants:
        value = _pure_literal(constant.value)
        if value in known_keys:
            candidates.setdefault(constant.owner_id, {}).setdefault(constant.name, []).append((value, constant.id))
    for variable in variables:
        if not _immutable_variable(variable):
            continue
        value = _pure_literal(variable.init_value)
        if value in known_keys:
            candidates.setdefault(variable.owner_id, {}).setdefault(variable.name, []).append((value, variable.id))
    return {
        owner: {name: values[0] for name, values in names.items() if len(values) == 1}
        for owner, names in candidates.items()
    }


def referenced_immutable_keys(
    code_node: CodeNode,
    symbols: dict[str, dict[str, tuple[str, str]]],
) -> list[tuple[str, str, int]]:
    """Return (key, symbol-name, evidence-line) for safe references in one routine."""
    code = mask_comments_and_literals(code_node.code_text)
    output: list[tuple[str, str, int]] = []
    for name, (key, _symbol_id) in symbols.get(code_node.owner_id, {}).items():
        word = re.compile(rf"(?<![\w.]){re.escape(name)}(?!\w)")
        match = word.search(code)
        if match is None:
            continue
        reassignment = re.compile(
            rf"(?<![\w.]){re.escape(name)}(?!\w)\s*(?:\+\+|--|[+\-*/%]=|=(?!=))"
        )
        if reassignment.search(code):
            continue
        line = code_node.start_line + code.count("\n", 0, match.start())
        output.append((key, name, line))
    return output
