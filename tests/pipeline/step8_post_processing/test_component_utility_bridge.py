from __future__ import annotations

from graph.schema.graph_edges import Calls
from graph.schema.graph_nodes import CodeNode
from pipeline.steps.step8_post_processing.build_components_step import _cluster
from tests.unittest_function_cases import load_function_cases


def load_tests(_loader, standard_tests, _pattern):
    return load_function_cases(standard_tests, globals())


def _node(node_id: str, stereotype: str = "Helper") -> CodeNode:
    return CodeNode(
        id=node_id,
        signature=f"{node_id}()",
        file_path="sample.c",
        start_line=1,
        end_line=2,
        label="FUNCTION",
        stereotype=stereotype,
    )


def test_cross_cutting_util_does_not_merge_business_components() -> None:
    nodes = [
        _node("code:a"),
        _node("code:a_detail"),
        _node("code:b"),
        _node("code:b_detail"),
        _node("code:string_compare", "Util"),
    ]
    calls = [
        Calls("code:a", "code:a_detail"),
        Calls("code:a", "code:string_compare"),
        Calls("code:string_compare", "code:b"),
        Calls("code:b", "code:b_detail"),
    ]

    clusters = _cluster(nodes, calls)
    member_sets = {frozenset(members) for _, members in clusters}

    assert frozenset({"code:a", "code:a_detail"}) in member_sets
    assert frozenset({"code:b", "code:b_detail"}) in member_sets
    assert all("code:string_compare" not in members for members in member_sets)
