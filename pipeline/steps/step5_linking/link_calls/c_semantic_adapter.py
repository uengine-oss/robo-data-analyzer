"""C semantic adapter over ANTLR structural components; never reparses source text."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from pipeline.steps.step5_linking.link_calls.semantic_adapter import (
    LanguageSemanticCandidateSet,
    SemanticCallableCandidate,
    range_tuple,
    scope_identity,
)


_STORAGE_TOKENS = frozenset({
    "Static", "Extern", "Typedef", "Auto", "Register", "ThreadLocal",
})
_NON_TYPE_SPECIFIER_TOKENS = _STORAGE_TOKENS | frozenset({"Inline", "Noreturn"})
_DECLARATOR_RULES = frozenset({
    "declarator", "directDeclarator", "abstractDeclarator", "directAbstractDeclarator",
})


def _all_tokens(component: Mapping[str, Any]):
    for token in component.get("direct_tokens") or ():
        yield token
    for child in component.get("children") or ():
        yield from _all_tokens(child)


def _specifier_tokens(callable_fact: Mapping[str, Any]) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    syntax = callable_fact.get("syntax") or {}
    for component in syntax.get("declaration_specifiers") or ():
        for token in _all_tokens(component):
            kind = str(token.get("token_kind") or "")
            if kind and kind not in _NON_TYPE_SPECIFIER_TOKENS:
                result.append((kind, str(token.get("text") or "")))
    return sorted(result)


def _canonical_component(
    component: Mapping[str, Any],
    *,
    callable_name_range: tuple[int, int],
    parameter_context: bool = False,
) -> Any:
    rule = str(component.get("grammar_rule") or "")
    nested_parameter = parameter_context or rule == "parameterDeclaration"
    tokens: list[tuple[str, str]] = []
    for token in component.get("direct_tokens") or ():
        kind = str(token.get("token_kind") or "")
        coordinate = range_tuple(token.get("range") or {})
        if kind == "Identifier" and (
            coordinate == callable_name_range
            or (nested_parameter and rule in _DECLARATOR_RULES)
        ):
            continue
        tokens.append((kind, str(token.get("text") or "")))
    children = [
        _canonical_component(
            child,
            callable_name_range=callable_name_range,
            parameter_context=nested_parameter,
        )
        for child in component.get("children") or ()
    ]
    return [rule, tokens, children]


def _type_key(callable_fact: Mapping[str, Any]) -> str:
    syntax = callable_fact.get("syntax") or {}
    name_range = range_tuple(callable_fact.get("name_range") or {})
    payload = {
        "specifiers": _specifier_tokens(callable_fact),
        "declarator": _canonical_component(
            syntax.get("declarator") or {}, callable_name_range=name_range,
        ),
        "attributes": [
            _canonical_component(item, callable_name_range=name_range)
            for item in syntax.get("attributes") or ()
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _linkage(callable_fact: Mapping[str, Any]) -> str:
    tokens = {
        str(token.get("token_kind") or "")
        for component in (callable_fact.get("syntax") or {}).get(
            "declaration_specifiers", ()
        )
        for token in _all_tokens(component)
    }
    if "Typedef" in tokens:
        return "unsupported_typedef_callable"
    if "Static" in tokens:
        return "internal"
    return "external"


def _semantic_candidate(
    fact: Mapping[str, Any], *, linkage_visibility: str,
) -> SemanticCallableCandidate:
    linkage = _linkage(fact)
    declaration_point = int(fact.get("declaration_point", -1))
    return SemanticCallableCandidate(
        fact_id=str(fact.get("fact_id") or ""),
        source_id=str(fact.get("source_id") or ""),
        terminal_name=str(fact.get("terminal_name") or ""),
        role=str(fact.get("role") or ""),
        scope_path=scope_identity(fact.get("scope_path") or ()),
        declaration_point=declaration_point,
        type_compatibility="compatible",
        linkage_visibility=linkage_visibility,
        linkage_key=linkage,
        linkage_scope="source" if linkage == "internal" else "project",
        type_key=_type_key(fact),
        code_node_id=str(fact.get("code_node_id") or ""),
        presence_status=str(fact.get("presence_status") or ""),
    )


class CSemanticAdapter:
    schema = "analyzer-c-semantics/v1"

    def adapt(
        self, call, callables, parameter_facts=(), binding_target_facts=(),
        scope_directive_facts=(), import_refs=(), code_nodes=(),
    ):
        del parameter_facts
        del binding_target_facts
        del scope_directive_facts
        del import_refs
        del code_nodes
        call_id = str(call.get("call_id") or "")
        if call.get("callee_kind") == "expression" or call.get("receiver_range"):
            return self._terminal(
                call, "dynamic", "c_runtime_callable_expression",
            )
        if call.get("callee_kind") != "named":
            return self._terminal(
                call, "unsupported", "unsupported_c_callee_kind",
            )
        name = str(call.get("call_text") or "")
        source_id = str(call.get("source_id") or "")
        call_start, _ = range_tuple(call.get("range") or {})
        call_scope = scope_identity(call.get("scope_path") or ())
        matching = [
            fact for fact in callables if fact.get("terminal_name") == name
        ]
        lexically_visible = [
            fact for fact in matching
            if str(fact.get("source_id") or "") == source_id
            and str(fact.get("presence_status") or "") == "active"
            and int(fact.get("declaration_point", -1)) <= call_start
            and _scope_prefix(
                scope_identity(fact.get("scope_path") or ()), call_scope,
            )
        ]
        visible_depth = max(
            (len(fact.get("scope_path") or ()) for fact in lexically_visible),
            default=-1,
        )
        visible_ids = {
            str(fact.get("fact_id") or "")
            for fact in lexically_visible
            if len(fact.get("scope_path") or ()) == visible_depth
        }
        candidates = tuple(
            _semantic_candidate(
                fact,
                linkage_visibility=(
                    "visible"
                    if str(fact.get("fact_id") or "") in visible_ids
                    else "not_visible"
                ),
            )
            for fact in matching
        )
        return LanguageSemanticCandidateSet(
            call_id=call_id,
            adapter_schema=self.schema,
            language="c",
            terminal_name=name,
            source_id=source_id,
            call_start=call_start,
            scope_path=call_scope,
            candidates=candidates,
            external_identity_namespace="c-linkage/v1",
        )

    def _terminal(self, call, status, reason):
        return LanguageSemanticCandidateSet(
            call_id=str(call.get("call_id") or ""),
            adapter_schema=self.schema,
            language="c",
            terminal_name=str(call.get("call_text") or ""),
            source_id=str(call.get("source_id") or ""),
            call_start=range_tuple(call.get("range") or {})[0],
            scope_path=scope_identity(call.get("scope_path") or ()),
            candidates=(),
            external_identity_namespace="c-linkage/v1",
            terminal_status=status,
            terminal_reason=reason,
        )


def _scope_prefix(
    declaration: tuple[tuple[str, int, int], ...],
    call: tuple[tuple[str, int, int], ...],
) -> bool:
    return len(declaration) <= len(call) and declaration == call[:len(declaration)]
