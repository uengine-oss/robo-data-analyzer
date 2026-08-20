from graph.schema.graph_nodes import CodeNode
from pipeline.steps.step5_linking.link_calls.call_classifier import classify_call_sites
import unittest
from tests.unittest_function_cases import load_function_cases


def _fn(node_id: str, name: str, owner_id: str) -> CodeNode:
    return CodeNode(
        id=node_id, name=name, owner_id=owner_id, signature=f"{name}()",
        file_path="review_point.c", start_line=1, end_line=2, label="FUNCTION",
    )


def test_opaque_id_internal_call_uses_name_not_id_shape() -> None:
    caller = _fn("code:mall/review_point.c:caller", "caller", "code:mall/review_point.c")
    callee = _fn(
        "code:mall/review_point.c:apply_point_balance",
        "apply_point_balance", "code:mall/review_point.c",
    )
    result = classify_call_sites([
        _canonical_site("apply", caller.id, callee.name),
    ], [caller, callee])
    assert [(e.callee_id, e.scope) for e in result.edges] == [(callee.id, "internal")]


def test_same_owner_wins_for_duplicate_function_name() -> None:
    caller = _fn("code:mall/a.c:caller", "caller", "code:mall/a.c")
    local = _fn("code:mall/a.c:work", "work", "code:mall/a.c")
    remote = _fn("code:mall/b.c:work", "work", "code:mall/b.c")
    result = classify_call_sites([
        _canonical_site("work", caller.id, "work"),
    ], [caller, remote, local])
    assert result.edges[0].callee_id == local.id


def test_missing_user_function_remains_external() -> None:
    caller = _fn("code:mall/a.c:caller", "caller", "code:mall/a.c")
    result = classify_call_sites([
        _canonical_site("customer", caller.id, "customer_hook"),
    ], [caller])
    assert [(e.callee_id, e.scope) for e in result.edges] == [("customer_hook", "external")]


def test_direct_self_call_carries_explicit_recursive_intent() -> None:
    recursive_function = _fn(
        "code:mall/a.c:walk_tree",
        "walk_tree",
        "code:mall/a.c",
    )
    result = classify_call_sites([
        _canonical_site("recursive", recursive_function.id, recursive_function.name),
    ], [recursive_function])
    assert len(result.edges) == 1
    assert result.edges[0].caller_id == result.edges[0].callee_id
    assert result.edges[0].recursive is True


def test_goto_label_statement_node_is_not_an_internal_callee() -> None:
    # framework 실측(2026-08-05): `goto PFM_CATCH;` 의 GOTO statement 노드가
    # code_nodes 에 합류하면서 PFM_CATCH() 호출이 GOTO 노드로 internal 링크됐다.
    # 기준선 동작 = framework drop. statement 노드는 호출 대상 해석에서 제외한다.
    caller = _fn("code:mall/a.c:caller", "caller", "code:mall/a.c")
    goto_label = CodeNode(
        id="code:mall/a.c:caller:GOTO@305", name="PFM_CATCH",
        owner_id="code:mall/a.c", signature="goto PFM_CATCH",
        file_path="a.c", start_line=305, end_line=305, label="GOTO",
    )
    result = classify_call_sites(
        [_canonical_site("catch", caller.id, "PFM_CATCH")],
        [caller, goto_label],
        framework_names=frozenset({"PFM_*"}),
    )
    assert result.edges == []
    assert result.dropped_framework == 1


def _canonical_site(
    call_id: str,
    caller_id: str,
    name: str,
    *,
    callee_kind: str = "named",
    presence_status: str = "active",
) -> dict:
    return {
        "call_id": call_id,
        "caller_id": caller_id,
        "call_text": name,
        "callee_kind": callee_kind,
        "presence_status": presence_status,
        "file": "sample.c",
        "line": 1,
        "argument_ranges": [],
    }


def test_non_active_expression_and_missing_owner_are_explicitly_accounted() -> None:
    caller = _fn("code:sample.c:run", "run", "code:sample.c")
    sites = [
        _canonical_site("inactive", caller.id, "hidden", presence_status="inactive"),
        _canonical_site("conditional", caller.id, "maybe", presence_status="conditional"),
        _canonical_site("expression", caller.id, "", callee_kind="expression"),
        _canonical_site("owner", "", "outside"),
    ]

    result = classify_call_sites(sites, [caller])

    assert result.edges == []
    assert result.preserved_inactive == 1
    assert result.unresolved_presence == 1
    assert result.unresolved_expression == 1
    assert result.unresolved_owner == 1
    assert {row["call_id"] for row in result.unresolved} == {
        "conditional", "expression", "owner",
    }
    assert [row["call_id"] for row in result.ledger] == [
        "inactive", "conditional", "expression", "owner",
    ]
    assert [row["outcome"] for row in result.ledger] == [
        "preserved", "unresolved", "unresolved", "unresolved",
    ]
    assert result.accounted == len(sites)


def test_wrapper_target_is_not_reparsed_from_source_line() -> None:
    caller = _fn("code:sample.c:run", "run", "code:sample.c")

    class SourceMustNotBeRead:
        def lines(self, _path):
            raise AssertionError("wrapper classification reparsed source")

    wrapper_site = _canonical_site("wrapper", caller.id, "mpfm_dlcall")
    wrapper_site["file"] = "sample.py"
    result = classify_call_sites(
        [wrapper_site],
        [caller],
        wrappers=[{"wrapper": "mpfm_dlcall", "callee_from": "first_arg_string"}],
        source_cache=SourceMustNotBeRead(),
    )

    assert result.edges == []
    assert result.wrapper_unresolved == 1
    assert result.unresolved == [{
        "call_id": "wrapper",
        "reason": "insufficient_wrapper_argument_syntax_evidence",
    }]
    assert result.ledger == [{
        "call_id": "wrapper",
        "outcome": "unresolved",
        "reason": "insufficient_wrapper_argument_syntax_evidence",
    }]


def load_tests(_loader, standard_tests, _pattern):
    return load_function_cases(standard_tests, globals())
