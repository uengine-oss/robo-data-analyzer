"""C semantic adapter over ANTLR structural components; never reparses source text."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence


_STORAGE_TOKENS = frozenset({
    "Static", "Extern", "Typedef", "Auto", "Register", "ThreadLocal",
})
_NON_TYPE_SPECIFIER_TOKENS = _STORAGE_TOKENS | frozenset({"Inline", "Noreturn"})
_DECLARATOR_RULES = frozenset({
    "declarator", "directDeclarator", "abstractDeclarator", "directAbstractDeclarator",
})


def _range_tuple(value: Mapping[str, Any]) -> tuple[int, int]:
    start = value.get("char_offset", value.get("charOffset"))
    length = value.get("char_length", value.get("charLength"))
    return int(start), int(length)


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
        coordinate = _range_tuple(token.get("range") or {})
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
    name_range = _range_tuple(callable_fact.get("name_range") or {})
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


def _scope_prefix(
    declaration: Sequence[Mapping[str, Any]],
    call: Sequence[Mapping[str, Any]],
) -> bool:
    if len(declaration) > len(call):
        return False
    return all(
        left.get("kind") == right.get("kind")
        and _range_tuple(left.get("range") or {}) == _range_tuple(right.get("range") or {})
        for left, right in zip(declaration, call, strict=False)
    )


def _identity(name: str, linkage: str, source_id: str = "") -> str:
    value = "\0".join(("c-linkage/v1", linkage, source_id, name))
    return hashlib.sha256(value.encode()).hexdigest()


class CSemanticAdapter:
    schema = "analyzer-c-semantics/v1"

    def resolve(self, call, callables):
        from pipeline.steps.step5_linking.link_calls.semantic_adapter import (
            SemanticResolution,
        )

        call_id = str(call.get("call_id") or "")
        if call.get("callee_kind") == "expression" or call.get("receiver_range"):
            return SemanticResolution(
                call_id, "dynamic", self.schema,
                reason="c_runtime_callable_expression",
            )
        if call.get("callee_kind") != "named":
            return SemanticResolution(
                call_id, "unsupported", self.schema,
                reason="unsupported_c_callee_kind",
            )
        name = str(call.get("call_text") or "")
        source_id = str(call.get("source_id") or "")
        call_start, _ = _range_tuple(call.get("range") or {})
        call_scope = call.get("scope_path") or ()
        visible = [
            fact for fact in callables
            if fact.get("source_id") == source_id
            and fact.get("terminal_name") == name
            and fact.get("presence_status") == "active"
            and int(fact.get("declaration_point", -1)) <= call_start
            and _scope_prefix(fact.get("scope_path") or (), call_scope)
        ]
        if not visible:
            return SemanticResolution(
                call_id, "external", self.schema,
                reason="partial_upload_external_boundary",
                external_identity_key=_identity(name, "external"),
            )
        depth = max(len(fact.get("scope_path") or ()) for fact in visible)
        binders = [fact for fact in visible if len(fact.get("scope_path") or ()) == depth]
        linkages = {_linkage(fact) for fact in binders}
        if linkages == {"unsupported_typedef_callable"}:
            return SemanticResolution(
                call_id, "unsupported", self.schema,
                reason="unsupported_c_typedef_callable",
                candidate_fact_ids=tuple(sorted(str(fact["fact_id"]) for fact in binders)),
            )
        if len(linkages) != 1:
            return SemanticResolution(
                call_id, "ambiguous", self.schema,
                reason="conflicting_c_linkage",
                candidate_fact_ids=tuple(sorted(str(fact["fact_id"]) for fact in binders)),
            )
        linkage = next(iter(linkages))
        type_keys = {_type_key(fact) for fact in binders}
        if len(type_keys) != 1:
            return SemanticResolution(
                call_id, "ambiguous", self.schema,
                reason="conflicting_visible_c_declarations",
                candidate_fact_ids=tuple(sorted(str(fact["fact_id"]) for fact in binders)),
            )
        declaration_id = str(max(
            binders, key=lambda fact: int(fact.get("declaration_point", -1))
        )["fact_id"])
        direct = [fact for fact in binders if fact.get("role") == "definition"]
        if direct:
            return self._internal_or_ambiguous(
                call_id, declaration_id, direct, SemanticResolution,
            )
        expected_type = next(iter(type_keys))
        definitions = [
            fact for fact in callables
            if fact.get("role") == "definition"
            and fact.get("terminal_name") == name
            and fact.get("presence_status") == "active"
            and _linkage(fact) == linkage
            and (linkage != "internal" or fact.get("source_id") == source_id)
        ]
        compatible = [fact for fact in definitions if _type_key(fact) == expected_type]
        if compatible:
            return self._internal_or_ambiguous(
                call_id, declaration_id, compatible, SemanticResolution,
            )
        if definitions:
            return SemanticResolution(
                call_id, "unsupported", self.schema,
                reason="incompatible_c_definition_evidence",
                declaration_fact_id=declaration_id,
                candidate_fact_ids=tuple(sorted(str(fact["fact_id"]) for fact in definitions)),
            )
        return SemanticResolution(
            call_id, "external", self.schema,
            reason="partial_upload_external_boundary",
            external_identity_key=_identity(
                name, linkage, source_id if linkage == "internal" else "",
            ),
            declaration_fact_id=declaration_id,
        )

    def _internal_or_ambiguous(
        self, call_id, declaration_id, definitions, resolution_type,
    ):
        candidates = tuple(sorted(str(fact["fact_id"]) for fact in definitions))
        projected = [fact for fact in definitions if fact.get("code_node_id")]
        if len(definitions) > 1:
            return resolution_type(
                call_id, "ambiguous", self.schema,
                reason="multiple_compatible_c_definitions",
                declaration_fact_id=declaration_id,
                candidate_fact_ids=candidates,
            )
        if len(projected) != 1:
            return resolution_type(
                call_id, "unsupported", self.schema,
                reason="missing_callable_code_node_projection",
                declaration_fact_id=declaration_id,
                candidate_fact_ids=candidates,
            )
        return resolution_type(
            call_id, "internal", self.schema,
            target_id=str(projected[0]["code_node_id"]),
            declaration_fact_id=declaration_id,
            candidate_fact_ids=candidates,
        )
