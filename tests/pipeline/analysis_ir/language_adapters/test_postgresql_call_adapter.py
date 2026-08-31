from __future__ import annotations

from graph.schema.graph_nodes import CodeNode
from pipeline.analysis_ir.resolution.call_classifier import classify_call_sites


def _range(start: int, length: int) -> dict[str, int]:
    return {"char_offset": start, "char_length": length}


def _scope() -> list[dict]:
    return [
        {"kind": "translation_unit", "range": _range(0, 500)},
        {"kind": "function", "range": _range(20, 400)},
        {"kind": "block", "range": _range(80, 250)},
    ]


def _node(node_id: str, name: str) -> CodeNode:
    return CodeNode(
        id=node_id,
        name=name,
        signature=name,
        file_path="sample.sql",
        start_line=1,
        end_line=8,
        label="PROCEDURE",
    )


def _component(
    rule: str,
    *,
    tokens: tuple[str, ...] = (),
    children: list[dict] | None = None,
) -> dict:
    return {
        "grammar_rule": rule,
        "range": _range(10, 20),
        "direct_tokens": [
            {
                "token_kind": token,
                "text": token,
                "range": _range(10 + index, 1),
            }
            for index, token in enumerate(tokens)
        ],
        "children": children or [],
    }


def _parameter(
    type_name: str,
    *,
    mode: str = "IN",
    default: bool = False,
) -> dict:
    argument = _component(
        "func_arg",
        tokens=(mode,) if mode else (),
        children=[_component("func_type", tokens=(type_name,))],
    )
    return _component(
        "func_arg_with_default",
        tokens=("DEFAULT",) if default else (),
        children=[argument],
    )


def _callable(
    fact_id: str,
    target: CodeNode,
    path: list[str],
    *,
    callable_kind: str = "FUNCTION",
    parameters: list[dict] | None = None,
) -> dict:
    return {
        "fact_id": fact_id,
        "source_id": "sample.sql",
        "terminal_name": path[-1],
        "name_path_components": path,
        "role": "definition",
        "scope_path": _scope()[:-1],
        "declaration_point": 50,
        "syntax": {
            "schema": "postgresql-callable-syntax/v1",
            "declaration_specifiers": [
                _component("postgresql_callable_kind", tokens=(callable_kind,)),
            ],
            "declarator": _component(
                "postgresql_callable_declarator",
                children=[
                    _component(
                        "func_args_with_defaults",
                        children=parameters or [],
                    ),
                ],
            ),
            "attributes": [],
        },
        "code_node_id": target.id,
        "presence_status": "active",
        "grammar_rule": "createfunctionstmt",
    }


def _call(
    call_id: str,
    caller: CodeNode,
    path: list[str],
    *,
    call_kind: str = "FUNCTION",
    argument_count: int = 0,
) -> dict:
    return {
        "call_id": call_id,
        "caller_id": caller.id,
        "source_id": "sample.sql",
        "call_text": path[-1],
        "callee_kind": "named",
        "callee_path_components": path,
        "range": _range(100, 20),
        "callee_range": _range(100, 10),
        "receiver_range": _range(100, 4) if len(path) > 1 else None,
        "arguments": [
            _range(112 + index * 2, 1) for index in range(argument_count)
        ],
        "scope_path": _scope(),
        "presence_status": "active",
        "grammar_rule": (
            "sqlGenericStmt" if call_kind == "PROCEDURE" else "functionCall"
        ),
    }


def _classify(calls: list[dict], nodes: list[CodeNode], callables: list[dict]):
    return classify_call_sites(
        calls,
        nodes,
        callable_facts=callables,
        evidence_sources=[{
            "source_id": "sample.sql",
            "language": "postgresql",
            "frontend_schema": "antlr-postgresql/v1",
        }],
        evidence_completeness=[
            {
                "source_id": "sample.sql",
                "kind": kind,
                "status": "complete",
                "population": len(calls) if kind == "call" else len(callables),
                "emitted": len(calls) if kind == "call" else len(callables),
                "explicitly_unresolved": 0,
            }
            for kind in ("call", "callable")
        ],
    )


def test_qualified_function_resolves_with_lowercase_identifier_folding() -> None:
    caller = _node("code:caller", "caller")
    target = _node("code:app.run", "run")

    result = _classify(
        [_call("pg-call", caller, ["App", "Run"], argument_count=1)],
        [caller, target],
        [_callable(
            "pg-target", target, ["app", "run"],
            parameters=[_parameter("integer")],
        )],
    )

    assert [edge.callee_id for edge in result.edges] == [target.id]
    assert result.ledger[0]["resolution_status"] == "internal"


def test_function_and_procedure_invocations_do_not_cross_bind() -> None:
    caller = _node("code:caller", "caller")
    function = _node("code:app.work:function", "work")
    procedure = _node("code:app.work:procedure", "work")
    callables = [
        _callable("pg-function", function, ["app", "work"]),
        _callable(
            "pg-procedure", procedure, ["app", "work"],
            callable_kind="PROCEDURE",
        ),
    ]

    function_result = _classify(
        [_call("pg-function-call", caller, ["app", "work"])],
        [caller, function, procedure],
        callables,
    )
    procedure_result = _classify(
        [_call(
            "pg-procedure-call", caller, ["app", "work"],
            call_kind="PROCEDURE",
        )],
        [caller, function, procedure],
        callables,
    )

    assert [edge.callee_id for edge in function_result.edges] == [function.id]
    assert [edge.callee_id for edge in procedure_result.edges] == [procedure.id]


def test_unqualified_call_stays_configuration_dependent_without_search_path() -> None:
    caller = _node("code:caller", "caller")
    target = _node("code:run", "run")

    result = _classify(
        [_call("pg-unqualified", caller, ["run"])],
        [caller, target],
        [_callable("pg-target", target, ["run"])],
    )

    assert result.edges == []
    assert result.ledger[0]["resolution_status"] == "configuration_dependent"
    assert result.ledger[0]["reason"] == "postgresql_search_path_not_sealed"


def test_defaults_and_variadic_parameters_filter_only_impossible_arity() -> None:
    caller = _node("code:caller", "caller")
    defaulted = _node("code:app.target:defaulted", "target")
    variadic = _node("code:app.target:variadic", "target")
    callables = [
        _callable(
            "pg-defaulted", defaulted, ["app", "target"],
            parameters=[
                _parameter("integer"),
                _parameter("text", default=True),
            ],
        ),
        _callable(
            "pg-variadic", variadic, ["app", "target"],
            parameters=[
                _parameter("integer"),
                _parameter("text[]", mode="VARIADIC"),
            ],
        ),
    ]

    one_argument = _classify(
        [_call("pg-one", caller, ["app", "target"], argument_count=1)],
        [caller, defaulted, variadic],
        callables,
    )
    many_arguments = _classify(
        [_call("pg-many", caller, ["app", "target"], argument_count=4)],
        [caller, defaulted, variadic],
        callables,
    )

    assert one_argument.edges == []
    assert one_argument.ledger[0]["resolution_status"] == "ambiguous"
    assert [edge.callee_id for edge in many_arguments.edges] == [variadic.id]


def test_quoted_identifiers_are_case_sensitive() -> None:
    caller = _node("code:caller", "caller")
    exact = _node("code:quoted:exact", "Run")
    different = _node("code:quoted:different", "run")

    result = _classify(
        [_call("pg-quoted", caller, ['"App"', '"Run"'])],
        [caller, exact, different],
        [
            _callable("pg-exact", exact, ['"App"', '"Run"']),
            _callable("pg-different", different, ['"App"', '"run"']),
        ],
    )

    assert [edge.callee_id for edge in result.edges] == [exact.id]


def test_missing_qualified_definition_remains_external_for_partial_upload() -> None:
    caller = _node("code:caller", "caller")
    result = _classify(
        [_call("pg-missing", caller, ["app", "missing"])],
        [caller],
        [],
    )

    assert result.edges == []
    assert result.ledger[0]["resolution_status"] == "external"
    assert result.ledger[0]["outcome"] == "preserved"
