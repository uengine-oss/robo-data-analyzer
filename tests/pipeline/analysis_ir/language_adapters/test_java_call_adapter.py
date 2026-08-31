from __future__ import annotations

from graph.schema.graph_nodes import CodeNode
from pipeline.analysis_ir.resolution.call_classifier import classify_call_sites


def _range(start: int, length: int) -> dict[str, int]:
    return {"char_offset": start, "char_length": length}


def _scope(*, class_start: int) -> list[dict]:
    return [
        {"kind": "translation_unit", "range": _range(0, 500)},
        {"kind": "class", "range": _range(class_start, 200)},
    ]


def _node(
    node_id: str, name: str, *, start: int = 1, owner_id: str = "code:sample:Sample",
) -> CodeNode:
    return CodeNode(
        id=node_id,
        name=name,
        owner_id=owner_id,
        signature=f"void {name}()",
        file_path="Sample.java",
        start_line=start,
        end_line=start + 1,
        label="METHOD",
    )


def _component(rule: str, *, children: list[dict] | None = None) -> dict:
    return {
        "grammar_rule": rule,
        "range": _range(10, 30),
        "direct_tokens": [],
        "children": children or [],
    }


def _token_component(text: str) -> dict:
    return {
        "grammar_rule": "methodModifier",
        "range": _range(5, len(text)),
        "direct_tokens": [{
            "token_kind": "STATIC",
            "text": text,
            "range": _range(5, len(text)),
        }],
        "children": [],
    }


def _callable(
    fact_id: str,
    target: CodeNode,
    *,
    class_start: int = 0,
    parameter_count: int = 0,
    declaration_point: int = 100,
    static: bool = False,
    grammar_rule: str = "methodDeclaration",
    source_id: str = "Sample.java",
) -> dict:
    parameters = [
        _component("formalParameter", children=[
            _component("unannType"),
            _component("variableDeclaratorId"),
        ])
        for _ in range(parameter_count)
    ]
    return {
        "fact_id": fact_id,
        "source_id": source_id,
        "terminal_name": target.name,
        "role": "definition",
        "scope_path": _scope(class_start=class_start),
        "declaration_point": declaration_point,
        "name_range": _range(20, len(target.name)),
        "syntax": {
            "schema": "java-callable-syntax/v1",
            "declaration_specifiers": [_token_component("static")] if static else [],
            "declarator": _component(
                "methodHeader",
                children=[_component("formalParameterList", children=parameters)],
            ),
            "attributes": [],
        },
        "code_node_id": target.id,
        "presence_status": "active",
        "grammar_rule": grammar_rule,
    }


def _call(
    call_id: str,
    caller: CodeNode,
    name: str,
    *,
    class_start: int = 0,
    argument_count: int = 0,
    receiver: bool = False,
    receiver_source: str | None = None,
    callee_kind: str = "named",
    callee_path_components: list[str] | None = None,
) -> dict:
    return {
        "call_id": call_id,
        "caller_id": caller.id,
        "source_id": "Sample.java",
        "call_text": name,
        "callee_kind": callee_kind,
        "range": _range(50, 10),
        "callee_range": _range(50, len(name)),
        "receiver_range": _range(45, 4) if receiver else None,
        "receiver_source": receiver_source,
        "callee_path_components": callee_path_components or [],
        "arguments": [
            {"range": _range(60 + index * 2, 1)}
            for index in range(argument_count)
        ],
        "scope_path": [
            *_scope(class_start=class_start),
            {"kind": "function", "range": _range(30, 100)},
            {"kind": "block", "range": _range(40, 80)},
        ],
        "presence_status": "active",
    }


def _classify(
    call: dict, nodes: list[CodeNode], callables: list[dict],
    *, import_refs: list[dict] | None = None,
):
    return classify_call_sites(
        [call],
        nodes,
        callable_facts=callables,
        import_refs=import_refs or [],
        evidence_sources=[{
            "source_id": "Sample.java",
            "language": "java",
            "frontend_schema": "antlr-java/v1",
        }],
        evidence_completeness=[
            {
                "source_id": "Sample.java",
                "kind": kind,
                "status": "complete",
                "population": 1,
                "emitted": 1,
                "explicitly_unresolved": 0,
            }
            for kind in ("call", "callable")
        ],
    )


def test_unqualified_same_class_method_is_internal_even_when_declared_after_call() -> None:
    caller = _node("code:Sample.java:run", "run")
    target = _node("code:Sample.java:target", "target", start=10)

    result = _classify(
        _call("java-call", caller, "target"),
        [caller, target],
        [_callable("java-target", target, declaration_point=120)],
    )

    assert [(edge.caller_id, edge.callee_id) for edge in result.edges] == [
        (caller.id, target.id),
    ]
    assert result.ledger[0]["resolution_status"] == "internal"


def test_java_arity_filters_incompatible_overload_without_source_reparsing() -> None:
    caller = _node("code:Sample.java:run", "run")
    zero = _node("code:Sample.java:target0", "target", start=10)
    one = _node("code:Sample.java:target1", "target", start=20)

    result = _classify(
        _call("java-overload", caller, "target", argument_count=1),
        [caller, zero, one],
        [
            _callable("java-target-zero", zero, parameter_count=0),
            _callable("java-target-one", one, parameter_count=1),
        ],
    )

    assert [edge.callee_id for edge in result.edges] == [one.id]
    assert result.ledger[0]["resolution_status"] == "internal"


def test_same_name_in_another_class_is_not_guessed_as_internal() -> None:
    caller = _node("code:Sample.java:run", "run")
    other = _node(
        "code:Sample.java:other", "target", start=30,
        owner_id="code:sample:Other",
    )

    result = _classify(
        _call("java-other-class", caller, "target", class_start=0),
        [caller, other],
        [_callable("java-other", other, class_start=250)],
    )

    assert result.edges == []
    assert result.ledger[0]["resolution_status"] == "external"


def test_receiver_call_requires_binding_environment() -> None:
    caller = _node("code:Sample.java:run", "run")
    target = _node("code:Sample.java:target", "target", start=10)
    callable_fact = _callable("java-target", target)

    receiver = _classify(
        _call("java-receiver", caller, "target", receiver=True),
        [caller, target],
        [callable_fact],
    )
    assert receiver.edges == []
    assert receiver.ledger[0]["resolution_status"] == "dynamic"
    assert receiver.ledger[0]["reason"] == "java_receiver_type_binding_environment_required"


def test_this_receiver_resolves_same_type_without_a_type_environment() -> None:
    caller = _node("code:Sample.java:run", "run")
    target = _node("code:Sample.java:target", "target", start=10)

    result = _classify(
        _call(
            "java-this-receiver", caller, "target",
            receiver=True, receiver_source="this",
        ),
        [caller, target],
        [_callable("java-this-target", target)],
    )

    assert [edge.callee_id for edge in result.edges] == [target.id]
    assert result.ledger[0]["resolution_status"] == "internal"


def test_same_type_constructor_is_internal_and_missing_definition_is_external() -> None:
    caller = _node("code:Sample.java:run", "run")
    constructor = _node("code:Sample.java:constructor", "Sample", start=10)
    call = _call("java-constructor", caller, "Sample", callee_kind="constructor")

    internal = _classify(
        call,
        [caller, constructor],
        [_callable(
            "java-constructor-definition", constructor,
            grammar_rule="constructorDeclaration",
        )],
    )
    external = _classify(call, [caller], [])

    assert [edge.callee_id for edge in internal.edges] == [constructor.id]
    assert internal.ledger[0]["resolution_status"] == "internal"
    assert external.edges == []
    assert external.ledger[0]["resolution_status"] == "external"


def test_compact_constructor_keeps_implicit_arity_unknown_without_reparsing() -> None:
    caller = _node("code:Sample.java:run", "run")
    constructor = _node("code:Sample.java:compact", "Sample", start=10)
    call = _call(
        "java-compact-constructor", caller, "Sample",
        callee_kind="constructor", argument_count=1,
    )

    result = _classify(
        call,
        [caller, constructor],
        [_callable(
            "java-compact-definition", constructor,
            grammar_rule="compactConstructorDeclaration",
        )],
    )

    assert [edge.callee_id for edge in result.edges] == [constructor.id]
    assert result.ledger[0]["resolution_status"] == "internal"


def _static_import(
    import_id: str, *, member: str | None, wildcard: bool = False,
) -> dict:
    return {
        "import_id": import_id,
        "source_id": "Sample.java",
        "presence_status": "active",
        "entries": [{
            "import_kind": "static_member",
            "path_components": ["sample", "Utilities"],
            "member": member,
            "wildcard": wildcard,
        }],
    }


def _type_import(
    import_id: str, *, path_components: list[str], wildcard: bool = False,
) -> dict:
    return {
        "import_id": import_id,
        "source_id": "Sample.java",
        "presence_status": "active",
        "entries": [{
            "import_kind": "namespace" if wildcard else "type",
            "path_components": path_components,
            "member": None,
            "wildcard": wildcard,
        }],
    }


def test_explicit_static_import_resolves_only_the_imported_user_method() -> None:
    caller = _node("code:Sample.java:run", "run")
    imported = _node(
        "code:Utilities.java:target", "target", start=10,
        owner_id="code:sample:Utilities",
    )
    result = _classify(
        _call("java-static-import", caller, "target"),
        [caller, imported],
        [_callable(
            "java-imported-target", imported, class_start=250, static=True,
            source_id="Utilities.java",
        )],
        import_refs=[_static_import("java-import", member="target")],
    )

    assert [edge.callee_id for edge in result.edges] == [imported.id]
    assert result.ledger[0]["resolution_status"] == "internal"


def test_static_wildcard_import_resolves_static_but_not_instance_method() -> None:
    caller = _node("code:Sample.java:run", "run")
    static_target = _node(
        "code:Utilities.java:target", "target", start=10,
        owner_id="code:sample:Utilities",
    )
    instance_target = _node(
        "code:Utilities.java:instance", "instance", start=20,
        owner_id="code:sample:Utilities",
    )
    import_ref = _static_import("java-wildcard", member=None, wildcard=True)

    static_result = _classify(
        _call("java-static-wildcard", caller, "target"),
        [caller, static_target, instance_target],
        [_callable(
            "java-static-target", static_target, class_start=250, static=True,
            source_id="Utilities.java",
        )],
        import_refs=[import_ref],
    )
    instance_result = _classify(
        _call("java-instance-wildcard", caller, "instance"),
        [caller, static_target, instance_target],
        [_callable(
            "java-instance-target", instance_target, class_start=250,
            source_id="Utilities.java",
        )],
        import_refs=[import_ref],
    )

    assert [edge.callee_id for edge in static_result.edges] == [static_target.id]
    assert instance_result.edges == []
    assert instance_result.ledger[0]["resolution_status"] == "external"


def test_constructor_visibility_uses_exact_type_and_namespace_imports() -> None:
    caller = _node("code:Sample.java:run", "run")
    constructor = _node(
        "code:external:Target:constructor", "Target", start=10,
        owner_id="code:external:Target",
    )
    fact = _callable(
        "java-external-constructor", constructor, class_start=250,
        grammar_rule="constructorDeclaration",
        source_id="Target.java",
    )
    call = _call("java-imported-constructor", caller, "Target", callee_kind="constructor")

    explicit = _classify(
        call, [caller, constructor], [fact],
        import_refs=[_type_import(
            "java-type-import", path_components=["external", "Target"],
        )],
    )
    wildcard = _classify(
        call, [caller, constructor], [fact],
        import_refs=[_type_import(
            "java-namespace-import", path_components=["external"], wildcard=True,
        )],
    )

    assert [edge.callee_id for edge in explicit.edges] == [constructor.id]
    assert [edge.callee_id for edge in wildcard.edges] == [constructor.id]


def test_qualified_constructor_uses_parser_type_components_without_import_guess() -> None:
    caller = _node("code:Sample.java:run", "run")
    constructor = _node(
        "code:external:Target:constructor", "Target", start=10,
        owner_id="code:external:Target",
    )
    fact = _callable(
        "java-qualified-constructor", constructor, class_start=250,
        grammar_rule="constructorDeclaration", source_id="Target.java",
    )

    exact = _classify(
        _call(
            "java-exact-qualified-constructor", caller, "Target",
            callee_kind="constructor",
            callee_path_components=["external", "Target"],
        ),
        [caller, constructor], [fact],
    )
    wrong = _classify(
        _call(
            "java-wrong-qualified-constructor", caller, "Target",
            callee_kind="constructor",
            callee_path_components=["other", "Target"],
        ),
        [caller, constructor], [fact],
    )

    assert [edge.callee_id for edge in exact.edges] == [constructor.id]
    assert wrong.edges == []
    assert wrong.ledger[0]["resolution_status"] == "external"
