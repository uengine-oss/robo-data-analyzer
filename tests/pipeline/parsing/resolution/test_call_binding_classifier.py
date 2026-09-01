"""Formal contract for IR 2.0 adapter-owned semantic resolution (C-080)."""
from __future__ import annotations

import pytest

from graph.schema.graph_nodes import CodeNode
from pipeline.parsing.resolution.call_classifier import (
    CallClassificationInvariantError,
    classify_call_sites,
)
from pipeline.parsing.resolution.call_resolution import (
    resolve_candidate_set,
)
from pipeline.parsing.adapters.call_adapter import (
    LanguageSemanticCandidateSet,
    SemanticCallableCandidate,
    semantic_adapter,
)


def _node(node_id: str, name: str, file_path: str) -> CodeNode:
    return CodeNode(
        id=node_id, name=name, owner_id=f"code:{file_path}",
        signature=f"int {name}(int value)", file_path=file_path,
        start_line=1, end_line=2, label="FUNCTION",
    )


def _range(start: int, length: int) -> dict[str, int]:
    return {"char_offset": start, "char_length": length}


def _scope(source_length: int = 200) -> list[dict]:
    return [{"kind": "translation_unit", "range": _range(0, source_length)}]


def _component(rule: str, tokens: list[tuple[str, str, int]]) -> dict:
    return {
        "grammar_rule": rule,
        "range": _range(min(item[2] for item in tokens), 20),
        "direct_tokens": [
            {"token_kind": kind, "text": text, "range": _range(offset, len(text))}
            for kind, text, offset in tokens
        ],
        "children": [],
    }


def _callable(
    fact_id: str,
    name: str,
    source_id: str,
    *,
    role: str,
    static: bool = False,
    code_node_id: str | None = None,
    declaration_point: int = 30,
) -> dict:
    name_start = 10
    specifiers = [_component("typeSpecifier", [("Int", "int", 4)])]
    if static:
        specifiers.insert(0, _component(
            "storageClassSpecifier", [("Static", "static", 0)],
        ))
    return {
        "fact_id": fact_id,
        "role": role,
        "code_node_id": code_node_id,
        "source_id": source_id,
        "range": _range(0, 40),
        "name_range": _range(name_start, len(name)),
        "terminal_name": name,
        "ast_node_range": _range(0, 40) if role == "definition" else None,
        "scope_path": _scope(),
        "scope_range": _range(0, 200),
        "declaration_point": declaration_point,
        "syntax": {
            "schema": "c-callable-syntax/v1",
            "declaration_specifiers": specifiers,
            "declarator": _component(
                "declarator", [("Identifier", name, name_start)],
            ),
            "attributes": [],
        },
        "grammar_rule": "functionDefinition" if role == "definition" else "declaration",
        "presence_status": "active",
        "presence": {"status": "active"},
    }


def _call(call_id: str, caller: CodeNode, name: str, *, kind: str = "named") -> dict:
    terminal = "" if kind == "expression" else name
    return {
        "call_id": call_id,
        "caller_id": caller.id,
        "owner_status": "resolved",
        "call_text": terminal,
        "callee_kind": kind,
        "source_id": caller.file_path,
        "presence_status": "active",
        "presence": {"status": "active"},
        "file": caller.file_path,
        "line": 2,
        "range": _range(50, 10),
        "callee_range": _range(50, 6),
        "receiver_range": None,
        "arguments": [],
        "scope_path": _scope(),
        "grammar_rule": "postfixExpression",
    }


def _context(*source_ids: str, language: str = "c") -> tuple[list[dict], list[dict]]:
    sources = [
        {
            "source_id": source_id,
            "language": language,
            "frontend_schema": "antlr-c/v1" if language == "c" else "antlr-python/v1",
        }
        for source_id in source_ids
    ]
    completeness = [
        {
            "source_id": source_id, "kind": kind, "status": "complete",
            "population": 1, "emitted": 1, "explicitlyUnresolved": 0,
        }
        for source_id in source_ids for kind in ("call", "callable")
    ]
    return sources, completeness


def _classify(calls, nodes, callables, *source_ids, language: str = "c"):
    sources, completeness = _context(*source_ids, language=language)
    return classify_call_sites(
        calls, nodes, callable_facts=callables,
        evidence_sources=sources, evidence_completeness=completeness,
    )


def test_visible_external_declaration_joins_one_compatible_definition() -> None:
    caller = _node("code:a.c:caller", "caller", "a.c")
    target = _node("code:b.c:target", "target", "b.c")
    result = _classify(
        [_call("call-1", caller, "target")], [caller, target],
        [
            _callable("decl-1", "target", "a.c", role="declaration"),
            _callable(
                "def-1", "target", "b.c", role="definition",
                code_node_id=target.id,
            ),
        ],
        "a.c", "b.c",
    )

    assert [(edge.callee_id, edge.scope) for edge in result.edges] == [
        (target.id, "internal"),
    ]
    assert result.ledger[0]["resolution_status"] == "internal"
    assert result.resolution_counts == {"internal": 1}


def test_language_adapter_emits_candidates_without_final_binding() -> None:
    caller = _node("code:a.c:caller", "caller", "a.c")
    target = _node("code:a.c:target", "target", "a.c")
    callable_fact = _callable(
        "def-candidate", "target", "a.c", role="definition",
        code_node_id=target.id,
    )
    adapter = semantic_adapter("c", "antlr-c/v1")
    assert adapter is not None

    candidate_set = adapter.adapt(
        _call("call-candidate", caller, "target"), [callable_fact],
    )

    assert candidate_set.terminal_status == ""
    assert candidate_set.terminal_reason == ""
    assert [candidate.fact_id for candidate in candidate_set.candidates] == [
        "def-candidate",
    ]
    assert candidate_set.candidates[0].code_node_id == target.id


def test_python_parameter_binding_blocks_same_name_internal_guess() -> None:
    adapter = semantic_adapter("python", "antlr-python/v1")
    assert adapter is not None
    function_scope = [
        {"kind": "translation_unit", "range": _range(0, 200)},
        {"kind": "function", "range": _range(20, 160)},
    ]
    call = {
        "call_id": "python-parameter-call",
        "call_text": "handler",
        "callee_kind": "named",
        "source_id": "sample.py",
        "range": _range(80, 9),
        "receiver_range": None,
        "scope_path": function_scope,
    }
    parameter = {
        "fact_id": "python-parameter",
        "source_id": "sample.py",
        "terminal_name": "handler",
        "range": _range(28, 7),
        "scope_path": function_scope,
    }

    result = adapter.adapt(call, [], [parameter])

    assert result.terminal_status == "dynamic"
    assert result.terminal_reason == "python_parameter_binding"
    assert result.candidates == ()


def _python_name_component(name: str, start: int) -> dict:
    return {
        "grammar_rule": "name",
        "range": _range(start, len(name)),
        "direct_tokens": [{
            "token_kind": "NAME", "text": name,
            "range": _range(start, len(name)),
        }],
        "children": [],
    }


def _python_binding_target(
    name: str,
    function_scope: list[dict],
    *,
    context: str = "assignment",
    attribute_target: bool = False,
) -> dict:
    name_component = _python_name_component(name, 40)
    root = name_component
    if attribute_target:
        root = {
            "grammar_rule": "expr",
            "range": _range(32, 15),
            "direct_tokens": [],
            "children": [
                {
                    "grammar_rule": "atom",
                    "range": _range(32, 3),
                    "direct_tokens": [],
                    "children": [_python_name_component("obj", 32)],
                },
                {
                    "grammar_rule": "trailer",
                    "range": _range(35, 12),
                    "direct_tokens": [{
                        "token_kind": "DOT", "text": ".",
                        "range": _range(35, 1),
                    }],
                    "children": [name_component],
                },
            ],
        }
    return {
        "fact_id": f"python-target-{context}-{attribute_target}",
        "source_id": "sample.py",
        "binding_context": context,
        "scope_path": function_scope,
        "syntax": {"schema": "python-binding-target-syntax/v1", "root": root},
    }


@pytest.mark.parametrize(
    "binding_context", (
        "assignment", "function_definition", "class_definition", "delete_target",
    ),
)
def test_python_local_name_target_blocks_same_name_internal_guess(
    binding_context: str,
) -> None:
    adapter = semantic_adapter("python", "antlr-python/v1")
    assert adapter is not None
    function_scope = [
        {"kind": "translation_unit", "range": _range(0, 200)},
        {"kind": "function", "range": _range(20, 160)},
    ]
    call = {
        "call_id": "python-local-target-call",
        "call_text": "handler",
        "callee_kind": "named",
        "source_id": "sample.py",
        "range": _range(80, 9),
        "receiver_range": None,
        "scope_path": function_scope,
    }

    result = adapter.adapt(
        call, [], [], [_python_binding_target(
            "handler", function_scope, context=binding_context,
        )],
    )

    assert result.terminal_status == "dynamic"
    assert result.terminal_reason == "python_local_binding_target"
    assert result.candidates == ()


@pytest.mark.parametrize(
    ("target", "expected_reason"),
    [
        ("attribute", "python_name_binding_facts_required"),
        ("comprehension", "python_name_binding_facts_required"),
    ],
)
def test_python_nonlocal_target_shapes_do_not_mask_function_name(
    target: str,
    expected_reason: str,
) -> None:
    adapter = semantic_adapter("python", "antlr-python/v1")
    assert adapter is not None
    function_scope = [
        {"kind": "translation_unit", "range": _range(0, 200)},
        {"kind": "function", "range": _range(20, 160)},
    ]
    call = {
        "call_id": f"python-{target}-call",
        "call_text": "handler",
        "callee_kind": "named",
        "source_id": "sample.py",
        "range": _range(80, 9),
        "receiver_range": None,
        "scope_path": function_scope,
    }
    fact = _python_binding_target(
        "handler", function_scope,
        context="comprehension_target" if target == "comprehension" else "assignment",
        attribute_target=target == "attribute",
    )

    result = adapter.adapt(call, [], [], [fact])

    assert result.terminal_status == "dynamic"
    assert result.terminal_reason == expected_reason
    assert result.candidates == ()


def test_python_comprehension_target_masks_body_but_not_outer_iterable() -> None:
    adapter = semantic_adapter("python", "antlr-python/v1")
    assert adapter is not None
    enclosing_scope = [
        {"kind": "translation_unit", "range": _range(0, 240)},
        {"kind": "function", "range": _range(20, 200)},
        {"kind": "block", "range": _range(30, 180)},
    ]
    comprehension_scope = [
        *enclosing_scope,
        {"kind": "comprehension", "range": _range(50, 80)},
    ]
    target = _python_binding_target(
        "handler", comprehension_scope, context="comprehension_target",
    )
    body_call = {
        "call_id": "python-comprehension-body-call",
        "call_text": "handler",
        "callee_kind": "named",
        "source_id": "sample.py",
        "range": _range(55, 9),
        "receiver_range": None,
        "scope_path": comprehension_scope,
    }
    outer_iterable_call = {
        **body_call,
        "call_id": "python-comprehension-outer-iterable-call",
        "range": _range(90, 9),
        "scope_path": enclosing_scope,
    }

    body = adapter.adapt(body_call, [], [], [target])
    outer = adapter.adapt(outer_iterable_call, [], [], [target])

    assert body.terminal_reason == "python_local_binding_target"
    assert outer.terminal_reason == "python_name_binding_facts_required"


@pytest.mark.parametrize(
    ("directive_kind", "expected_reason"),
    [
        ("global", "python_global_binding"),
        ("nonlocal", "python_nonlocal_binding"),
    ],
)
def test_python_scope_directive_overrides_local_assignment_classification(
    directive_kind: str,
    expected_reason: str,
) -> None:
    adapter = semantic_adapter("python", "antlr-python/v1")
    assert adapter is not None
    function_scope = [
        {"kind": "translation_unit", "range": _range(0, 200)},
        {"kind": "function", "range": _range(20, 160)},
    ]
    call = {
        "call_id": f"python-{directive_kind}-call",
        "call_text": "handler",
        "callee_kind": "named",
        "source_id": "sample.py",
        "range": _range(80, 9),
        "receiver_range": None,
        "scope_path": function_scope,
    }
    directive = {
        "fact_id": f"python-directive-{directive_kind}",
        "source_id": "sample.py",
        "terminal_name": "handler",
        "directive_kind": directive_kind,
        "scope_path": function_scope,
    }

    result = adapter.adapt(
        call,
        [],
        [],
        [_python_binding_target("handler", function_scope)],
        [directive],
    )

    assert result.terminal_status == "dynamic"
    assert result.terminal_reason == expected_reason
    assert result.candidates == ()


def test_python_conflicting_scope_directives_fail_closed() -> None:
    adapter = semantic_adapter("python", "antlr-python/v1")
    assert adapter is not None
    function_scope = [
        {"kind": "translation_unit", "range": _range(0, 200)},
        {"kind": "function", "range": _range(20, 160)},
    ]
    call = {
        "call_id": "python-conflicting-directives",
        "call_text": "handler",
        "callee_kind": "named",
        "source_id": "sample.py",
        "range": _range(80, 9),
        "receiver_range": None,
        "scope_path": function_scope,
    }
    directives = [
        {
            "fact_id": f"python-directive-{kind}",
            "source_id": "sample.py",
            "terminal_name": "handler",
            "directive_kind": kind,
            "scope_path": function_scope,
        }
        for kind in ("global", "nonlocal")
    ]

    result = adapter.adapt(call, [], [], [], directives)

    assert result.terminal_status == "unsupported"
    assert result.terminal_reason == "python_conflicting_scope_directives"
    assert result.candidates == ()


def _python_import_ref(
    *,
    import_id: str,
    scope_path: list[dict],
    import_kind: str = "module_member",
    path_components: list[str] | None = None,
    member: str | None = "handler",
    alias: str | None = None,
    wildcard: bool = False,
) -> dict:
    return {
        "import_id": import_id,
        "source_id": "sample.py",
        "evidence_status": "complete",
        "presence_status": "active",
        "scope_path": scope_path,
        "entries": [{
            "entry_ordinal": 0,
            "import_kind": import_kind,
            "path_components": path_components or ["pkg"],
            "member": member,
            "alias": alias,
            "wildcard": wildcard,
        }],
    }


@pytest.mark.parametrize(
    "import_ref",
    [
        _python_import_ref(
            import_id="import-member",
            scope_path=[
                {"kind": "translation_unit", "range": _range(0, 200)},
                {"kind": "function", "range": _range(20, 160)},
            ],
        ),
        _python_import_ref(
            import_id="import-alias",
            scope_path=[
                {"kind": "translation_unit", "range": _range(0, 200)},
                {"kind": "function", "range": _range(20, 160)},
            ],
            member="original", alias="handler",
        ),
        _python_import_ref(
            import_id="import-module",
            scope_path=[{"kind": "translation_unit", "range": _range(0, 200)}],
            import_kind="module", path_components=["handler", "tools"], member=None,
        ),
    ],
)
def test_python_import_binding_uses_structural_entry_and_scope(
    import_ref: dict,
) -> None:
    adapter = semantic_adapter("python", "antlr-python/v1")
    assert adapter is not None
    function_scope = [
        {"kind": "translation_unit", "range": _range(0, 200)},
        {"kind": "function", "range": _range(20, 160)},
    ]
    call = {
        "call_id": "python-import-call",
        "call_text": "handler",
        "callee_kind": "named",
        "source_id": "sample.py",
        "range": _range(80, 9),
        "receiver_range": None,
        "scope_path": function_scope,
    }

    result = adapter.adapt(call, [], [], [], [], [import_ref])

    assert result.terminal_status == "dynamic"
    assert result.terminal_reason == "python_import_binding"
    assert result.candidates == ()


def test_python_import_from_sibling_function_does_not_mask_name() -> None:
    adapter = semantic_adapter("python", "antlr-python/v1")
    assert adapter is not None
    call_scope = [
        {"kind": "translation_unit", "range": _range(0, 300)},
        {"kind": "function", "range": _range(20, 100)},
    ]
    sibling_scope = [
        {"kind": "translation_unit", "range": _range(0, 300)},
        {"kind": "function", "range": _range(150, 100)},
    ]
    call = {
        "call_id": "python-sibling-import-call",
        "call_text": "handler",
        "callee_kind": "named",
        "source_id": "sample.py",
        "range": _range(80, 9),
        "receiver_range": None,
        "scope_path": call_scope,
    }
    import_ref = _python_import_ref(
        import_id="sibling-import", scope_path=sibling_scope,
    )

    result = adapter.adapt(call, [], [], [], [], [import_ref])

    assert result.terminal_reason == "python_name_binding_facts_required"


def test_python_visible_wildcard_import_fails_closed() -> None:
    adapter = semantic_adapter("python", "antlr-python/v1")
    assert adapter is not None
    module_scope = [{"kind": "translation_unit", "range": _range(0, 200)}]
    call = {
        "call_id": "python-wildcard-import-call",
        "call_text": "handler",
        "callee_kind": "named",
        "source_id": "sample.py",
        "range": _range(80, 9),
        "receiver_range": None,
        "scope_path": module_scope,
    }
    import_ref = _python_import_ref(
        import_id="wildcard-import", scope_path=module_scope,
        member=None, wildcard=True,
    )

    result = adapter.adapt(call, [], [], [], [], [import_ref])

    assert result.terminal_reason == "python_wildcard_import_binding"


def test_c_adapter_owns_declaration_order_visibility_fact() -> None:
    caller = _node("code:a.c:caller", "caller", "a.c")
    adapter = semantic_adapter("c", "antlr-c/v1")
    assert adapter is not None

    candidate_set = adapter.adapt(
        _call("call-visibility", caller, "target"),
        [
            _callable(
                "before-call", "target", "a.c", role="declaration",
                declaration_point=30,
            ),
            _callable(
                "after-call", "target", "a.c", role="definition",
                code_node_id="code:a.c:target", declaration_point=70,
            ),
        ],
    )

    assert {
        candidate.fact_id: candidate.linkage_visibility
        for candidate in candidate_set.candidates
    } == {"before-call": "visible", "after-call": "not_visible"}


def test_neutral_core_does_not_reapply_c_declaration_order_rule() -> None:
    candidate = SemanticCallableCandidate(
        fact_id="java-method",
        source_id="Sample.java",
        terminal_name="target",
        role="definition",
        scope_path=(("translation_unit", 0, 200), ("class", 60, 100)),
        declaration_point=70,
        type_compatibility="compatible",
        linkage_visibility="visible",
        linkage_key="Sample#target()",
        linkage_scope="project",
        type_key="java-signature",
        code_node_id="code:Sample.java:target",
        presence_status="active",
    )
    result = resolve_candidate_set(LanguageSemanticCandidateSet(
        call_id="java-call",
        adapter_schema="test-java-semantics/v1",
        language="java",
        terminal_name="target",
        source_id="Sample.java",
        call_start=50,
        scope_path=(("translation_unit", 0, 200),),
        candidates=(candidate,),
        external_identity_namespace="java-linkage/v1",
    ))

    assert result.status == "internal"
    assert result.target_id == "code:Sample.java:target"


def test_same_name_without_visible_declaration_is_external_without_stub_edge() -> None:
    caller = _node("code:a.c:caller", "caller", "a.c")
    same_name = _node("code:b.c:target", "target", "b.c")
    result = _classify(
        [_call("call-2", caller, "target")], [caller, same_name],
        [_callable(
            "def-2", "target", "b.c", role="definition",
            code_node_id=same_name.id,
        )],
        "a.c", "b.c",
    )

    assert result.edges == []
    assert result.ledger[0]["resolution_status"] == "external"
    assert len(result.ledger[0]["external_identity_key"]) == 64


def test_duplicate_compatible_definitions_are_ambiguous_not_first_sorted() -> None:
    caller = _node("code:a.c:caller", "caller", "a.c")
    first = _node("code:b.c:target", "target", "b.c")
    second = _node("code:c.c:target", "target", "c.c")
    result = _classify(
        [_call("call-3", caller, "target")], [caller, first, second],
        [
            _callable("decl-3", "target", "a.c", role="declaration"),
            _callable("def-3a", "target", "b.c", role="definition", code_node_id=first.id),
            _callable("def-3b", "target", "c.c", role="definition", code_node_id=second.id),
        ],
        "a.c", "b.c", "c.c",
    )

    assert result.edges == []
    assert result.ledger[0]["resolution_status"] == "ambiguous"
    assert result.ledger[0]["candidate_fact_ids"] == ["def-3a", "def-3b"]


def test_static_linkage_does_not_cross_translation_units() -> None:
    caller = _node("code:a.c:caller", "caller", "a.c")
    other = _node("code:b.c:target", "target", "b.c")
    result = _classify(
        [_call("call-4", caller, "target")], [caller, other],
        [
            _callable("decl-4", "target", "a.c", role="declaration", static=True),
            _callable(
                "def-4", "target", "b.c", role="definition",
                static=True, code_node_id=other.id,
            ),
        ],
        "a.c", "b.c",
    )

    assert result.edges == []
    assert result.ledger[0]["resolution_status"] == "external"


def test_expression_callee_is_dynamic_without_name_guessing() -> None:
    caller = _node("code:a.c:caller", "caller", "a.c")
    result = _classify(
        [_call("call-5", caller, "", kind="expression")], [caller], [], "a.c",
    )

    assert result.edges == []
    assert result.ledger[0]["resolution_status"] == "dynamic"


def test_unregistered_language_is_unsupported_without_name_fallback() -> None:
    caller = _node("code:a.py:caller", "caller", "a.py")
    same_name = _node("code:b.py:target", "target", "b.py")
    result = _classify(
        [_call("call-6", caller, "target")], [caller, same_name], [],
        "a.py", "b.py", language="python",
    )

    assert result.edges == []
    assert result.ledger[0]["resolution_status"] == "dynamic"
    assert result.ledger[0]["reason"] == "python_name_binding_facts_required"


def test_partial_callable_evidence_fails_closed_at_affected_source() -> None:
    caller = _node("code:a.c:caller", "caller", "a.c")
    sources, completeness = _context("a.c")
    callable_row = next(row for row in completeness if row["kind"] == "callable")
    callable_row.update({
        "status": "partial", "population": 1, "emitted": 0,
        "explicitlyUnresolved": 1,
        "reason": "insufficient_parser_recovery",
        "reasons": ["insufficient_parser_recovery"],
    })
    result = classify_call_sites(
        [_call("call-7", caller, "target")], [caller], callable_facts=[],
        evidence_sources=sources, evidence_completeness=completeness,
    )

    assert result.ledger[0]["resolution_status"] == "unsupported"
    assert result.ledger[0]["reason"] == "insufficient_parser_recovery"


def test_duplicate_call_id_rejects_before_resolution() -> None:
    caller = _node("code:a.c:caller", "caller", "a.c")
    call = _call("duplicate", caller, "target")
    sources, completeness = _context("a.c")

    with pytest.raises(CallClassificationInvariantError, match="exact-once"):
        classify_call_sites(
            [call, dict(call)], [caller], evidence_sources=sources,
            evidence_completeness=completeness,
        )


def test_duplicate_import_fact_id_rejects_before_python_resolution() -> None:
    caller = _node("code:sample.py:caller", "caller", "sample.py")
    call = _call("python-import-exact-once", caller, "handler")
    sources, completeness = _context("sample.py", language="python")
    ref = _python_import_ref(
        import_id="same-import-id", scope_path=_scope(),
    )

    with pytest.raises(CallClassificationInvariantError, match="import evidence IDs"):
        classify_call_sites(
            [call], [caller], import_refs=[ref, dict(ref)],
            evidence_sources=sources, evidence_completeness=completeness,
        )
