"""Python call boundary without name-based binding guesses.

Python bare names may be rebound by assignments, parameters, imports, ``global``, or
``nonlocal`` declarations. Callable-name equality is therefore not binding evidence.
Until the complete lexical binding environment is present, this adapter closes every
call as an explicit dynamic boundary and creates no CALLS edge.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from graph.schema.graph_nodes import CodeNode

from pipeline.steps.step5_linking.link_calls.semantic_adapter import (
    LanguageSemanticCandidateSet,
    range_tuple,
    scope_identity,
)


class PythonSemanticAdapter:
    schema = "analyzer-python-semantics/v1"

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
        del code_nodes
        del callables
        callee_kind = str(call.get("callee_kind") or "")
        if callee_kind not in {"named", "constructor", "expression"}:
            status = "unsupported"
            reason = "unsupported_python_callee_kind"
        elif call.get("receiver_range") is not None or callee_kind == "expression":
            status = "dynamic"
            reason = "python_runtime_callable_expression"
        else:
            directive = _scope_directive_for_name(call, scope_directive_facts)
            if directive == "global":
                status = "dynamic"
                reason = "python_global_binding"
            elif directive == "nonlocal":
                status = "dynamic"
                reason = "python_nonlocal_binding"
            elif directive == "conflicting":
                status = "unsupported"
                reason = "python_conflicting_scope_directives"
            elif _parameter_masks_name(call, parameter_facts):
                status = "dynamic"
                reason = "python_parameter_binding"
            elif _binding_target_masks_name(call, binding_target_facts):
                status = "dynamic"
                reason = "python_local_binding_target"
            else:
                import_binding = _import_binding_for_name(call, import_refs)
                if import_binding == "named":
                    status = "dynamic"
                    reason = "python_import_binding"
                elif import_binding == "wildcard":
                    status = "dynamic"
                    reason = "python_wildcard_import_binding"
                else:
                    status = "dynamic"
                    reason = "python_name_binding_facts_required"
        return LanguageSemanticCandidateSet(
            call_id=str(call.get("call_id") or ""),
            adapter_schema=self.schema,
            language="python",
            terminal_name=str(call.get("call_text") or ""),
            source_id=str(call.get("source_id") or ""),
            call_start=range_tuple(call.get("range") or {})[0],
            scope_path=scope_identity(call.get("scope_path") or ()),
            candidates=(),
            external_identity_namespace="python-callable/v1",
            terminal_status=status,
            terminal_reason=reason,
        )


def _innermost_binding_scope(
    values: Sequence[Mapping[str, Any]],
) -> tuple[str, int, int] | None:
    scopes = scope_identity(values)
    return next(
        (scope for scope in reversed(scopes) if scope[0] in {"function", "lambda"}),
        None,
    )


def _parameter_masks_name(
    call: Mapping[str, Any],
    parameter_facts: Sequence[Mapping[str, Any]],
) -> bool:
    call_scope = _innermost_binding_scope(call.get("scope_path") or ())
    if call_scope is None:
        return False
    source_id = str(call.get("source_id") or "")
    name = str(call.get("call_text") or "")
    return any(
        str(fact.get("source_id") or "") == source_id
        and str(fact.get("terminal_name") or "") == name
        and _innermost_binding_scope(fact.get("scope_path") or ()) == call_scope
        for fact in parameter_facts
    )


def _scope_directive_for_name(
    call: Mapping[str, Any],
    scope_directive_facts: Sequence[Mapping[str, Any]],
) -> str | None:
    call_scope = _innermost_binding_scope(call.get("scope_path") or ())
    if call_scope is None:
        return None
    source_id = str(call.get("source_id") or "")
    name = str(call.get("call_text") or "")
    kinds = {
        str(fact.get("directive_kind") or "")
        for fact in scope_directive_facts
        if str(fact.get("source_id") or "") == source_id
        and str(fact.get("terminal_name") or "") == name
        and _innermost_binding_scope(fact.get("scope_path") or ()) == call_scope
    }
    if not kinds:
        return None
    if kinds == {"global"}:
        return "global"
    if kinds == {"nonlocal"}:
        return "nonlocal"
    return "conflicting"


_LOCAL_BINDING_CONTEXTS = frozenset({
    "assignment", "annotated_assignment", "augmented_assignment",
    "for_target", "with_target", "except_target",
    "function_definition", "class_definition", "delete_target",
    "comprehension_target",
})


def _binding_target_masks_name(
    call: Mapping[str, Any],
    binding_target_facts: Sequence[Mapping[str, Any]],
) -> bool:
    call_scope = _innermost_binding_scope(call.get("scope_path") or ())
    if call_scope is None:
        return False
    source_id = str(call.get("source_id") or "")
    name = str(call.get("call_text") or "")
    call_scopes = scope_identity(call.get("scope_path") or ())
    for fact in binding_target_facts:
        if (
            str(fact.get("source_id") or "") != source_id
            or str(fact.get("binding_context") or "") not in _LOCAL_BINDING_CONTEXTS
            or name not in _bound_names(fact.get("syntax") or {})
        ):
            continue
        if str(fact.get("binding_context") or "") == "comprehension_target":
            comprehension_scope = _innermost_scope_of_kind(
                fact.get("scope_path") or (), "comprehension",
            )
            if comprehension_scope is not None and comprehension_scope in call_scopes:
                return True
        elif _innermost_binding_scope(fact.get("scope_path") or ()) == call_scope:
            return True
    return False


def _innermost_scope_of_kind(
    values: Sequence[Mapping[str, Any]], kind: str,
) -> tuple[str, int, int] | None:
    return next(
        (scope for scope in reversed(scope_identity(values)) if scope[0] == kind),
        None,
    )


def _bound_names(syntax: Mapping[str, Any]) -> frozenset[str]:
    root = syntax.get("root")
    if not isinstance(root, Mapping):
        return frozenset()
    return frozenset(_names_from_target_component(root))


def _names_from_target_component(component: Mapping[str, Any]) -> tuple[str, ...]:
    """Interpret a validated Python assignment target without reading source text."""
    children_value = component.get("children")
    children = tuple(
        child for child in children_value
        if isinstance(child, Mapping)
    ) if isinstance(children_value, list) else ()
    rule = str(component.get("grammar_rule") or "")
    if rule == "expr" and any(
        str(child.get("grammar_rule") or "") == "trailer" for child in children
    ):
        return ()
    if rule == "name":
        token_values = component.get("direct_tokens")
        if not isinstance(token_values, list):
            return ()
        return tuple(
            str(token.get("text") or "")
            for token in token_values
            if isinstance(token, Mapping)
            and token.get("token_kind") == "NAME"
            and str(token.get("text") or "")
        )
    return tuple(
        name
        for child in children
        for name in _names_from_target_component(child)
    )


def _import_binding_for_name(
    call: Mapping[str, Any],
    import_refs: Sequence[Mapping[str, Any]],
) -> str | None:
    source_id = str(call.get("source_id") or "")
    name = str(call.get("call_text") or "")
    call_scope = _innermost_import_scope(call.get("scope_path") or ())
    wildcard_visible = False
    for ref in import_refs:
        if (
            str(ref.get("source_id") or "") != source_id
            or str(ref.get("evidence_status") or "") != "complete"
            or str(ref.get("presence_status") or "") == "inactive"
        ):
            continue
        import_scope = _innermost_import_scope(ref.get("scope_path") or ())
        if not _import_scope_is_visible(import_scope, call_scope):
            continue
        entries = ref.get("entries")
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            if bool(entry.get("wildcard")):
                wildcard_visible = True
                continue
            if _python_import_bound_name(entry) == name:
                return "named"
    return "wildcard" if wildcard_visible else None


def _innermost_import_scope(
    values: Sequence[Mapping[str, Any]],
) -> tuple[str, int, int] | None:
    scopes = scope_identity(values)
    return next(
        (
            scope for scope in reversed(scopes)
            if scope[0] in {"function", "lambda", "class", "translation_unit"}
        ),
        None,
    )


def _import_scope_is_visible(
    import_scope: tuple[str, int, int] | None,
    call_scope: tuple[str, int, int] | None,
) -> bool:
    if import_scope is None or call_scope is None:
        return False
    if import_scope == call_scope:
        return True
    return import_scope[0] == "translation_unit" and call_scope[0] in {
        "function", "lambda", "class",
    }


def _python_import_bound_name(entry: Mapping[str, Any]) -> str:
    alias = str(entry.get("alias") or "")
    if alias:
        return alias
    import_kind = str(entry.get("import_kind") or "")
    if import_kind == "module":
        components = entry.get("path_components")
        if isinstance(components, list) and components:
            return str(components[0] or "")
        return ""
    if import_kind == "module_member":
        return str(entry.get("member") or "")
    return ""
