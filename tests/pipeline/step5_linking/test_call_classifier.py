from __future__ import annotations

from pipeline.steps.step5_linking.link_calls.call_classifier import classify_call_sites
from tests.pipeline.step5_linking.test_call_binding_classifier import (
    _call,
    _callable,
    _context,
    _node,
)


def _classify(calls, nodes, callables=(), *, wrappers=(), framework_names=frozenset()):
    source_ids = sorted({call["source_id"] for call in calls})
    sources, completeness = _context(*source_ids)
    return classify_call_sites(
        calls, nodes, wrappers=wrappers, framework_names=framework_names,
        callable_facts=callables,
        evidence_sources=sources, evidence_completeness=completeness,
    )


def test_direct_definition_carries_explicit_recursive_intent() -> None:
    routine = _node("code:a.c:walk_tree", "walk_tree", "a.c")
    definition = _callable(
        "def-recursive", routine.name, "a.c", role="definition",
        code_node_id=routine.id,
    )
    result = _classify([_call("recursive", routine, routine.name)], [routine], [definition])

    assert len(result.edges) == 1
    assert result.edges[0].callee_id == routine.id
    assert result.edges[0].recursive is True


def test_non_active_expression_and_missing_owner_are_explicitly_accounted() -> None:
    caller = _node("code:a.c:run", "run", "a.c")
    inactive = _call("inactive", caller, "hidden")
    inactive["presence_status"] = "inactive"
    inactive["presence"] = {"status": "inactive"}
    conditional = _call("conditional", caller, "maybe")
    conditional["presence_status"] = "conditional"
    conditional["presence"] = {"status": "conditional"}
    expression = _call("expression", caller, "", kind="expression")
    ownerless = _call("owner", caller, "outside")
    ownerless["caller_id"] = ""
    ownerless["owner_status"] = "unresolved_no_routine"

    result = _classify(
        [inactive, conditional, expression, ownerless], [caller],
    )

    assert [row["resolution_status"] for row in result.ledger] == [
        "inactive", "configuration_dependent", "dynamic", "unsupported",
    ]
    assert result.accounted == 4
    assert result.preserved_inactive == 1
    assert result.unresolved_presence == 1
    assert result.unresolved_expression == 1
    assert result.missing_routine_owner_count == 1


def _argument(
    source: str,
    *,
    syntax_kind: str,
    literal_value: str | None = None,
    identifier: str | None = None,
) -> dict:
    return {
        "range": {"charOffset": 57, "charLength": len(source)},
        "source": source,
        "syntax_kind": syntax_kind,
        "literal_kind": "string" if syntax_kind == "string_literal" else None,
        "literal_value": literal_value,
        "identifier": identifier,
    }


def test_wrapper_string_target_uses_existing_adapter_for_partial_upload_external() -> None:
    caller = _node("code:a.c:run", "run", "a.c")
    call = _call("wrapper", caller, "mpfm_dlcall")
    call["arguments"] = [_argument(
        '"remote_job"', syntax_kind="string_literal", literal_value="remote_job",
    )]
    result = _classify(
        [call], [caller],
        wrappers=[{"wrapper": "mpfm_dlcall", "callee_from": "first_arg_string"}],
        framework_names=frozenset({"mpfm_dlcall"}),
    )

    assert result.edges == []
    assert result.wrapper_unresolved == 0
    assert result.ledger[0]["resolution_status"] == "external"
    assert result.ledger[0]["outcome"] == "preserved"
    assert result.ledger[0]["reason"] == "partial_upload_external_boundary"
    assert result.ledger[0]["external_identity_key"]


def test_wrapper_identifier_target_resolves_internal_definition() -> None:
    caller = _node("code:a.c:run", "run", "a.c")
    target = _node("code:a.c:target_job", "target_job", "a.c")
    definition = _callable(
        "def-target", target.name, "a.c", role="definition",
        code_node_id=target.id,
    )
    call = _call("wrapper", caller, "dispatch")
    call["arguments"] = [_argument(
        "target_job", syntax_kind="identifier", identifier="target_job",
    )]

    result = _classify(
        [call], [caller, target], [definition],
        wrappers=[{"wrapper": "dispatch", "callee_from": "first_arg_identifier"}],
    )

    assert [(edge.caller_id, edge.callee_id) for edge in result.edges] == [
        (caller.id, target.id),
    ]
    assert result.ledger[0]["resolution_status"] == "internal"
    assert result.ledger[0]["outcome"] == "edge"


def test_wrapper_missing_or_mismatched_argument_structure_fails_closed() -> None:
    caller = _node("code:a.c:run", "run", "a.c")
    missing = _call("missing", caller, "mpfm_dlcall")
    mismatched = _call("mismatched", caller, "mpfm_dlcall")
    mismatched["arguments"] = [_argument(
        "target_job", syntax_kind="identifier", identifier="target_job",
    )]

    result = _classify(
        [missing, mismatched], [caller],
        wrappers=[{"wrapper": "mpfm_dlcall", "callee_from": "first_arg_string"}],
    )

    assert result.wrapper_unresolved == 2
    assert [row["reason"] for row in result.ledger] == [
        "runtime_wrapper_argument_missing",
        "runtime_wrapper_string_literal_required",
    ]
    assert all(row["resolution_status"] == "dynamic" for row in result.ledger)


def test_wrapper_and_nested_argument_call_keep_separate_occurrences() -> None:
    caller = _node("code:a.c:run", "run", "a.c")
    wrapper = _call("outer", caller, "dispatch")
    wrapper["arguments"] = [_argument(
        "target_job", syntax_kind="identifier", identifier="target_job",
    )]
    nested = _call("inner", caller, "target_job")

    result = _classify(
        [wrapper, nested], [caller],
        wrappers=[{"wrapper": "dispatch", "callee_from": "first_arg_identifier"}],
    )

    assert [row["call_id"] for row in result.ledger] == ["outer", "inner"]
    assert [row["resolution_status"] for row in result.ledger] == [
        "external", "external",
    ]


def test_duplicate_wrapper_contract_is_rejected() -> None:
    caller = _node("code:a.c:run", "run", "a.c")
    contracts = [
        {"wrapper": "dispatch", "callee_from": "first_arg_identifier"},
        {"wrapper": "dispatch", "callee_from": "first_arg_identifier"},
    ]

    try:
        _classify([_call("call", caller, "dispatch")], [caller], wrappers=contracts)
    except Exception as error:
        assert "duplicate runtime wrapper contract" in str(error)
    else:
        raise AssertionError("duplicate wrapper contract must fail closed")


def test_wrapper_contract_cannot_override_workspace_routine() -> None:
    caller = _node("code:a.c:dispatch", "dispatch", "a.c")
    contracts = [{"wrapper": "dispatch", "callee_from": "first_arg_identifier"}]

    try:
        _classify([_call("call", caller, "dispatch")], [caller], wrappers=contracts)
    except Exception as error:
        assert "conflicts with workspace routine" in str(error)
    else:
        raise AssertionError("workspace routine must outrank wrapper contract")


def test_missing_ir2_identity_never_reenters_name_resolution() -> None:
    caller = _node("code:a.c:caller", "caller", "a.c")
    same_name = _node("code:b.c:target", "target", "b.c")
    result = classify_call_sites(
        [_call("legacy", caller, "target")], [caller, same_name],
    )

    assert result.edges == []
    assert result.ledger[0]["resolution_status"] == "unsupported"
    assert result.ledger[0]["reason"] == "missing_evidence_source_identity"


def test_verified_framework_contract_excludes_runtime_call_without_graph_edge() -> None:
    caller = _node("code:a.c:run", "run", "a.c")
    result = _classify(
        [_call("framework", caller, "PFM_MSGE")], [caller],
        framework_names=frozenset({"PFM_*"}),
    )

    assert result.edges == []
    assert result.dropped_framework == 1
    assert result.ledger[0]["resolution_status"] == "framework_provided"
    assert result.ledger[0]["outcome"] == "excluded"


def test_framework_contract_cannot_exclude_workspace_routine() -> None:
    caller = _node("code:a.c:PFM_LOCAL", "PFM_LOCAL", "a.c")

    try:
        _classify(
            [_call("local", caller, "PFM_LOCAL")], [caller],
            framework_names=frozenset({"PFM_*"}),
        )
    except Exception as error:
        assert "conflicts with workspace routine" in str(error)
    else:
        raise AssertionError("workspace routine conflict must fail closed")
