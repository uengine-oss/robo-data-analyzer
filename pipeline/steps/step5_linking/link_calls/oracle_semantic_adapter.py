"""Oracle semantic binding over Parser-owned structural name components."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence

from graph.schema.graph_nodes import CodeNode
from pipeline.steps.step5_linking.link_calls.semantic_adapter import (
    LanguageSemanticCandidateSet,
    SemanticCallableCandidate,
    range_tuple,
    scope_identity,
)


_SCHEMA = "analyzer-oracle-semantics/v1"


def _normalize_identifier(value: Any) -> str:
    text = str(value or "")
    if not text or text != text.strip():
        return ""
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        return "Q:" + text[1:-1].replace('""', '"')
    return "U:" + text.upper()


def _identity(components: Sequence[Any]) -> tuple[str, ...]:
    normalized = tuple(_normalize_identifier(value) for value in components)
    return normalized if normalized and all(normalized) else ()


def _oracle_callable_facts(
    callables: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    return [
        fact for fact in callables
        if (fact.get("syntax") or {}).get("schema") == "oracle-callable-syntax/v1"
    ]


def _package_scope(
    path: tuple[tuple[str, int, int], ...],
) -> tuple[str, int, int] | None:
    return next((item for item in reversed(path) if item[0] == "package"), None)


def _possible_call_identities(
    raw_identity: tuple[str, ...],
    caller_identity: tuple[str, ...],
    *,
    caller_in_package: bool,
) -> frozenset[tuple[str, ...]]:
    result = {raw_identity}
    if not caller_identity:
        return frozenset(result)
    if caller_in_package and len(caller_identity) >= 2:
        package_prefix = caller_identity[:-1]
        schema_prefix = caller_identity[:-2]
        if len(raw_identity) == 1:
            result.add(package_prefix + raw_identity)
        elif schema_prefix:
            result.add(schema_prefix + raw_identity)
    elif len(caller_identity) >= 2:
        result.add(caller_identity[:-1] + raw_identity)
    return frozenset(result)


def _walk_components(component: Mapping[str, Any]):
    yield component
    for child in component.get("children") or ():
        yield from _walk_components(child)


def _parameter_components(fact: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    declarator = (fact.get("syntax") or {}).get("declarator") or {}
    return [
        component for component in declarator.get("children") or ()
        if component.get("grammar_rule") == "parameter"
    ]


def _has_default(parameter: Mapping[str, Any]) -> bool:
    return any(
        component.get("grammar_rule") == "default_value_part"
        for component in _walk_components(parameter)
    )


def _arity_compatibility(
    call: Mapping[str, Any], fact: Mapping[str, Any],
) -> str:
    parameters = _parameter_components(fact)
    required = sum(not _has_default(parameter) for parameter in parameters)
    supplied = len(call.get("arguments") or ())
    return "compatible" if required <= supplied <= len(parameters) else "incompatible"


def _canonical_parameter(component: Mapping[str, Any]) -> Any:
    rule = str(component.get("grammar_rule") or "")
    if rule in {"parameter_name", "default_value_part"}:
        return None
    tokens = sorted(
        (
            range_tuple(token.get("range") or {}),
            str(token.get("token_kind") or ""),
            str(token.get("text") or ""),
        )
        for token in component.get("direct_tokens") or ()
    )
    children = [
        value for child in component.get("children") or ()
        if (value := _canonical_parameter(child)) is not None
    ]
    return [rule, tokens, children]


def _type_key(fact: Mapping[str, Any]) -> str:
    payload = [_canonical_parameter(value) for value in _parameter_components(fact)]
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _linkage_key(identity: tuple[str, ...]) -> str:
    encoded = json.dumps(identity, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _external_namespace(
    identities: frozenset[tuple[str, ...]], database_link: str,
) -> str:
    payload = {
        "identities": sorted(identities),
        "database_link": database_link,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "oracle-callable/v1:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class OracleSemanticAdapter:
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
            return self._terminal(call, "unsupported", "unsupported_oracle_callee_kind")

        raw_identity = _identity(call.get("callee_path_components") or ())
        if not raw_identity:
            return self._terminal(
                call, "unsupported", "missing_oracle_structural_callee_path",
            )
        database_link = _normalize_identifier(call.get("database_link"))
        if call.get("database_link") is not None and not database_link:
            return self._terminal(
                call, "unsupported", "invalid_oracle_database_link_structure",
            )

        oracle_facts = _oracle_callable_facts(callables)
        identities: dict[str, tuple[str, ...]] = {}
        for fact in oracle_facts:
            identity = _identity(fact.get("name_path_components") or ())
            if not identity:
                return self._terminal(
                    call, "unsupported", "missing_oracle_callable_name_path",
                )
            identities[str(fact.get("fact_id") or "")] = identity

        caller_fact = next((
            fact for fact in oracle_facts
            if str(fact.get("code_node_id") or "")
            == str(call.get("caller_id") or "")
        ), None)
        caller_identity = (
            identities.get(str(caller_fact.get("fact_id") or ""), ())
            if caller_fact is not None else ()
        )
        call_scope = scope_identity(call.get("scope_path") or ())
        possible_identities = _possible_call_identities(
            raw_identity,
            caller_identity,
            caller_in_package=_package_scope(call_scope) is not None,
        )

        matching = [
            fact for fact in oracle_facts
            if identities[str(fact.get("fact_id") or "")] in possible_identities
        ] if database_link == "" else []

        candidates = tuple(
            SemanticCallableCandidate(
                fact_id=str(fact.get("fact_id") or ""),
                source_id=str(fact.get("source_id") or ""),
                terminal_name=raw_identity[-1],
                role=str(fact.get("role") or ""),
                scope_path=scope_identity(fact.get("scope_path") or ()),
                declaration_point=int(fact.get("declaration_point", -1)),
                type_compatibility=_arity_compatibility(call, fact),
                linkage_visibility="visible",
                linkage_key=_linkage_key(
                    identities[str(fact.get("fact_id") or "")]
                ),
                linkage_scope="project",
                type_key=_type_key(fact),
                code_node_id=str(fact.get("code_node_id") or ""),
                presence_status=str(fact.get("presence_status") or ""),
            )
            for fact in matching
        )
        return LanguageSemanticCandidateSet(
            call_id=str(call.get("call_id") or ""),
            adapter_schema=self.schema,
            language="oracle",
            terminal_name=raw_identity[-1],
            source_id=str(call.get("source_id") or ""),
            call_start=range_tuple(call.get("range") or {})[0],
            scope_path=call_scope,
            candidates=candidates,
            external_identity_namespace=_external_namespace(
                possible_identities, database_link,
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
            language="oracle",
            terminal_name=terminal or "U:UNKNOWN",
            source_id=str(call.get("source_id") or ""),
            call_start=range_tuple(call.get("range") or {})[0],
            scope_path=scope_identity(call.get("scope_path") or ()),
            candidates=(),
            external_identity_namespace="oracle-callable/v1",
            terminal_status=status,
            terminal_reason=reason,
        )
