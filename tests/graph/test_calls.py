"""CALLS edge identity contract tests."""

from graph.schema.graph_edges import Calls
from graph.edges.calls import _split_calls


def test_external_stub_ids_are_namespaced_and_opaque() -> None:
    _, items = _split_calls([Calls(
        caller_id="code:mall/a.c:caller", callee_id="customer_hook",
        scope="external", callee_module="integration", callee_name="customer_hook",
    )])
    assert items[0]["callee_module"] == "code:external:module:integration"
    assert items[0]["callee_id"] == "code:external:module:integration:customer_hook"


def test_same_external_module_name_in_different_packages_does_not_collide() -> None:
    _, items = _split_calls([
        Calls("caller-a", "run", "external", "jobs", "alpha", "run"),
        Calls("caller-b", "run", "external", "jobs", "beta", "run"),
    ])
    assert items[0]["callee_module"] != items[1]["callee_module"]
