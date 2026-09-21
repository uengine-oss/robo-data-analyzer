"""Java call candidates derived only from ANTLR structural facts.

The closed cases include same-type methods, verified imports, constructors, and receiver
dispatch whose declared field/local/parameter type binds to one selected-source module.
Inheritance, expression receivers, and overload type conversion remain explicit when the
Parser evidence cannot prove one target.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence

from graph.schema.graph_nodes import CodeNode, Variable
from pipeline.parsing.adapters.call_adapter import (
    LanguageSemanticCandidateSet,
    SemanticCallableCandidate,
    range_tuple,
    scope_identity,
)


_SCHEMA = "analyzer-java-semantics/v1"


def _walk_components(component: Mapping[str, Any]):
    yield component
    for child in component.get("children") or ():
        yield from _walk_components(child)


def _parameter_shape(callable_fact: Mapping[str, Any]) -> tuple[int, bool]:
    declarator = (callable_fact.get("syntax") or {}).get("declarator") or {}
    parameters = [
        component for component in _walk_components(declarator)
        if component.get("grammar_rule") == "formalParameter"
    ]
    variable_arity = any(
        nested.get("grammar_rule") == "variableArityParameter"
        for parameter in parameters
        for nested in _walk_components(parameter)
    )
    return len(parameters), variable_arity


def _arity_compatibility(call: Mapping[str, Any], fact: Mapping[str, Any]) -> str:
    if fact.get("grammar_rule") == "compactConstructorDeclaration":
        # A compact record constructor declares its parameters implicitly in the
        # surrounding record header, outside this callable fact's source range.
        # Treat arity as unknown instead of inventing a zero-parameter signature.
        return "unknown"
    supplied = len(call.get("arguments") or ())
    declared, variable_arity = _parameter_shape(fact)
    if variable_arity:
        return "compatible" if supplied >= max(0, declared - 1) else "incompatible"
    return "compatible" if supplied == declared else "incompatible"


def _canonical_component(
    component: Mapping[str, Any], *, callable_name_range: tuple[int, int],
) -> Any:
    rule = str(component.get("grammar_rule") or "")
    tokens: list[tuple[str, str]] = []
    direct = list(component.get("direct_tokens") or ())
    for index, token in enumerate(direct):
        kind = str(token.get("token_kind") or "")
        coordinate = range_tuple(token.get("range") or {})
        is_parameter_name = (
            kind == "Identifier"
            and (
                rule == "variableDeclaratorId"
                or (rule == "variableArityParameter" and index == len(direct) - 1)
            )
        )
        if coordinate == callable_name_range or is_parameter_name:
            continue
        tokens.append((kind, str(token.get("text") or "")))
    return [
        rule,
        tokens,
        [
            _canonical_component(child, callable_name_range=callable_name_range)
            for child in component.get("children") or ()
        ],
    ]


def _type_key(fact: Mapping[str, Any]) -> str:
    syntax = fact.get("syntax") or {}
    name_range = range_tuple(fact.get("name_range") or {})
    payload = {
        "declarator": _canonical_component(
            syntax.get("declarator") or {}, callable_name_range=name_range,
        ),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _innermost_class(
    path: tuple[tuple[str, int, int], ...],
) -> tuple[str, int, int] | None:
    return next((item for item in reversed(path) if item[0] == "class"), None)


def _member_linkage_key(
    owner_id: str,
    terminal_name: str,
) -> str:
    payload = [
        "java-member/v1",
        owner_id,
        terminal_name,
    ]
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _is_static_callable(fact: Mapping[str, Any]) -> bool:
    syntax = fact.get("syntax") or {}
    return any(
        str(token.get("text") or "") == "static"
        for component in syntax.get("declaration_specifiers") or ()
        for nested in _walk_components(component)
        for token in nested.get("direct_tokens") or ()
    )


def _java_type_name(owner_id: str) -> str:
    return owner_id.removeprefix("code:").replace(":", ".").strip(".")


def _java_package_name(owner_id: str) -> str:
    return owner_id.removeprefix("code:").partition(":")[0]


def _java_simple_type_name(owner_id: str) -> str:
    return owner_id.removeprefix("code:").rsplit(":", 1)[-1]


def _visible_static_import_owners(
    call: Mapping[str, Any], import_refs: Sequence[Mapping[str, Any]],
) -> frozenset[str]:
    source_id = str(call.get("source_id") or "")
    terminal_name = str(call.get("call_text") or "")
    owners: set[str] = set()
    for ref in import_refs:
        if (
            str(ref.get("source_id") or "") != source_id
            or ref.get("presence_status") != "active"
        ):
            continue
        for entry in ref.get("entries") or ():
            if entry.get("import_kind") != "static_member":
                continue
            if (
                entry.get("wildcard") is not True
                and str(entry.get("member") or "") != terminal_name
            ):
                continue
            components = [str(value) for value in entry.get("path_components") or ()]
            if components:
                owners.add(".".join(components))
    return frozenset(owners)


def _constructor_type_is_visible(
    call: Mapping[str, Any], candidate_owner_id: str, caller_owner_id: str,
    import_refs: Sequence[Mapping[str, Any]],
) -> bool:
    if not candidate_owner_id:
        return False
    terminal_name = str(call.get("call_text") or "")
    if _java_simple_type_name(candidate_owner_id) != terminal_name:
        return False
    path_components = [
        str(value) for value in call.get("callee_path_components") or ()
        if str(value)
    ]
    if len(path_components) > 1:
        return ".".join(path_components) == _java_type_name(candidate_owner_id)
    if candidate_owner_id == caller_owner_id:
        return True
    if _java_package_name(candidate_owner_id) == _java_package_name(caller_owner_id):
        return True

    source_id = str(call.get("source_id") or "")
    candidate_type = _java_type_name(candidate_owner_id)
    candidate_package = _java_package_name(candidate_owner_id)
    for ref in import_refs:
        if (
            str(ref.get("source_id") or "") != source_id
            or ref.get("presence_status") != "active"
        ):
            continue
        for entry in ref.get("entries") or ():
            components = [str(value) for value in entry.get("path_components") or ()]
            imported_name = ".".join(components)
            if entry.get("import_kind") == "type" and imported_name == candidate_type:
                return True
            if (
                entry.get("import_kind") == "namespace"
                and entry.get("wildcard") is True
                and imported_name == candidate_package
            ):
                return True
    return False


def _visible_java_type_owner(
    type_name: str,
    *,
    source_id: str,
    caller_owner_id: str,
    import_refs: Sequence[Mapping[str, Any]],
    available_owner_ids: frozenset[str],
) -> str:
    """Bind one Parser-owned declared type without reparsing Java source."""
    exact = tuple(
        owner_id for owner_id in available_owner_ids
        if _java_type_name(owner_id) == type_name
    )
    if len(exact) == 1:
        return exact[0]
    if "." in type_name:
        return ""

    caller_package = _java_package_name(caller_owner_id)
    same_package = {
        owner_id for owner_id in available_owner_ids
        if _java_package_name(owner_id) == caller_package
        and _java_simple_type_name(owner_id) == type_name
    }
    imported: set[str] = set()
    for ref in import_refs:
        if (
            str(ref.get("source_id") or "") != source_id
            or ref.get("presence_status") != "active"
        ):
            continue
        for entry in ref.get("entries") or ():
            components = [str(value) for value in entry.get("path_components") or ()]
            imported_name = ".".join(components)
            if entry.get("import_kind") == "type" and components[-1:] == [type_name]:
                imported.update(
                    owner_id for owner_id in available_owner_ids
                    if _java_type_name(owner_id) == imported_name
                )
            if entry.get("import_kind") == "namespace" and entry.get("wildcard") is True:
                imported.update(
                    owner_id for owner_id in available_owner_ids
                    if _java_package_name(owner_id) == imported_name
                    and _java_simple_type_name(owner_id) == type_name
                )
    candidates = same_package | imported
    return next(iter(candidates)) if len(candidates) == 1 else ""


def _receiver_owner(
    call: Mapping[str, Any],
    caller: CodeNode,
    callables: Sequence[Mapping[str, Any]],
    variables: Sequence[Variable],
    import_refs: Sequence[Mapping[str, Any]],
    available_owner_ids: frozenset[str],
) -> tuple[str, bool]:
    receiver = str(call.get("receiver_source") or "")
    source_id = str(call.get("source_id") or "")
    call_line = int(call.get("line") or 0)
    parameter_types: set[str] = set()
    for fact in callables:
        if str(fact.get("code_node_id") or "") != caller.id:
            continue
        declarator = (fact.get("syntax") or {}).get("declarator") or {}
        for component in _walk_components(declarator):
            if component.get("grammar_rule") not in {
                "formalParameter", "variableArityParameter",
            }:
                continue
            names = [
                str(token.get("text") or "")
                for nested in _walk_components(component)
                if nested.get("grammar_rule") == "variableDeclaratorId"
                for token in nested.get("direct_tokens") or ()
                if token.get("token_kind") == "Identifier"
            ]
            types = [
                "".join(
                    str(token.get("text") or "")
                    for nested in _walk_components(type_component)
                    for token in nested.get("direct_tokens") or ()
                )
                for type_component in _walk_components(component)
                if type_component.get("grammar_rule") == "unannType"
            ]
            if names == [receiver] and len(types) == 1 and types[0]:
                parameter_types.add(types[0])
    if parameter_types:
        if len(parameter_types) != 1:
            return "", False
        return _visible_java_type_owner(
            next(iter(parameter_types)), source_id=source_id,
            caller_owner_id=caller.owner_id, import_refs=import_refs,
            available_owner_ids=available_owner_ids,
        ), False

    declarations = [
        variable for variable in variables
        if variable.name == receiver
        and variable.owner_id == caller.owner_id
        and (
            variable.label in {"FIELD", "MEMBER"}
            or (
                variable.label in {"VARIABLE", "LOCAL_VARIABLE"}
                and caller.start_line <= variable.start_line <= call_line
                and call_line <= caller.end_line
            )
        )
        and variable.file_path.replace("\\", "/").endswith(source_id)
        and variable.var_type
    ]
    if declarations:
        local_declarations = [
            variable for variable in declarations
            if variable.label in {"VARIABLE", "LOCAL_VARIABLE"}
        ]
        selected = local_declarations or declarations
        latest_line = max(variable.start_line for variable in selected)
        declared_types = {
            variable.var_type for variable in selected
            if variable.start_line == latest_line
        }
        if len(declared_types) != 1:
            return "", False
        return _visible_java_type_owner(
            next(iter(declared_types)), source_id=source_id,
            caller_owner_id=caller.owner_id, import_refs=import_refs,
            available_owner_ids=available_owner_ids,
        ), False

    owner_id = _visible_java_type_owner(
        receiver, source_id=source_id, caller_owner_id=caller.owner_id,
        import_refs=import_refs, available_owner_ids=available_owner_ids,
    )
    return owner_id, bool(owner_id)


class JavaSemanticAdapter:
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
        variables: Sequence[Variable] = (),
    ) -> LanguageSemanticCandidateSet:
        del parameter_facts
        del binding_target_facts
        del scope_directive_facts
        if call.get("callee_kind") == "expression":
            return self._terminal(call, "dynamic", "java_callable_expression_dispatch")
        if call.get("callee_kind") not in {"named", "constructor"}:
            return self._terminal(call, "unsupported", "unsupported_java_callee_kind")
        is_constructor = call.get("callee_kind") == "constructor"
        has_receiver = call.get("receiver_range") is not None
        receiver_is_this = has_receiver and call.get("receiver_source") == "this"
        name = str(call.get("call_text") or "")
        source_id = str(call.get("source_id") or "")
        call_scope = scope_identity(call.get("scope_path") or ())
        call_class = _innermost_class(call_scope)
        if call_class is None:
            return self._terminal(call, "unsupported", "missing_java_enclosing_type_scope")

        nodes_by_id = {node.id: node for node in code_nodes}
        caller = nodes_by_id.get(str(call.get("caller_id") or ""))
        if caller is None or not caller.owner_id:
            return self._terminal(call, "unsupported", "missing_java_caller_owner")
        available_owner_ids = frozenset(
            node.owner_id for node in code_nodes if node.owner_id
        )
        receiver_owner_id = caller.owner_id if receiver_is_this else ""
        receiver_is_type = False
        if has_receiver and not receiver_is_this and not is_constructor:
            receiver_owner_id, receiver_is_type = _receiver_owner(
                call, caller, callables, variables, import_refs,
                available_owner_ids,
            )
            if not receiver_owner_id:
                return self._terminal(
                    call, "dynamic", "java_receiver_type_binding_environment_required",
                )
        imported_static_owners = _visible_static_import_owners(call, import_refs)
        candidates: list[SemanticCallableCandidate] = []
        for fact in callables:
            if fact.get("terminal_name") != name:
                continue
            if is_constructor and fact.get("grammar_rule") not in {
                "constructorDeclaration", "compactConstructorDeclaration",
            }:
                continue
            candidate_node = nodes_by_id.get(str(fact.get("code_node_id") or ""))
            candidate_owner_id = candidate_node.owner_id if candidate_node is not None else ""
            same_class = bool(candidate_owner_id) and candidate_owner_id == caller.owner_id
            statically_imported = (
                bool(candidate_owner_id)
                and _java_type_name(candidate_owner_id) in imported_static_owners
                and _is_static_callable(fact)
            )
            constructor_visible = is_constructor and _constructor_type_is_visible(
                call, candidate_owner_id, caller.owner_id, import_refs,
            )
            compatibility = _arity_compatibility(call, fact)
            receiver_visible = (
                bool(receiver_owner_id)
                and candidate_owner_id == receiver_owner_id
                and (not receiver_is_type or _is_static_callable(fact))
            )
            candidates.append(SemanticCallableCandidate(
                fact_id=str(fact.get("fact_id") or ""),
                source_id=str(fact.get("source_id") or ""),
                terminal_name=str(fact.get("terminal_name") or ""),
                role=str(fact.get("role") or ""),
                scope_path=scope_identity(fact.get("scope_path") or ()),
                declaration_point=int(fact.get("declaration_point", -1)),
                type_compatibility=compatibility,
                linkage_visibility=(
                    "visible"
                    if constructor_visible or (not is_constructor and (
                        receiver_visible or (not has_receiver and (
                            same_class or statically_imported
                        ))
                    ))
                    else "not_visible"
                ),
                linkage_key=_member_linkage_key(candidate_owner_id, name),
                linkage_scope="project",
                type_key=_type_key(fact),
                code_node_id=str(fact.get("code_node_id") or ""),
                presence_status=str(fact.get("presence_status") or ""),
            ))

        return LanguageSemanticCandidateSet(
            call_id=str(call.get("call_id") or ""),
            adapter_schema=self.schema,
            language="java",
            terminal_name=name,
            source_id=source_id,
            call_start=range_tuple(call.get("range") or {})[0],
            scope_path=call_scope,
            candidates=tuple(candidates),
            external_identity_namespace=(
                f"java-member/v1:{caller.owner_id}"
            ),
        )

    def _terminal(self, call: Mapping[str, Any], status: str, reason: str):
        return LanguageSemanticCandidateSet(
            call_id=str(call.get("call_id") or ""),
            adapter_schema=self.schema,
            language="java",
            terminal_name=str(call.get("call_text") or ""),
            source_id=str(call.get("source_id") or ""),
            call_start=range_tuple(call.get("range") or {})[0],
            scope_path=scope_identity(call.get("scope_path") or ()),
            candidates=(),
            external_identity_namespace="java-callable/v1",
            terminal_status=status,
            terminal_reason=reason,
        )
