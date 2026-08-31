"""PostgreSQL binding over Parser-owned callable and call structure."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping, Sequence

from graph.schema.graph_nodes import CodeNode
from pipeline.steps.step5_linking.link_calls.semantic_adapter import (
    LanguageSemanticCandidateSet,
    SemanticCallableCandidate,
    range_tuple,
    scope_identity,
)


_SCHEMA = "analyzer-postgresql-semantics/v1"
_PARAMETER_MODES = frozenset({"IN", "OUT", "INOUT", "VARIADIC"})


def _normalize_identifier(value: Any) -> str:
    text = str(value or "")
    if not text or text != text.strip():
        return ""
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        return "Q:" + text[1:-1].replace('""', '"')
    return "U:" + text.lower()


def _identity(components: Sequence[Any]) -> tuple[str, ...]:
    normalized = tuple(_normalize_identifier(value) for value in components)
    return normalized if normalized and all(normalized) else ()


def _walk_components(component: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    yield component
    for child in component.get("children") or ():
        yield from _walk_components(child)


def _walk_tokens(component: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    for value in _walk_components(component):
        yield from value.get("direct_tokens") or ()


def _token_value(token: Mapping[str, Any]) -> str:
    return str(token.get("text") or token.get("token_kind") or "")


def _postgresql_callable_facts(
    callables: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    return [
        fact for fact in callables
        if (fact.get("syntax") or {}).get("schema")
        == "postgresql-callable-syntax/v1"
    ]


def _callable_kind(fact: Mapping[str, Any]) -> str:
    syntax = fact.get("syntax") or {}
    values = {
        _token_value(token).upper()
        for component in syntax.get("declaration_specifiers") or ()
        for token in _walk_tokens(component)
    }
    matching = values & {"FUNCTION", "PROCEDURE"}
    return next(iter(matching)) if len(matching) == 1 else ""


def _call_kind(call: Mapping[str, Any]) -> str:
    grammar_rule = str(call.get("grammar_rule") or "")
    if grammar_rule == "functionCall":
        return "FUNCTION"
    if grammar_rule == "sqlGenericStmt":
        return "PROCEDURE"
    return ""


def _parameter_components(fact: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    declarator = (fact.get("syntax") or {}).get("declarator") or {}
    return [
        component for component in _walk_components(declarator)
        if component.get("grammar_rule") == "func_arg_with_default"
    ]


def _func_arg(parameter: Mapping[str, Any]) -> Mapping[str, Any]:
    return next((
        component for component in _walk_components(parameter)
        if component.get("grammar_rule") == "func_arg"
    ), {})


def _parameter_mode(parameter: Mapping[str, Any]) -> str:
    values = {
        _token_value(token).upper()
        for token in _walk_tokens(_func_arg(parameter))
    }
    matching = values & _PARAMETER_MODES
    return next(iter(matching)) if len(matching) == 1 else "IN"


def _has_default(parameter: Mapping[str, Any]) -> bool:
    return any(
        str(token.get("token_kind") or "").upper() in {"DEFAULT", "EQUAL"}
        or _token_value(token).upper() in {"DEFAULT", "="}
        for token in parameter.get("direct_tokens") or ()
    )


def _arity_compatibility(
    call: Mapping[str, Any],
    fact: Mapping[str, Any],
    callable_kind: str,
) -> str:
    parameters = _parameter_components(fact)
    considered = [
        parameter for parameter in parameters
        if callable_kind == "PROCEDURE" or _parameter_mode(parameter) != "OUT"
    ]
    variadic = any(_parameter_mode(parameter) == "VARIADIC" for parameter in considered)
    required = sum(
        not _has_default(parameter)
        and _parameter_mode(parameter) != "VARIADIC"
        for parameter in considered
    )
    supplied = len(call.get("arguments") or ())
    if supplied < required:
        return "incompatible"
    if not variadic and supplied > len(considered):
        return "incompatible"
    return "compatible"


def _canonical_component(component: Mapping[str, Any]) -> Any:
    rule = str(component.get("grammar_rule") or "")
    if rule == "param_name":
        return None
    tokens = sorted(
        (
            range_tuple(token.get("range") or {}),
            str(token.get("token_kind") or ""),
            _token_value(token),
        )
        for token in component.get("direct_tokens") or ()
    )
    children = [
        value for child in component.get("children") or ()
        if (value := _canonical_component(child)) is not None
    ]
    return [rule, tokens, children]


def _type_key(fact: Mapping[str, Any], callable_kind: str) -> str:
    parameters = _parameter_components(fact)
    considered = [
        parameter for parameter in parameters
        if callable_kind == "PROCEDURE" or _parameter_mode(parameter) != "OUT"
    ]
    payload = [
        _canonical_component(_func_arg(parameter)) for parameter in considered
    ]
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _stable_key(namespace: str, value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return namespace + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class PostgreSqlSemanticAdapter:
    schema = _SCHEMA

    def adapt(
        self,
        call: Mapping[str, Any],
        callables: Sequence[Mapping[str, Any]],
        parameter_facts: Sequence[Mapping[str, Any]] = (),
        binding_target_facts: Sequence[Mapping[str, Any]] = (),
        scope_directive_facts: Sequence[Mapping[str, Any]] = (),
        import_refs: Sequence[Mapping[str, Any]] = (),
        code_nodes: Sequence[CodeNode] = (),
    ) -> LanguageSemanticCandidateSet:
        del parameter_facts, binding_target_facts, scope_directive_facts
        del import_refs, code_nodes
        if call.get("callee_kind") != "named":
            return self._terminal(
                call, "unsupported", "unsupported_postgresql_callee_kind",
            )

        raw_identity = _identity(call.get("callee_path_components") or ())
        if not raw_identity or len(raw_identity) > 2:
            return self._terminal(
                call, "unsupported", "invalid_postgresql_structural_callee_path",
            )
        call_kind = _call_kind(call)
        if not call_kind:
            return self._terminal(
                call, "unsupported", "unsupported_postgresql_call_syntax",
            )
        if len(raw_identity) == 1:
            return self._terminal(
                call,
                "configuration_dependent",
                "postgresql_search_path_not_sealed",
                terminal_name=raw_identity[-1],
            )

        matching: list[tuple[Mapping[str, Any], tuple[str, ...], str]] = []
        for fact in _postgresql_callable_facts(callables):
            identity = _identity(fact.get("name_path_components") or ())
            callable_kind = _callable_kind(fact)
            if not identity or len(identity) > 2 or not callable_kind:
                return self._terminal(
                    call,
                    "unsupported",
                    "invalid_postgresql_callable_structure",
                    terminal_name=raw_identity[-1],
                )
            if identity == raw_identity and callable_kind == call_kind:
                matching.append((fact, identity, callable_kind))

        candidates = tuple(
            SemanticCallableCandidate(
                fact_id=str(fact.get("fact_id") or ""),
                source_id=str(fact.get("source_id") or ""),
                terminal_name=raw_identity[-1],
                role=str(fact.get("role") or ""),
                scope_path=scope_identity(fact.get("scope_path") or ()),
                declaration_point=int(fact.get("declaration_point", -1)),
                type_compatibility=_arity_compatibility(call, fact, callable_kind),
                linkage_visibility="visible",
                linkage_key=_stable_key(
                    "postgresql-linkage/v1:", [identity, callable_kind],
                ),
                linkage_scope="project",
                type_key=_type_key(fact, callable_kind),
                code_node_id=str(fact.get("code_node_id") or ""),
                presence_status=str(fact.get("presence_status") or ""),
            )
            for fact, identity, callable_kind in matching
        )
        return LanguageSemanticCandidateSet(
            call_id=str(call.get("call_id") or ""),
            adapter_schema=self.schema,
            language="postgresql",
            terminal_name=raw_identity[-1],
            source_id=str(call.get("source_id") or ""),
            call_start=range_tuple(call.get("range") or {})[0],
            scope_path=scope_identity(call.get("scope_path") or ()),
            candidates=candidates,
            external_identity_namespace=_stable_key(
                "postgresql-callable/v1:", [raw_identity, call_kind],
            ),
        )

    def _terminal(
        self,
        call: Mapping[str, Any],
        status: str,
        reason: str,
        *,
        terminal_name: str = "",
    ) -> LanguageSemanticCandidateSet:
        terminal = terminal_name or _normalize_identifier(call.get("call_text"))
        return LanguageSemanticCandidateSet(
            call_id=str(call.get("call_id") or ""),
            adapter_schema=self.schema,
            language="postgresql",
            terminal_name=terminal or "U:unknown",
            source_id=str(call.get("source_id") or ""),
            call_start=range_tuple(call.get("range") or {})[0],
            scope_path=scope_identity(call.get("scope_path") or ()),
            candidates=(),
            external_identity_namespace="postgresql-callable/v1:",
            terminal_status=status,
            terminal_reason=reason,
        )
