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
        {"caller_id": caller.id, "call_text": callee.name, "file": caller.file_path, "line": 1},
    ], [caller, callee])
    assert [(e.callee_id, e.scope) for e in result.edges] == [(callee.id, "internal")]


def test_same_owner_wins_for_duplicate_function_name() -> None:
    caller = _fn("code:mall/a.c:caller", "caller", "code:mall/a.c")
    local = _fn("code:mall/a.c:work", "work", "code:mall/a.c")
    remote = _fn("code:mall/b.c:work", "work", "code:mall/b.c")
    result = classify_call_sites([
        {"caller_id": caller.id, "call_text": "work", "file": caller.file_path, "line": 1},
    ], [caller, remote, local])
    assert result.edges[0].callee_id == local.id


def test_missing_user_function_remains_external() -> None:
    caller = _fn("code:mall/a.c:caller", "caller", "code:mall/a.c")
    result = classify_call_sites([
        {"caller_id": caller.id, "call_text": "customer_hook", "file": caller.file_path, "line": 1},
    ], [caller])
    assert [(e.callee_id, e.scope) for e in result.edges] == [("customer_hook", "external")]


def test_direct_self_call_carries_explicit_recursive_intent() -> None:
    recursive_function = _fn(
        "code:mall/a.c:walk_tree",
        "walk_tree",
        "code:mall/a.c",
    )
    result = classify_call_sites([
        {
            "caller_id": recursive_function.id,
            "call_text": recursive_function.name,
            "file": recursive_function.file_path,
            "line": 1,
        },
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
        [{"caller_id": caller.id, "call_text": "PFM_CATCH",
          "file": caller.file_path, "line": 10}],
        [caller, goto_label],
        framework_names=frozenset({"PFM_*"}),
    )
    assert result.edges == []
    assert result.dropped_framework == 1


def load_tests(_loader, standard_tests, _pattern):
    return load_function_cases(standard_tests, globals())
