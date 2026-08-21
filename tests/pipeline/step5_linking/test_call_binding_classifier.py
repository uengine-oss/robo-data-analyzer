"""Formal red contract for language-neutral call binding (spec 131 C-067)."""
from __future__ import annotations

from graph.schema.graph_nodes import CodeNode
from pipeline.steps.step5_linking.link_calls.call_classifier import (
    CallClassificationInvariantError,
    classify_call_sites,
)
from tests.unittest_function_cases import load_function_cases


def _node(node_id: str, name: str, file_path: str) -> CodeNode:
    return CodeNode(
        id=node_id,
        name=name,
        owner_id=f"code:{file_path}",
        signature=f"int {name}(int value)",
        file_path=file_path,
        start_line=1,
        end_line=2,
        label="FUNCTION",
    )


def _call(
    call_id: str,
    caller: CodeNode,
    terminal_name: str,
    *,
    status: str,
    resolution_mode: str = "none",
    binding_key: str | None = None,
    compatibility_key: str | None = None,
    compatibility_scope: str | None = None,
    target_scope: str = "corpus",
    dispatch: str = "exact",
    declaration_fact_id: str | None = None,
    external_identity_key: str | None = None,
    candidate_fact_ids: tuple[str, ...] = (),
    reason: str | None = None,
    configuration_id: str | None = None,
) -> dict:
    return {
        "call_id": call_id,
        "caller_id": caller.id,
        "owner_status": "resolved",
        "call_text": terminal_name,
        "callee_kind": "named",
        "source_id": caller.file_path,
        "presence_status": "active",
        "presence": {
            "status": "active",
            "condition": None,
            "configurationId": configuration_id,
            "provenance": "unconditional",
        },
        "file": caller.file_path,
        "line": 1,
        "range": {"char_offset": 10, "char_length": 8},
        "callee_range": {"char_offset": 10, "char_length": 4},
        "argument_ranges": [],
        "grammar_rule": "postfixExpression",
        "binding": {
            "adapter_schema": "fixture-binding/v1",
            "status": status,
            "resolution_mode": resolution_mode,
            "declaration_fact_id": declaration_fact_id,
            "binding_key": binding_key,
            "compatibility_key": compatibility_key,
            "compatibility_scope": compatibility_scope,
            "target_scope": target_scope,
            "dispatch": dispatch,
            "configuration_id": configuration_id,
            "external_identity_key": external_identity_key,
            "candidate_fact_ids": list(candidate_fact_ids),
            "provenance": (
                "unsupported_adapter" if status == "unsupported" else "grammar_scope"
            ),
            "reason": reason,
        },
    }


def _callable(
    fact_id: str,
    *,
    role: str,
    source_id: str,
    binding_key: str,
    compatibility_key: str | None,
    compatibility_status: str,
    compatibility_scope: str,
    definition_status: str,
    target_scope: str = "corpus",
    node: CodeNode | None = None,
    configuration_id: str | None = None,
) -> dict:
    return {
        "fact_id": fact_id,
        "role": role,
        "code_node_id": node.id if node is not None else None,
        "source_id": source_id,
        "adapter_schema": "fixture-binding/v1",
        "binding_key": binding_key,
        "compatibility_key": compatibility_key,
        "compatibility_status": compatibility_status,
        "compatibility_scope": compatibility_scope,
        "definition_status": definition_status,
        "target_scope": target_scope,
        "configuration_id": configuration_id,
        "presence_status": "active",
    }


def _declaration(
    fact_id: str,
    source_id: str,
    binding_key: str,
    compatibility_key: str | None,
    *,
    compatibility_status: str = "exact",
    compatibility_scope: str = "corpus",
    target_scope: str = "corpus",
    configuration_id: str | None = None,
) -> dict:
    return _callable(
        fact_id,
        role="declaration",
        source_id=source_id,
        binding_key=binding_key,
        compatibility_key=compatibility_key,
        compatibility_status=compatibility_status,
        compatibility_scope=compatibility_scope,
        definition_status="not_applicable",
        target_scope=target_scope,
        configuration_id=configuration_id,
    )


def _definition(
    fact_id: str,
    node: CodeNode,
    binding_key: str,
    compatibility_key: str | None,
    *,
    compatibility_status: str = "exact",
    compatibility_scope: str = "corpus",
    target_scope: str = "corpus",
    configuration_id: str | None = None,
) -> dict:
    return _callable(
        fact_id,
        role="definition",
        source_id=node.file_path,
        binding_key=binding_key,
        compatibility_key=compatibility_key,
        compatibility_status=compatibility_status,
        compatibility_scope=compatibility_scope,
        definition_status="exact",
        target_scope=target_scope,
        node=node,
        configuration_id=configuration_id,
    )


def test_compatible_target_uses_only_adapter_owned_opaque_keys() -> None:
    caller = _node("code:a.c:caller", "caller", "a.c")
    target = _node("code:b.c:actual_name", "actual_name", "b.c")
    declaration_id = "c" * 64
    definition_id = "d" * 64
    result = classify_call_sites(
        [_call(
            "call-1",
            caller,
            "different_spelling",
            status="declaration_bound",
            resolution_mode="compatible_definition",
            binding_key="a" * 64,
            compatibility_key="b" * 64,
            compatibility_scope="corpus",
            declaration_fact_id=declaration_id,
        )],
        [caller, target],
        callable_facts=[
            _declaration(
                declaration_id, caller.file_path, "a" * 64, "b" * 64,
            ),
            _definition(definition_id, target, "a" * 64, "b" * 64),
        ],
    )

    assert [(edge.callee_id, edge.scope) for edge in result.edges] == [
        (target.id, "internal"),
    ]
    assert result.ledger[0]["resolution"] == "internal"
    assert result.ledger[0]["declaration_fact_id"] == declaration_id
    assert result.ledger[0]["target_fact_id"] == definition_id


def test_direct_definition_uses_exact_referenced_fact_not_compatible_lookup() -> None:
    caller = _node("code:a.c:caller", "caller", "a.c")
    direct = _node("code:a.c:direct", "direct", "a.c")
    duplicate = _node("code:b.c:duplicate", "duplicate", "b.c")
    direct_id = "1" * 64
    result = classify_call_sites(
        [_call(
            "call-direct",
            caller,
            "different_spelling",
            status="declaration_bound",
            resolution_mode="direct_definition",
            binding_key="2" * 64,
            compatibility_scope="unavailable",
            target_scope="source_file",
            declaration_fact_id=direct_id,
        )],
        [caller, direct, duplicate],
        callable_facts=[
            _definition(
                direct_id,
                direct,
                "2" * 64,
                None,
                compatibility_status="unavailable",
                compatibility_scope="unavailable",
                target_scope="source_file",
            ),
            _definition(
                "3" * 64,
                duplicate,
                "2" * 64,
                None,
                compatibility_status="unavailable",
                compatibility_scope="unavailable",
                target_scope="source_file",
            ),
        ],
    )

    assert [(edge.callee_id, edge.scope) for edge in result.edges] == [
        (direct.id, "internal"),
    ]
    assert result.ledger[0]["target_fact_id"] == direct_id


def test_same_name_without_visible_declaration_is_external_occurrence_only() -> None:
    caller = _node("code:a.c:caller", "caller", "a.c")
    same_name = _node("code:b.c:target", "target", "b.c")
    result = classify_call_sites(
        [_call(
            "call-2",
            caller,
            "target",
            status="external",
            reason="missing_visible_declaration",
        )],
        [caller, same_name],
        callable_facts=[_definition(
            "e" * 64, same_name, "f" * 64, "1" * 64,
        )],
    )

    assert result.edges == []
    assert result.ledger[0]["resolution"] == "external"
    assert result.ledger[0]["reason"] == "missing_visible_declaration"
    assert result.ledger[0]["external_identity_key"] is None


def test_duplicate_compatible_definitions_are_ambiguous_not_first_sorted() -> None:
    caller = _node("code:a.c:caller", "caller", "a.c")
    first = _node("code:b.c:target", "target", "b.c")
    second = _node("code:c.c:target", "target", "c.c")
    declaration_id = "4" * 64
    result = classify_call_sites(
        [_call(
            "call-3",
            caller,
            "target",
            status="declaration_bound",
            resolution_mode="compatible_definition",
            binding_key="2" * 64,
            compatibility_key="3" * 64,
            compatibility_scope="corpus",
            declaration_fact_id=declaration_id,
        )],
        [caller, first, second],
        callable_facts=[
            _declaration(
                declaration_id, caller.file_path, "2" * 64, "3" * 64,
            ),
            _definition("5" * 64, first, "2" * 64, "3" * 64),
            _definition("6" * 64, second, "2" * 64, "3" * 64),
        ],
    )

    assert result.edges == []
    assert result.ledger[0]["resolution"] == "ambiguous"
    assert result.ledger[0]["candidate_fact_ids"] == ["5" * 64, "6" * 64]


def test_source_file_scope_never_crosses_translation_units() -> None:
    caller = _node("code:a.c:caller", "caller", "a.c")
    static_other_file = _node("code:b.c:local", "local", "b.c")
    declaration_id = "9" * 64
    result = classify_call_sites(
        [_call(
            "call-4",
            caller,
            "local",
            status="declaration_bound",
            resolution_mode="compatible_definition",
            binding_key="7" * 64,
            compatibility_key="8" * 64,
            compatibility_scope="source_file",
            target_scope="source_file",
            declaration_fact_id=declaration_id,
        )],
        [caller, static_other_file],
        callable_facts=[
            _declaration(
                declaration_id,
                caller.file_path,
                "7" * 64,
                "8" * 64,
                compatibility_scope="source_file",
                target_scope="source_file",
            ),
            _definition(
                "a" * 64,
                static_other_file,
                "7" * 64,
                "8" * 64,
                compatibility_scope="source_file",
                target_scope="source_file",
            ),
        ],
    )

    assert result.edges == []
    assert result.ledger[0]["resolution"] == "external"


def test_unsupported_adapter_preserves_candidates_without_name_fallback() -> None:
    caller = _node("code:sample.py:caller", "caller", "sample.py")
    same_name = _node("code:other.py:printf", "printf", "other.py")
    candidate_id = "b" * 64
    result = classify_call_sites(
        [_call(
            "call-5",
            caller,
            "printf",
            status="unsupported",
            dispatch="dynamic",
            target_scope="runtime",
            candidate_fact_ids=(candidate_id,),
            reason="insufficient_callable_type_compatibility",
        )],
        [caller, same_name],
        framework_names=frozenset({"print*"}),
        callable_facts=[_declaration(
            candidate_id,
            caller.file_path,
            "c" * 64,
            None,
            compatibility_status="unavailable",
            compatibility_scope="unavailable",
            target_scope="source_file",
        )],
    )

    assert result.edges == []
    assert result.dropped_stdlib == 0
    assert result.dropped_framework == 0
    assert result.ledger[0]["resolution"] == "unsupported"
    assert result.ledger[0]["candidate_fact_ids"] == [candidate_id]


def test_same_opaque_keys_from_different_configurations_never_join() -> None:
    caller = _node("code:a.c:caller", "caller", "a.c")
    target = _node("code:b.c:target", "target", "b.c")
    declaration_id = "1" * 64
    result = classify_call_sites(
        [_call(
            "call-6",
            caller,
            "target",
            status="declaration_bound",
            resolution_mode="compatible_definition",
            binding_key="e" * 64,
            compatibility_key="f" * 64,
            compatibility_scope="configuration",
            declaration_fact_id=declaration_id,
            configuration_id="configuration-a",
        )],
        [caller, target],
        callable_facts=[
            _declaration(
                declaration_id,
                caller.file_path,
                "e" * 64,
                "f" * 64,
                compatibility_scope="configuration",
                configuration_id="configuration-a",
            ),
            _definition(
                "2" * 64,
                target,
                "e" * 64,
                "f" * 64,
                compatibility_scope="configuration",
                configuration_id="configuration-b",
            ),
        ],
    )

    assert result.edges == []
    assert result.ledger[0]["resolution"] == "external"


def test_missing_or_unknown_referenced_callable_fact_fails_closed() -> None:
    caller = _node("code:a.c:caller", "caller", "a.c")
    call = _call(
        "call-invalid",
        caller,
        "target",
        status="declaration_bound",
        resolution_mode="compatible_definition",
        binding_key="a" * 64,
        compatibility_key="b" * 64,
        compatibility_scope="corpus",
        declaration_fact_id="c" * 64,
    )

    try:
        classify_call_sites([call], [caller], callable_facts=[])
    except CallClassificationInvariantError:
        pass
    else:
        raise AssertionError("missing declarationFactId target must fail closed")


def test_missing_binding_never_reenters_legacy_name_resolution() -> None:
    caller = _node("code:a.c:caller", "caller", "a.c")
    same_name = _node("code:b.c:target", "target", "b.c")
    call = _call(
        "call-legacy",
        caller,
        "target",
        status="external",
        reason="missing_visible_declaration",
    )
    del call["binding"]

    try:
        classify_call_sites([call], [caller, same_name], callable_facts=[])
    except CallClassificationInvariantError:
        pass
    else:
        raise AssertionError("missing binding must not fall back to routine name")


def load_tests(_loader, standard_tests, _pattern):
    return load_function_cases(standard_tests, globals())
