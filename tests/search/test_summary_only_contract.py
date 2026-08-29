"""Semantic retrieval scores the canonical ``summary`` and no duplicate text field."""
from __future__ import annotations

import unittest

from search.corpus.corpus import Corpus, _CORPUS_QUERY
from search.corpus.embed_material import compose_embed_text
from search.ranking.recall import _structural
from search.ranking.rerank import passage


def _row(node_id: str, summary: str, **overrides) -> dict:
    row = {
        "id": node_id,
        "label": "FUNCTION",
        "name": "internal_name",
        "logical_name": "업무 이름",
        "summary": summary,
        "code_text": "raw source token",
        "stereotype": "VALIDATOR",
        "rels": ["WRITES:orders"],
    }
    row.update(overrides)
    return row


class SummaryOnlyContractTest(unittest.TestCase):
    def test_embedding_material_is_exactly_summary(self) -> None:
        first = _row("a", " 주문 상태 오류코드 반환 ")
        second = _row(
            "a",
            " 주문 상태 오류코드 반환 ",
            name="totally_different",
            code_text="different source",
            stereotype="WRITER",
            rels=["READS:customers"],
        )
        self.assertEqual("주문 상태 오류코드 반환", compose_embed_text(first))
        self.assertEqual(compose_embed_text(first), compose_embed_text(second))

    def test_queries_require_summary(self) -> None:
        where_clause = _CORPUS_QUERY.split("RETURN", 1)[0]
        self.assertIn("node.summary IS NOT NULL", where_clause)
        self.assertIn("HAS_SEARCH_SEGMENT", where_clause)
        self.assertNotIn("logical_name", _CORPUS_QUERY)

    def test_sparse_and_structural_channels_ignore_other_fields(self) -> None:
        rows = [
            _row("summary-hit", "needle"),
            _row(
                "noise",
                "unrelated",
                name="needle",
                logical_name="needle",
                code_text="needle",
            ),
            _row("other", "completely unrelated"),
        ]
        corpus = Corpus(rows, {}, "scope:test", "run:test")
        scores = corpus.bm25.get_scores(["needle"])
        self.assertGreater(scores[0], scores[1])
        self.assertEqual([("summary-hit", 1.0)], _structural(corpus, "needle", 10))

    def test_cross_encoder_passage_is_exactly_summary(self) -> None:
        self.assertEqual("needle facts", passage(_row("a", " needle facts ")))

if __name__ == "__main__":
    unittest.main()
