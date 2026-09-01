"""Internal-only product CALLS relationship contract tests."""

import asyncio
import unittest

from tests.unittest_function_cases import load_function_cases

from graph.edges.call_edges import (
    CallRelationshipInvariantError,
    emit_calls,
    internal_call_items,
)
from graph.schema.graph_edges import Calls


def test_internal_self_call_is_explicitly_recursive() -> None:
    items = internal_call_items([
        Calls("dispatch_call", "dispatch_call", recursive=True),
        Calls("dispatch_call", "authorize"),
    ])
    assert items[0]["recursive"] is True
    assert items[1]["recursive"] is False


def test_self_call_without_recursive_intent_is_rejected() -> None:
    with unittest.TestCase().assertRaises(CallRelationshipInvariantError):
        internal_call_items([Calls("dispatch_call", "dispatch_call")])


def test_recursive_intent_on_different_endpoints_is_rejected() -> None:
    with unittest.TestCase().assertRaises(CallRelationshipInvariantError):
        internal_call_items([Calls("dispatch_call", "authorize", recursive=True)])


def test_non_internal_scope_is_rejected_at_product_boundary() -> None:
    edge = Calls("dispatch_call", "authorize")
    object.__setattr__(edge, "scope", "external")
    with unittest.TestCase().assertRaisesRegex(
        CallRelationshipInvariantError, "only proven internal",
    ):
        internal_call_items([edge])


def test_stream_uses_the_final_minimal_calls_shape() -> None:
    edge = Calls("dispatch_call", "dispatch_call", recursive=True)

    class Stream:
        def __init__(self) -> None:
            self.events = []

        async def emit_async(self, event) -> None:
            self.events.append(event)

    stream = Stream()
    asyncio.run(emit_calls(stream, [edge]))

    assert stream.events == [{
        "type": "relationship_event",
        "op": "merge",
        "rel_type": "CALLS",
        "from": {"id": "dispatch_call"},
        "to": {"id": "dispatch_call"},
        "props": {},
    }]


class CallsContractTest(unittest.TestCase):
    def test_explicit_recursion(self) -> None:
        test_internal_self_call_is_explicitly_recursive()

    def test_invalid_recursion_contracts(self) -> None:
        test_self_call_without_recursive_intent_is_rejected()
        test_recursive_intent_on_different_endpoints_is_rejected()

    def test_internal_boundary(self) -> None:
        test_non_internal_scope_is_rejected_at_product_boundary()

    def test_storage_stream_parity(self) -> None:
        test_stream_uses_the_final_minimal_calls_shape()


if __name__ == "__main__":
    unittest.main()


def load_tests(_loader, standard_tests, _pattern):
    return load_function_cases(standard_tests, globals())
