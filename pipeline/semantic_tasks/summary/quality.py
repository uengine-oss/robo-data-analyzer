"""Framework/DBMS가 공유하는 표시·검색 텍스트 경계."""
from __future__ import annotations

from collections.abc import Iterable
import re

from graph.schema.graph_nodes import CodeNode
from pipeline.models.analysis_context import ReferencedSymbolContext


_IDENTIFIER = re.compile(
    r"[A-Za-z_][A-Za-z0-9_$#]*(?:[.%][A-Za-z_][A-Za-z0-9_$#]*)*"
)
_NON_IDENTIFIERS = frozenset({
    "and", "false", "not", "null", "or", "true",
})
_COMPARISON = re.compile(
    r"(?P<left>[A-Za-z_][A-Za-z0-9_$#]*(?:[.%][A-Za-z_][A-Za-z0-9_$#]*)*)"
    r"\s*(?P<operator><>|!=|>=|<=|==|=|>|<)\s*"
    r"(?P<right>"
    r"[A-Za-z_][A-Za-z0-9_$#]*(?:[.%][A-Za-z_][A-Za-z0-9_$#]*)*"
    r"|[-+]?\d+(?:\.\d+)?"
    r"|'[^'\r\n]*'|\"[^\"\r\n]*\""
    r")"
)
_OPERATOR_COMPLEMENT = {
    "=": "<>",
    "<>": "=",
    ">": "<=",
    "<=": ">",
    ">=": "<",
    "<": ">=",
}


def normalize_canonical_summary(summary: str) -> str:
    """Normalize the one model-owned text reused for display, search, and handoff."""
    normalized = (summary or "").strip()
    if not normalized:
        raise ValueError("summary is empty")
    return normalized


def copied_callee_summary_id(
    summary: str,
    callee_summaries: Iterable[tuple[str, str]],
) -> str | None:
    """Return the copied callee id when exact equality proves lost ownership.

    Semantic similarity is not deterministic evidence, so this boundary only
    normalizes insignificant whitespace and case.  Paraphrases and containment
    remain part of the direct-meaning audit.
    """

    candidate = " ".join(normalize_canonical_summary(summary).split()).casefold()
    for callee_id, callee_summary in callee_summaries:
        if not isinstance(callee_summary, str) or not callee_summary.strip():
            continue
        callee = " ".join(callee_summary.split()).casefold()
        if candidate == callee:
            return callee_id
    return None


def missing_structural_summary_identities(
    summary: str,
    node: CodeNode,
) -> tuple[str, ...]:
    """Return direct AST identities omitted from a model-owned summary.

    This is deliberately a validation boundary, not a semantic generator. It
    never derives prose from names or syntax. It only checks that physical
    identities already supplied by the parser remain visible in the model's
    natural-language account of a direct structural effect.
    """

    label = (node.label or "").upper()
    required: list[str] = []
    if label in {"CALL", "FUNCTION_CALL"} and (node.name or "").strip():
        required.append(node.name.strip())
    if label == "ASSIGNMENT" and (node.target or "").strip():
        required.extend(_physical_identifiers(node.target))
    if label in {"EXIT", "RETURN", "THROW", "RAISE"}:
        required.extend(_physical_identifiers(node.expression))

    folded_summary = normalize_canonical_summary(summary).casefold()
    return tuple(
        identity
        for identity in dict.fromkeys(required)
        if identity.casefold() not in folded_summary
    )


def unsupported_code_comparison_claims(
    summary: str,
    evidence_text: str,
    symbols: Iterable[ReferencedSymbolContext],
) -> tuple[str, ...]:
    """Reject only high-confidence operand substitutions in code-like claims.

    This boundary does not infer business meaning or require prose to repeat code.
    It checks explicit ``identifier operator operand`` text that the model chose to
    emit.  When the authoritative input contains the same operator/right operand
    on a different, non-alias symbol, the candidate has changed a physical fact.

    Equality is intentionally not inferred from dataflow such as COUNT/filter
    relationships.  Only parser-provided simple initialization aliases are
    equivalent.  Complement operators are allowed because a summary may describe
    the explicit else path of a source comparison.
    """

    known, aliases = _symbol_aliases(symbols)
    if not known:
        return ()
    source_comparisons = tuple(_comparison_facts(evidence_text))
    if not source_comparisons:
        return ()

    rejected: list[str] = []
    for raw_claim, left, operator, right in _comparison_facts(summary):
        if left not in known:
            continue
        allowed_operators = {operator, _OPERATOR_COMPLEMENT.get(operator, operator)}
        left_group = aliases.get(left, frozenset({left}))
        right_group = aliases.get(right, frozenset({right}))

        if any(
            source_operator in allowed_operators
            and source_left in left_group
            and source_right in right_group
            for _raw, source_left, source_operator, source_right in source_comparisons
        ):
            continue

        collisions = [
            source_raw
            for source_raw, source_left, source_operator, source_right
            in source_comparisons
            if source_operator in allowed_operators
            and source_right in right_group
            and source_left in known
            and source_left not in left_group
        ]
        if collisions:
            rejected.append(f"{raw_claim} (source: {collisions[0]})")
    return tuple(dict.fromkeys(rejected))


def _comparison_facts(text: str):
    for match in _COMPARISON.finditer(text or ""):
        yield (
            match.group(0),
            _normalize_identifier(match.group("left")),
            _normalize_operator(match.group("operator")),
            _normalize_operand(match.group("right")),
        )


def _symbol_aliases(
    symbols: Iterable[ReferencedSymbolContext],
) -> tuple[frozenset[str], dict[str, frozenset[str]]]:
    names: set[str] = set()
    edges: dict[str, set[str]] = {}
    for symbol in symbols:
        name = _normalize_identifier(symbol.name)
        if not name:
            continue
        names.add(name)
        candidates = [symbol.declared_value, *symbol.initialization_sources]
        for candidate in candidates:
            if not _IDENTIFIER.fullmatch((candidate or "").strip()):
                continue
            other = _normalize_identifier(candidate)
            names.add(other)
            edges.setdefault(name, set()).add(other)
            edges.setdefault(other, set()).add(name)

    groups: dict[str, frozenset[str]] = {}
    for name in names:
        pending = [name]
        connected: set[str] = set()
        while pending:
            current = pending.pop()
            if current in connected:
                continue
            connected.add(current)
            pending.extend(edges.get(current, ()))
        group = frozenset(connected)
        for member in group:
            groups[member] = group
    return frozenset(names), groups


def _normalize_identifier(value: str) -> str:
    return (value or "").strip().casefold()


def _normalize_operand(value: str) -> str:
    stripped = (value or "").strip()
    if _IDENTIFIER.fullmatch(stripped):
        return _normalize_identifier(stripped)
    return stripped.casefold()


def _normalize_operator(value: str) -> str:
    if value == "==":
        return "="
    if value == "!=":
        return "<>"
    return value


def _physical_identifiers(expression: str) -> tuple[str, ...]:
    return tuple(
        token
        for token in _IDENTIFIER.findall(expression or "")
        if token.casefold() not in _NON_IDENTIFIERS
    )
