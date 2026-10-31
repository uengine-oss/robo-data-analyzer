"""External-file graph identity and persistence contracts (spec 078)."""
from __future__ import annotations

import unittest

from graph.edges.external_links import ExternalGraphContractError, upsert_external_edges
from graph.nodes.external import ExternalNodeContractError, upsert_external_nodes
from graph.schema.graph_edges import HasChunk, ReferTo
from graph.schema.graph_nodes import ExternalChunk, ProjectFile
from graph.schema.node_ids import (
    FILE_NS,
    derived_chunk_id,
    external_chunk_id,
    project_file_id,
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


_CHUNK_TEXT = "first:\n  enabled: true\n"


def _chunk(**overrides) -> ExternalChunk:
    file_id = project_file_id("shop", "config/app.yaml")
    values = dict(
        id=external_chunk_id(file_id, 0, len(_CHUNK_TEXT)),
        name="first",
        keys=("first", "first.enabled"),
        semantic_kind="yaml_entry",
        text=_CHUNK_TEXT,
        file_path="config/app.yaml",
        char_start=0,
        char_end=len(_CHUNK_TEXT),
        start_line=1,
        end_line=2,
        provenance="source_span",
        source_hash="b" * 64,
        content_hash="a" * 64,
    )
    values.update(overrides)
    return ExternalChunk(**values)


class ExternalIdentityContractTest(unittest.TestCase):
    def test_file_namespace_and_span_identity(self) -> None:
        left = project_file_id("shop", "config/a.properties")
        right = project_file_id("shop", "config/b.properties")
        self.assertTrue(left.startswith(FILE_NS))
        self.assertNotEqual(external_chunk_id(left, 0, 10), external_chunk_id(right, 0, 10))
        self.assertNotEqual(external_chunk_id(left, 0, 10), external_chunk_id(left, 0, 11))
        self.assertEqual(left, project_file_id("shop", "config\\a.properties"))
        self.assertNotEqual(derived_chunk_id(left, 0), derived_chunk_id(left, 1))
        with self.assertRaises(ValueError):
            external_chunk_id(left, 5, 5)

    def test_absolute_and_root_escape_paths_fail_closed(self) -> None:
        for value in ("../secret.env", "a/../../secret.env", "/etc/passwd", r"C:\\secret.env"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                project_file_id("shop", value)

    def test_chunk_invariants_fail_closed(self) -> None:
        # span 폭과 text 길이 불일치 = 재직렬화 의심 → 거부
        with self.assertRaises(ValueError):
            _chunk(char_end=99)
        # derived 청크는 span을 주장할 수 없다
        with self.assertRaises(ValueError):
            _chunk(provenance="derived_document")
        # 정렬되지 않은 키 거부
        with self.assertRaises(ValueError):
            _chunk(keys=("z", "a"))
        with self.assertRaises(ValueError):
            _chunk(semantic_kind="INJECTED")
        with self.assertRaises(ValueError):
            _chunk(content_hash="short")

    def test_refer_to_requires_matched_key(self) -> None:
        with self.assertRaises(ValueError):
            ReferTo("code:a", "file:x:chunk:0-5", matched_key="  ")


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
        self.assertIn("MERGE (n:ANALYZER_OWNED", client.writes[0][0])
        self.assertIn("MERGE (n:ANALYZER_OWNED:PROJECT_FILE {", client.writes[0][0])
        self.assertIn("graph_owner: 'analyzer'", client.writes[0][0])

    async def test_chunk_write_serializes_keys_and_label(self) -> None:
        client = _RecordingClient()
        await upsert_external_nodes(client, [_chunk()])  # type: ignore[arg-type]
        query, items = client.writes[0]
        self.assertIn("MERGE (n:ANALYZER_OWNED:EXTERNAL_CHUNK {", query)
        self.assertEqual(items[0]["keys"], ["first", "first.enabled"])
        self.assertEqual(items[0]["provenance"], "source_span")

    async def test_unknown_node_type_fails_closed(self) -> None:
        client = _RecordingClient()
        with self.assertRaises(ExternalNodeContractError):
            await upsert_external_nodes(client, [object()])  # type: ignore[arg-type]

    async def test_edge_writes_are_idempotent_and_keep_evidence(self) -> None:
        edge = ReferTo(
            "code:Order.find", "file:shop/app.yaml:chunk:0-22",
            matched_key="first.enabled", evidence_file="Order.java", evidence_line=42,
        )
        client = _RecordingClient()
        await upsert_external_edges(client, [edge])  # type: ignore[arg-type]
        await upsert_external_edges(client, [edge])  # type: ignore[arg-type]
        self.assertEqual(client.writes[0], client.writes[1])
        query, items = client.writes[0]
        self.assertIn("MERGE (source)-[r:REFER_TO]->(target)", query)
        self.assertIn("graph_owner: 'analyzer'", query)
        self.assertEqual(items[0]["matched_key"], "first.enabled")
        self.assertEqual(items[0]["evidence_line"], 42)

    async def test_blank_and_missing_endpoints_fail_closed(self) -> None:
        client = _RecordingClient()
        with self.assertRaises(ValueError):
            await upsert_external_edges(client, [HasChunk("", "file:x")])  # type: ignore[arg-type]

        client = _RecordingClient(matched_ids={"file:source"})
        with self.assertRaisesRegex(ExternalGraphContractError, "missing graph endpoints"):
            await upsert_external_edges(client, [HasChunk("file:source", "file:missing")])  # type: ignore[arg-type]
        self.assertEqual(client.writes, [])


if __name__ == "__main__":
    unittest.main()
