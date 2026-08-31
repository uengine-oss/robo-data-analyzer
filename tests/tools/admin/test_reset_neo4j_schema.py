from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from graph.connection_context import Neo4jOverride, get_override, set_override
from graph.publication_schema import SEARCH_SEGMENT_VECTOR_INDEX_NAME
from tools.admin import reset_neo4j_schema as subject


class ResetNeo4jSchemaArgumentTests(unittest.TestCase):
    def test_help_exits_before_any_connection(self) -> None:
        with patch.object(subject, "inspect_databases") as inspect:
            with self.assertRaises(SystemExit) as raised:
                subject.parse_args(["--help"])
        self.assertEqual(0, raised.exception.code)
        inspect.assert_not_called()

    def test_confirmation_is_required_and_database_specific(self) -> None:
        with self.assertRaises(SystemExit):
            subject.parse_args(["--database", "neo4j", "--confirm", "wrong"])

    def test_system_and_unknown_databases_are_rejected(self) -> None:
        for database in ("system", "new-db"):
            with self.subTest(database=database), self.assertRaises(SystemExit):
                subject.parse_args([
                    "--database", database,
                    "--confirm", f"RESET-ANALYZER-SCHEMA:{database}",
                ])


class ResetNeo4jSchemaGateTests(unittest.TestCase):
    def test_inventory_must_match_allowlist_exactly(self) -> None:
        rows = [
            {"name": name, "currentStatus": "online"}
            for name in sorted(subject.EXPECTED_DATABASES - {"test"})
        ]
        with self.assertRaisesRegex(RuntimeError, "inventory gate failed"):
            subject.require_safe_inventory(rows, target="neo4j")

    def test_every_database_must_be_online(self) -> None:
        rows = [
            {
                "name": name,
                "currentStatus": "offline" if name == "framework" else "online",
            }
            for name in sorted(subject.EXPECTED_DATABASES)
        ]
        with self.assertRaisesRegex(RuntimeError, "online-state gate failed"):
            subject.require_safe_inventory(rows, target="neo4j")


class ResetNeo4jSchemaExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self) -> None:
        set_override(None)

    async def test_gate_failure_creates_no_target_client(self) -> None:
        with (
            patch.object(subject, "inspect_databases", new=AsyncMock(return_value=[])),
            patch.object(subject, "Neo4jClient") as client_type,
        ):
            with self.assertRaisesRegex(RuntimeError, "inventory gate failed"):
                await subject.reset_analyzer_schema(database="neo4j")
        client_type.assert_not_called()

    async def test_only_owned_schema_is_dropped_and_override_is_restored(self) -> None:
        rows = [
            {"name": name, "currentStatus": "online"}
            for name in sorted(subject.EXPECTED_DATABASES)
        ]
        foreign_constraint = "catalog_owned_constraint"
        foreign_index = "fabric_owned_index"
        client = MagicMock()
        client.run_read_query = AsyncMock(side_effect=[
            [{"name": subject.ANALYZER_CONSTRAINT_NAMES[0]}, {"name": foreign_constraint}],
            [{"name": SEARCH_SEGMENT_VECTOR_INDEX_NAME}, {"name": foreign_index}],
        ])
        client.run_query = AsyncMock()
        client.close = AsyncMock()
        previous = Neo4jOverride("bolt://previous:7687", "u", "p", "test")
        set_override(previous)

        with (
            patch.object(subject, "inspect_databases", new=AsyncMock(return_value=rows)),
            patch.object(subject, "Neo4jClient", return_value=client),
            patch.object(subject, "ensure_schema", new=AsyncMock()) as ensure,
        ):
            dropped = await subject.reset_analyzer_schema(database="neo4j")

        self.assertEqual((1, 1), dropped)
        statements = [call.args[0] for call in client.run_query.await_args_list]
        self.assertEqual(2, len(statements))
        self.assertTrue(any(subject.ANALYZER_CONSTRAINT_NAMES[0] in q for q in statements))
        self.assertTrue(any(SEARCH_SEGMENT_VECTOR_INDEX_NAME in q for q in statements))
        self.assertFalse(any(foreign_constraint in q for q in statements))
        self.assertFalse(any(foreign_index in q for q in statements))
        ensure.assert_awaited_once_with(client)
        client.close.assert_awaited_once()
        self.assertEqual(previous, get_override())


if __name__ == "__main__":
    unittest.main()
