"""External-file graph identity and persistence contracts (spec 075 P0)."""
from __future__ import annotations

import unittest

from graph.edges.external_links import ExternalGraphContractError, upsert_external_edges
from graph.nodes.external import upsert_external_nodes
from graph.schema.graph_edges import CallsSql, HasChunk, HasPart
from graph.schema.graph_nodes import ExternalChunk, ProjectFile
from graph.schema.node_ids import (
    FILE_NS,
    candidate_link_id,
    external_chunk_id,
    project_file_id,
    unresolved_ref_id,
)


class _RecordingClient:
    def __init__(self, matched_ids: set[str] | None = None) -> None:
        self.matched_ids = matched_ids
        self.reads: list[tuple[str, dict]] = []
        self.writes: list[tuple[str, list[dict]]] = []

    async def run_read_query(self, query: str, params: dict | None = None) -> list[dict]:
        params = params or {}
        self.reads.append((query, params))
        matched = self.matched_ids if self.matched_ids is not None else set(params["ids"])
        return [{"id": value} for value in sorted(matched)]

    async def run_batch_unwind(self, query: str, items: list[dict]) -> None:
        self.writes.append((query, items))


class ExternalIdentityContractTest(unittest.TestCase):
    def test_file_namespace_and_collision_boundaries(self) -> None:
        left = project_file_id("shop", "config/a.properties")
        right = project_file_id("shop", "config/b.properties")
        self.assertTrue(left.startswith(FILE_NS))
        self.assertNotEqual(external_chunk_id(left, "datasource.main"), external_chunk_id(right, "datasource.main"))
        self.assertEqual(left, project_file_id("shop", "config\\a.properties"))
        self.assertNotEqual(unresolved_ref_id("a", "b"), unresolved_ref_id("a", "c"))
        self.assertNotEqual(candidate_link_id("a", "b", "m1"), candidate_link_id("a", "b", "m2"))

    def test_absolute_and_root_escape_paths_fail_closed(self) -> None:
        for value in ("../secret.env", "a/../../secret.env", "/etc/passwd", r"C:\\secret.env"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                project_file_id("shop", value)

    def test_external_node_label_allowlist_and_value_contracts(self) -> None:
        with self.assertRaises(ValueError):
            ExternalChunk(
                id="file:x:chunk:y", name="x", key="x", kind="sql", label="INJECTED", text="select 1",
                file_path="x.xml", start_line=1, end_line=1, full_len=8, content_hash="a" * 64,
            )
        with self.assertRaises(ValueError):
            ExternalChunk(
                id="file:x:chunk:y", name="x", key="x", kind="sql", label="SQL_STATEMENT", text="x" * 1201,
                file_path="x.xml", start_line=1, end_line=1, full_len=1201, content_hash="a" * 64,
            )


class ExternalWriterContractTest(unittest.IsolatedAsyncioTestCase):
    async def test_node_writes_are_owner_stamped_closed_and_idempotent(self) -> None:
        node = ProjectFile(
            id=project_file_id("shop", "config/app.properties"), name="app.properties", project="shop",
            relpath="config/app.properties", ext=".properties", category="config", status="analyzed",
        )
        client = _RecordingClient()
        await upsert_external_nodes(client, [node])  # type: ignore[arg-type]
        await upsert_external_nodes(client, [node])  # type: ignore[arg-type]
        self.assertEqual(len(client.writes), 2)
        self.assertEqual(client.writes[0], client.writes[1])
        self.assertIn("MERGE (n:PROJECT_FILE", client.writes[0][0])
        self.assertIn("graph_owner: 'analyzer'", client.writes[0][0])

    async def test_edge_writes_are_idempotent_and_keep_evidence(self) -> None:
        edge = CallsSql("code:Order.find", "file:shop/sql.xml:chunk:1", "Order.java", 42, "SQL_ID")
        client = _RecordingClient()
        await upsert_external_edges(client, [edge])  # type: ignore[arg-type]
        await upsert_external_edges(client, [edge])  # type: ignore[arg-type]
        self.assertEqual(client.writes[0], client.writes[1])
        query, items = client.writes[0]
        self.assertIn("MERGE (source)-[r:CALLS_SQL]->(target)", query)
        self.assertIn("graph_owner: 'analyzer'", query)
        self.assertEqual(items[0]["evidence_line"], 42)

    async def test_blank_and_missing_endpoints_fail_closed(self) -> None:
        client = _RecordingClient()
        with self.assertRaises(ValueError):
            await upsert_external_edges(client, [HasChunk("", "file:x")])  # type: ignore[arg-type]

        client = _RecordingClient(matched_ids={"file:source"})
        with self.assertRaisesRegex(ExternalGraphContractError, "missing graph endpoints"):
            await upsert_external_edges(client, [HasPart("file:source", "file:missing", 1)])  # type: ignore[arg-type]
        self.assertEqual(client.writes, [])


if __name__ == "__main__":
    unittest.main()
