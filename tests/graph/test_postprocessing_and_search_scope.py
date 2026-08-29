"""Search readers must stay inside one completed Analyzer generation."""

import unittest
from pathlib import Path

from search.corpus.corpus import _CORPUS_QUERY, _FAN_IN_QUERY
from search.query.glossary import _GLOSSARY_TERMS_QUERY
from search.ranking.recall import _QUERY_NODES


ROOT = Path(__file__).resolve().parents[2]


class PostProcessingAndSearchScopeTest(unittest.TestCase):
    def test_search_queries_require_scope_and_generation(self) -> None:
        for query in (_GLOSSARY_TERMS_QUERY, _CORPUS_QUERY, _FAN_IN_QUERY, _QUERY_NODES):
            self.assertIn("analysis_scope_id", query)
            self.assertIn("generation_id", query)

    def test_glossary_reader_uses_closed_product_shape(self) -> None:
        self.assertIn(":GlossaryTerm", _GLOSSARY_TERMS_QUERY)
        for property_name in (
            "stable_id", "canonical_term", "aliases", "meaning", "category",
        ):
            self.assertIn(property_name, _GLOSSARY_TERMS_QUERY)
        self.assertNotIn("terms_json", _GLOSSARY_TERMS_QUERY)
        self.assertNotIn(":GLOSSARY", _GLOSSARY_TERMS_QUERY)

    def test_search_readers_do_not_reintroduce_unscoped_id_matches(self) -> None:
        sources = "\n".join(
            (ROOT / relative_path).read_text(encoding="utf-8")
            for relative_path in (
                "search/corpus/evidence_reader.py",
                "search/corpus/node_detail.py",
                "search/ranking/retriever.py",
                "search/ranking/graph_access.py",
                "search/query/glossary.py",
            )
        )
        for forbidden in (
            "MATCH (n {id:$id})",
            "MATCH (x {id:$id})",
            "MATCH (a {id:$id})",
            "MATCH (b {id:$id})",
            'MATCH (g:GLOSSARY {id:"glossary"})',
        ):
            self.assertNotIn(forbidden, sources)


if __name__ == "__main__":
    unittest.main()
