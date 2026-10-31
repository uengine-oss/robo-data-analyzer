"""CALLS edge identity contract tests."""

import asyncio
import unittest

from graph.schema.graph_edges import Calls
from graph.edges.calls import (
    EXTERNAL_PACKAGE_QUERY,
    EXTERNAL_QUERY,
    CallRelationshipInvariantError,
    _split_calls,
    emit_calls,
    upsert_calls,
)


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
        Calls("dispatch_call", "dispatch_call", "internal", recursive=True),
        Calls("dispatch_call", "authorize", "internal"),
    ])
    assert internal[0]["recursive"] is True
    assert internal[1]["recursive"] is False

    class Stream:
        def __init__(self) -> None:
            self.events = []

        async def emit_async(self, event) -> None:
            self.events.append(event)

    stream = Stream()
    asyncio.run(emit_calls(stream, [Calls("dispatch_call", "dispatch_call", "internal", recursive=True)]))
    assert stream.events[0]["props"] == {"scope": "internal", "recursive": True}


def test_self_call_without_recursive_intent_is_rejected() -> None:
    with unittest.TestCase().assertRaises(CallRelationshipInvariantError):
        _split_calls([Calls("dispatch_call", "dispatch_call", "internal")])


def test_recursive_intent_on_different_endpoints_is_rejected() -> None:
    with unittest.TestCase().assertRaises(CallRelationshipInvariantError):
        _split_calls([Calls("dispatch_call", "authorize", "internal", recursive=True)])


def test_unknown_scope_is_rejected_instead_of_silently_dropped() -> None:
    edge = Calls("dispatch_call", "authorize")
    object.__setattr__(edge, "scope", "mystery")
    with unittest.TestCase().assertRaisesRegex(CallRelationshipInvariantError, "unknown CALLS scope"):
        _split_calls([edge])


def test_external_recursive_intent_reaches_storage_and_stream() -> None:
    edge = Calls(
        "code:routine:dispatch", "dispatch",
        scope="external", callee_module="legacy", callee_name="dispatch",
        recursive=True,
    )

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
    asyncio.run(upsert_calls(client, [edge]))  # type: ignore[arg-type]
    assert client.calls == [(EXTERNAL_QUERY, [client.calls[0][1][0]])]
    assert client.calls[0][1][0]["recursive"] is True
    assert "r.recursive = item.recursive" in EXTERNAL_QUERY

    stream = Stream()
    asyncio.run(emit_calls(stream, [edge]))
    call_events = [event for event in stream.events if event.get("rel_type") == "CALLS"]
    assert call_events == [{
        "type": "relationship_event", "op": "merge", "rel_type": "CALLS",
        "from": {"id": "code:routine:dispatch"},
        "to": {"id": "code:external:module:legacy:dispatch"},
        "props": {"scope": "external", "recursive": True},
    }]


def test_external_stub_is_marked_by_property_with_labels_given_at_creation() -> None:
    """Ontological DB: 라벨 = 단일 상속 타입 — ``:EXTERNAL`` 이 FUNCTION·MODULE 두 부모 밑에 올 수 없다."""
    assert "MERGE (callee:ANALYZER_OWNED:FUNCTION {" in EXTERNAL_QUERY
    assert "MERGE (m:ANALYZER_OWNED:MODULE {" in EXTERNAL_QUERY
    assert "callee.external = true" in EXTERNAL_QUERY
    assert "m.external = true" in EXTERNAL_QUERY
    for query in (EXTERNAL_QUERY, EXTERNAL_PACKAGE_QUERY):
        assert ":EXTERNAL" not in query
        assert "FOREACH" not in query
        assert "SET callee:" not in query and "SET m:" not in query and "SET p:" not in query
    assert "MERGE (p:ANALYZER_OWNED:PACKAGE {" in EXTERNAL_PACKAGE_QUERY


def test_external_package_link_is_a_separate_write_only_when_present() -> None:
    with_pkg = Calls("caller-a", "run", "external", callee_module="jobs", callee_package="alpha", callee_name="run")
    without_pkg = Calls("caller-b", "stop", "external", callee_module="jobs", callee_name="stop")

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
    asyncio.run(upsert_calls(client, [with_pkg, without_pkg]))  # type: ignore[arg-type]
    assert [query for query, _ in client.calls] == [EXTERNAL_QUERY, EXTERNAL_PACKAGE_QUERY]
    assert len(client.calls[0][1]) == 2
    assert [item["callee_package"] for item in client.calls[1][1]] == ["code:external:package:alpha"]

    stream = Stream()
    asyncio.run(emit_calls(stream, [with_pkg]))
    stubs = [event for event in stream.events if event["type"] == "node_event" and event["label"] != "PACKAGE"]
    assert [event["labels"] for event in stubs] == [["FUNCTION"], ["MODULE"]]
    assert all(event["props"]["external"] is True for event in stubs)


class CallsContractTest(unittest.TestCase):
    def test_external_stub_property_contract(self) -> None:
        test_external_stub_is_marked_by_property_with_labels_given_at_creation()

    def test_external_package_split(self) -> None:
        test_external_package_link_is_a_separate_write_only_when_present()

    def test_external_ids(self) -> None:
        test_external_stub_ids_are_namespaced_and_opaque()

    def test_external_package_collision(self) -> None:
        test_same_external_module_name_in_different_packages_does_not_collide()

    def test_explicit_recursion(self) -> None:
        test_internal_self_call_is_explicitly_recursive_not_silently_removed()

    def test_invalid_recursion_contracts(self) -> None:
        test_self_call_without_recursive_intent_is_rejected()
        test_recursive_intent_on_different_endpoints_is_rejected()

    def test_unknown_scope_contract(self) -> None:
        test_unknown_scope_is_rejected_instead_of_silently_dropped()

    def test_external_recursion_contract(self) -> None:
        test_external_recursive_intent_reaches_storage_and_stream()


if __name__ == "__main__":
    unittest.main()
