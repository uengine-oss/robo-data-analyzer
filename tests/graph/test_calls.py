"""Internal-only product CALLS relationship contract tests."""

import asyncio
import unittest

from tests.unittest_function_cases import load_function_cases

from graph.edges.calls import (
    INTERNAL_QUERY,
    CallRelationshipInvariantError,
    emit_calls,
    internal_call_items,
    upsert_calls,
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


def test_storage_and_stream_share_the_internal_population() -> None:
    edge = Calls("dispatch_call", "dispatch_call", recursive=True)

    class Client:
        def __init__(self) -> None:
            self.calls = []

        async def run_batch_unwind(self, query, items) -> None:
            self.calls.append((query, items))

    class Stream:
        def __init__(self) -> None:
            self.events = []

        async def emit_async(self, event) -> None:
            self.events.append(event)

    client = Client()
    stream = Stream()
    asyncio.run(upsert_calls(client, [edge]))  # type: ignore[arg-type]
    asyncio.run(emit_calls(stream, [edge]))

    assert client.calls == [(
        INTERNAL_QUERY,
        [{"caller_id": "dispatch_call", "callee_id": "dispatch_call", "recursive": True}],
    )]
    assert stream.events == [{
        "type": "relationship_event",
        "op": "merge",
        "rel_type": "CALLS",
        "from": {"id": "dispatch_call"},
        "to": {"id": "dispatch_call"},
        "props": {"scope": "internal", "recursive": True},
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
        test_storage_and_stream_share_the_internal_population()


if __name__ == "__main__":
    unittest.main()


def load_tests(_loader, standard_tests, _pattern):
    return load_function_cases(standard_tests, globals())
