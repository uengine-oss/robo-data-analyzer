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


def test_wrapper_target_is_dynamic_without_argument_reparse() -> None:
    caller = _node("code:a.c:run", "run", "a.c")
    result = _classify(
        [_call("wrapper", caller, "mpfm_dlcall")], [caller],
        wrappers=[{"wrapper": "mpfm_dlcall", "callee_from": "first_arg_string"}],
    )

    assert result.edges == []
    assert result.wrapper_unresolved == 1
    assert result.ledger[0]["resolution_status"] == "dynamic"


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
