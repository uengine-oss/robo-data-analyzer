"""Search readers must consume the current product publication contract."""
from __future__ import annotations

import inspect
import unittest

from search.corpus import corpus, embed_material, evidence_reader, node_detail
from search.ranking import graph_access, recall, retriever


class CurrentPublicationReaderContractTests(unittest.TestCase):
    def test_product_readers_have_no_legacy_storage_contract(self) -> None:
        source = "\n".join(
            inspect.getsource(module)
            for module in (
                corpus,
                embed_material,
                evidence_reader,
                node_detail,
                graph_access,
                recall,
                retriever,
            )
        )
        for obsolete in (
            ":TABLE",
            ":COLUMN",
            ":RULE",
            ":EXAMPLE",
            ":FILE",
            ":MODULE",
            ":PACKAGE",
            "EMBEDDED_LABEL",
            "EMBED_META_LABEL",
            "FETCH_MATERIAL",
            "db.index.vector.queryNodes",
            "graph_owner:$graph_owner",
            "{id:$id",
            "node.id",
        ):
            with self.subTest(obsolete=obsolete):
                self.assertNotIn(obsolete, source)

    def test_search_reads_current_product_identity_and_segments(self) -> None:
        source = "\n".join((
            inspect.getsource(corpus),
            inspect.getsource(evidence_reader),
            inspect.getsource(node_detail),
            inspect.getsource(graph_access),
            inspect.getsource(recall),
            inspect.getsource(retriever),
        ))
        for required in (
            "stable_id",
            "CodeNode",
            "Table",
            "Column",
            "Rule",
            "Example",
            "SearchSegment",
            "HAS_SEARCH_SEGMENT",
            "SEARCH segment IN",
        ):
            with self.subTest(required=required):
                self.assertIn(required, source)

    def test_rule_and_table_reads_use_only_published_relationships(self) -> None:
        self.assertNotIn("AFFECTS_TABLE", inspect.getsource(evidence_reader.read_rules))
        self.assertEqual(("READS", "WRITES"), graph_access.TABLE_RELATIONS)
        self.assertEqual(("READS", "WRITES"), retriever._TABLE_RELS)


if __name__ == "__main__":
    unittest.main()
