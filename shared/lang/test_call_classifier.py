from graph.schema.graph_nodes import CodeNode
from shared.lang.call_classifier import classify_call_sites


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
