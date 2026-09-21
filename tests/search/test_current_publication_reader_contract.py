"""Search reads the direct minimal product graph."""
from __future__ import annotations

import inspect
import unittest

from search.corpus import evidence_reader, node_detail, search_corpus as corpus
from search.ranking import graph_access
from search.ranking import graph_recall as recall
from search.ranking import graph_retriever as retriever


class CurrentPublicationReaderContractTests(unittest.TestCase):
    def test_search_uses_direct_identity_embedding_and_products(self) -> None:
        source = "\n".join(inspect.getsource(module) for module in (
            corpus, evidence_reader, node_detail, graph_access, recall, retriever,
        ))
        for required in (
            "product_id", "_owner", "_search_embedding", ":TABLE", ":COLUMN",
            ":RULE", "HAS_RULE", "CALLS", "READS", "WRITES",
        ):
            with self.subTest(required=required):
                self.assertIn(required, source)

    def test_rule_and_table_reads_use_published_relationships(self) -> None:
        self.assertIn("HAS_RULE", inspect.getsource(evidence_reader.read_rules))
        self.assertEqual(("READS", "WRITES"), graph_access.TABLE_RELATIONS)
        self.assertEqual(("READS", "WRITES"), retriever._TABLE_RELS)


if __name__ == "__main__":
    unittest.main()
