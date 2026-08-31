from __future__ import annotations

from graph.schema.graph_nodes import CodeNode
from pipeline.steps.step5_linking.link_calls.call_classifier import classify_call_sites


def _range(start: int, length: int) -> dict[str, int]:
    return {"char_offset": start, "char_length": length}


def _scope(*, package: bool = False) -> list[dict]:
    values = [{"kind": "translation_unit", "range": _range(0, 500)}]
    if package:
        values.append({"kind": "package", "range": _range(10, 400)})
    values.extend([
        {"kind": "function", "range": _range(40, 300)},
        {"kind": "block", "range": _range(80, 200)},
    ])
    return values


def _node(node_id: str, name: str, *, label: str = "PROCEDURE") -> CodeNode:
    return CodeNode(
        id=node_id,
        name=name,
        signature=name,
        file_path="sample.prc",
        start_line=1,
        end_line=5,
        label=label,
    )


def _component(
    rule: str,
    *,
    text: str = "",
    children: list[dict] | None = None,
) -> dict:
    return {
        "grammar_rule": rule,
        "range": _range(10, max(1, len(text))),
        "direct_tokens": ([{
            "token_kind": "REGULAR_ID",
            "text": text,
            "range": _range(10, len(text)),
        }] if text else []),
        "children": children or [],
    }


def _parameter(type_name: str, *, default: bool = False) -> dict:
    children = [
        _component("parameter_name", text="p_value"),
        _component("type_spec", text=type_name),
    ]
    if default:
        children.append(_component("default_value_part", text="DEFAULT 0"))
    return _component("parameter", children=children)


def _callable(
    fact_id: str,
    target: CodeNode,
    path: list[str],
    *,
    parameter_types: tuple[str, ...] = (),
    default_from: int | None = None,
    package: bool = False,
) -> dict:
    parameters = [
        _parameter(type_name, default=(default_from is not None and index >= default_from))
        for index, type_name in enumerate(parameter_types)
    ]
    return {
        "fact_id": fact_id,
        "source_id": "sample.prc",
        "terminal_name": path[-1],
        "name_path_components": path,
        "role": "definition",
        "scope_path": _scope(package=package)[:-1],
        "declaration_point": 50,
        "syntax": {
            "schema": "oracle-callable-syntax/v1",
            "declaration_specifiers": [],
            "declarator": _component(
                "oracle_callable_declarator", children=parameters,
            ),
            "attributes": [],
        },
        "code_node_id": target.id,
        "presence_status": "active",
        "grammar_rule": "procedure_body" if package else "create_procedure_body",
    }


def _call(
    call_id: str,
    caller: CodeNode,
    path: list[str],
    *,
    argument_count: int = 0,
    database_link: str | None = None,
    package: bool = False,
) -> dict:
    return {
        "call_id": call_id,
        "caller_id": caller.id,
        "source_id": "sample.prc",
        "call_text": path[-1],
        "callee_kind": "named",
        "callee_path_components": path,
        "database_link": database_link,
        "range": _range(100, 20),
        "callee_range": _range(100, 10),
        "receiver_range": _range(100, 4) if len(path) > 1 else None,
        "arguments": [
            _range(112 + index * 2, 1) for index in range(argument_count)
        ],
        "scope_path": _scope(package=package),
        "presence_status": "active",
    }


def _classify(calls: list[dict], nodes: list[CodeNode], callables: list[dict]):
    return classify_call_sites(
        calls,
        nodes,
        callable_facts=callables,
        evidence_sources=[{
            "source_id": "sample.prc",
            "language": "oracle",
            "frontend_schema": "antlr-oracle/v1",
        }],
        evidence_completeness=[
            {
                "source_id": "sample.prc",
                "kind": kind,
                "status": "complete",
                "population": len(calls) if kind == "call" else len(callables),
                "emitted": len(calls) if kind == "call" else len(callables),
                "explicitly_unresolved": 0,
            }
            for kind in ("call", "callable")
        ],
    )


def test_qualified_identifier_and_arity_resolve_from_structural_facts() -> None:
    caller = _node("code:caller", "caller")
    zero = _node("code:app.run:zero", "run")
    one = _node("code:app.run:one", "run")
    callables = [
        _callable("oracle-zero", zero, ["APP", "RUN"]),
        _callable("oracle-one", one, ["app", "run"], parameter_types=("NUMBER",)),
    ]

    result = _classify(
        [_call("oracle-call", caller, ["App", "Run"], argument_count=1)],
        [caller, zero, one],
        callables,
    )

    assert [edge.callee_id for edge in result.edges] == [one.id]
    assert result.ledger[0]["resolution_status"] == "internal"


def test_package_scope_supplies_only_the_structurally_known_prefix() -> None:
    caller = _node("code:app.jobs.run", "run")
    target = _node("code:app.jobs.target", "target")
    callables = [
        _callable(
            "oracle-caller", caller, ["APP", "JOBS", "RUN"], package=True,
        ),
        _callable(
            "oracle-target", target, ["APP", "JOBS", "TARGET"], package=True,
        ),
    ]

    result = _classify(
        [_call("oracle-local", caller, ["target"], package=True)],
        [caller, target],
        callables,
    )

    assert [edge.callee_id for edge in result.edges] == [target.id]


def test_partial_upload_and_database_link_remain_distinct_external_calls() -> None:
    caller = _node("code:caller", "caller")
    local = _classify(
        [_call("oracle-missing", caller, ["APP", "MISSING"])],
        [caller],
        [],
    )
    remote = _classify(
        [_call(
            "oracle-remote", caller, ["APP", "MISSING"],
            database_link="REMOTE_DB",
        )],
        [caller],
        [],
    )

    assert local.ledger[0]["resolution_status"] == "external"
    assert remote.ledger[0]["resolution_status"] == "external"
    assert (
        local.ledger[0]["external_identity_key"]
        != remote.ledger[0]["external_identity_key"]
    )


def test_package_name_alone_cannot_claim_standard_library_identity() -> None:
    caller = _node("code:caller", "caller")
    call = _call(
        "oracle-package-call", caller,
        ["DBMS_OUTPUT", "PUT_LINE"], argument_count=1,
    )

    outside_upload = _classify([call], [caller], [])
    target = _node("code:custom-put-line", "PUT_LINE")
    internal = _classify(
        [call],
        [caller, target],
        [_callable(
            "oracle-custom-put-line", target,
            ["DBMS_OUTPUT", "PUT_LINE"], parameter_types=("VARCHAR2",),
        )],
    )

    assert outside_upload.ledger[0]["resolution_status"] == "external"
    assert outside_upload.ledger[0]["outcome"] == "preserved"
    assert [edge.callee_id for edge in internal.edges] == [target.id]


def test_repeated_occurrences_keep_exact_ledger_and_share_one_product_edge() -> None:
    caller = _node("code:caller", "caller")
    target = _node("code:target", "target")
    fact = _callable("oracle-target", target, ["TARGET"])

    result = _classify(
        [
            _call("oracle-call-1", caller, ["target"]),
            _call("oracle-call-2", caller, ["target"]),
        ],
        [caller, target],
        [fact],
    )

    assert [row["call_id"] for row in result.ledger] == [
        "oracle-call-1", "oracle-call-2",
    ]
    assert len(result.edges) == 1
    assert (result.edges[0].caller_id, result.edges[0].callee_id) == (
        caller.id, target.id,
    )


def test_overload_types_that_structure_cannot_disambiguate_remain_ambiguous() -> None:
    caller = _node("code:caller", "caller")
    number = _node("code:target:number", "target")
    text = _node("code:target:text", "target")

    result = _classify(
        [_call("oracle-overload", caller, ["target"], argument_count=1)],
        [caller, number, text],
        [
            _callable(
                "oracle-number", number, ["TARGET"], parameter_types=("NUMBER",),
            ),
            _callable(
                "oracle-text", text, ["TARGET"], parameter_types=("VARCHAR2",),
            ),
        ],
    )

    assert result.edges == []
    assert result.ledger[0]["resolution_status"] == "ambiguous"
