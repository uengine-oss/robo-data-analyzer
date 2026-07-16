"""CALLS edge identity contract tests."""

import unittest

from graph.schema.graph_edges import Calls
from graph.edges.calls import _split_calls, emit_calls


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


def test_internal_self_call_is_explicitly_recursive_not_silently_removed() -> None:
    internal, _ = _split_calls([
        Calls("dispatch_call", "dispatch_call", "internal"),
        Calls("dispatch_call", "authorize", "internal"),
    ])
    assert internal[0]["recursive"] is True
    assert internal[1]["recursive"] is False

    class Stream:
        def __init__(self) -> None:
            self.events = []

        def emit(self, event) -> None:
            self.events.append(event)

    stream = Stream()
    emit_calls(stream, [Calls("dispatch_call", "dispatch_call", "internal")])
    assert stream.events[0]["props"] == {"recursive": True}


class CallsContractTest(unittest.TestCase):
    def test_external_ids(self) -> None:
        test_external_stub_ids_are_namespaced_and_opaque()

    def test_external_package_collision(self) -> None:
        test_same_external_module_name_in_different_packages_does_not_collide()

    def test_explicit_recursion(self) -> None:
        test_internal_self_call_is_explicitly_recursive_not_silently_removed()


if __name__ == "__main__":
    unittest.main()
