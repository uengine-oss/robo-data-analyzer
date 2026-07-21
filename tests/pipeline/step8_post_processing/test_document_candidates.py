from __future__ import annotations

import hashlib
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from graph.schema.graph_nodes import CodeNode, DocumentNode
from pipeline.steps.step8_post_processing.embed_step import _build_document_candidates, embed_step
from search.corpus.embed_material import FETCH_MATERIAL


def _doc(node_id: str, text: str) -> DocumentNode:
    return DocumentNode(
        node_id, "DOC_CHUNK", "chunk", "", text, "guide.md", 1, 1, 1,
        "file:root", hashlib.sha256(text.encode()).hexdigest(),
    )


class DocumentCandidateTest(unittest.TestCase):
    @patch("pipeline.steps.step8_post_processing.embed_step.embedder.encode_texts")
    @patch("pipeline.steps.step8_post_processing.embed_step.embedder.fingerprint", return_value="fake-v1")
    def test_similarity_creates_candidate_node_only(self, _fingerprint, encode) -> None:
        encode.return_value = [[1.0, 0.0]]
        docs = [_doc("file:doc:1", "주문 저장 처리")]
        material = [{"id": "code:save", "label": "METHOD", "name": "save", "summary": "주문 저장", "rels": []}]
        code_updates = [{"id": "code:save", "vector": [0.99, 0.01]}]
        result = _build_document_candidates(docs, material, code_updates, threshold=0.9)
        self.assertEqual(1, len(result))
        self.assertEqual(("file:doc:1", "code:save", "candidate"), (result[0].source_id, result[0].target_id, result[0].status))
        self.assertIn("semantic similarity", result[0].reason)

    @patch("pipeline.steps.step8_post_processing.embed_step.embedder.encode_texts", return_value=[[1.0, 0.0]])
    def test_below_threshold_is_not_promoted(self, _encode) -> None:
        result = _build_document_candidates(
            [_doc("file:doc:1", "무관한 내용")],
            [{"id": "code:x", "label": "METHOD", "name": "x", "summary": "다름", "rels": []}],
            [{"id": "code:x", "vector": [0.0, 1.0]}],
            threshold=0.9,
        )
        self.assertEqual([], result)

    @patch("pipeline.steps.step8_post_processing.embed_step.embedder.encode_texts", return_value=[[1.0, 0.0]])
    def test_exact_mention_pair_is_not_duplicated_as_candidate(self, _encode) -> None:
        result = _build_document_candidates(
            [_doc("file:doc:1", "saveOrder")],
            [{"id": "code:save", "label": "METHOD", "name": "save", "summary": "주문 저장", "rels": []}],
            [{"id": "code:save", "vector": [1.0, 0.0]}],
            excluded_pairs={("file:doc:1", "code:save")},
        )
        self.assertEqual([], result)


class DocumentCandidateIntegrationTest(unittest.IsolatedAsyncioTestCase):
    @patch("pipeline.steps.step8_post_processing.embed_step.embedder.fingerprint", return_value="fake-v1")
    @patch("pipeline.steps.step8_post_processing.embed_step.embedder.encode_texts")
    @patch(
        "pipeline.steps.step8_post_processing.embed_step.SETTINGS",
        SimpleNamespace(analysis_pipeline=SimpleNamespace(embed_enabled=True)),
    )
    async def test_embed_owner_persists_candidate_without_fact_edge(self, encode, _fingerprint) -> None:
        encode.side_effect = lambda texts: [[1.0, 0.0] for _ in texts]
        material = [{
            "id": "code:save",
            "label": "METHOD",
            "name": "save",
            "stereotype": "",
            "summary": "주문 저장",
            "search_text": "",
            "rels": [],
        }]

        class _Neo:
            def __init__(self) -> None:
                self.batches = []
                self.queries = []

            async def run_read_query(self, query, params=None):
                if query == FETCH_MATERIAL:
                    return material
                if query.startswith("SHOW INDEXES"):
                    return []
                return []

            async def run_batch_unwind(self, query, items):
                self.batches.append((query, items))

            async def run_query(self, query, params=None):
                self.queries.append((query, params))

        code = CodeNode(
            "code:save", "save()", "Order.java", 1, 2, "METHOD",
            name="save", summary="주문 저장",
        )
        neo = _Neo()
        result = await embed_step(
            {
                "code_nodes": [code],
                "document_nodes": [_doc("file:doc:1", "주문 저장 처리")],
                "external_edges": [],
                "tables": [],
                "columns": [],
                "has_column_edges": [],
            },
            {"configurable": {"neo_client": neo}},
        )
        self.assertEqual(1, len(result["candidate_links"]))
        self.assertTrue(any("MERGE (n:CANDIDATE_LINK" in query for query, _ in neo.batches))
        self.assertFalse(any("MENTIONS" in query for query, _ in neo.batches))


if __name__ == "__main__":
    unittest.main()
