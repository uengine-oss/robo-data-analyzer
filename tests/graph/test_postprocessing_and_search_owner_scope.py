"""Analyzer post-processing and search queries must not cross graph-owner boundaries."""

import unittest
from pathlib import Path

from graph.ownership import ANALYZER_GRAPH_OWNER
from pipeline.steps.step7_post_processing.build_components_step import _SET_QUERY as COMPONENT_SET_QUERY
from pipeline.steps.step7_post_processing.build_glossary_step import _UPSERT_QUERY as GLOSSARY_UPSERT_QUERY
from pipeline.steps.step7_post_processing.embed_step import (
    _embedded_code_query,
    _embedding_query,
    _metadata_upsert_query,
)
from pipeline.steps.step7_post_processing.health_metrics_step import _SET_QUERY as HEALTH_SET_QUERY
from search.corpus.corpus import _CORPUS_QUERY, _FAN_IN_QUERY
from search.corpus.embed_material import FETCH_MATERIAL
from search.ranking.recall import _QUERY_NODES


ROOT = Path(__file__).resolve().parents[2]


class PostProcessingAndSearchOwnerScopeTest(unittest.TestCase):
    def test_post_processing_writes_include_owner_in_match_or_merge_identity(self) -> None:
        queries = (
            COMPONENT_SET_QUERY,
            HEALTH_SET_QUERY,
            GLOSSARY_UPSERT_QUERY,
            _embedded_code_query(),
            _embedding_query("TABLE", "id"),
            _embedding_query("COLUMN", "id"),
            _metadata_upsert_query(),
        )

        for query in queries:
            self.assertIn("graph_owner", query)
            self.assertIn(ANALYZER_GRAPH_OWNER, query)
        self.assertIn("MERGE (g:GLOSSARY {id: \"glossary\", graph_owner:", GLOSSARY_UPSERT_QUERY)
        self.assertIn("MERGE (m:EMBED_META {graph_owner:", _metadata_upsert_query())

    def test_search_material_and_vector_queries_are_owner_scoped(self) -> None:
        self.assertIn("n.graph_owner", FETCH_MATERIAL)
        self.assertIn("t:TABLE {graph_owner:", FETCH_MATERIAL)
        self.assertIn("n.graph_owner", _CORPUS_QUERY)
        self.assertEqual(2, _FAN_IN_QUERY.count("graph_owner"))
        self.assertIn("node.graph_owner = $graph_owner", _QUERY_NODES)

    def test_search_readers_do_not_reintroduce_unscoped_id_matches(self) -> None:
        sources = "\n".join(
            (ROOT / relative_path).read_text(encoding="utf-8")
            for relative_path in (
                "search/corpus/evidence_reader.py",
                "search/corpus/node_detail.py",
                "search/ranking/retriever.py",
                "search/ranking/graph_access.py",
                "search/query/glossary.py",
                "scripts/reembed_graph.py",
                "scripts/resynthesize_glossary.py",
            )
        )

        for forbidden in (
            "MATCH (n {id:$id})",
            "MATCH (x {id:$id})",
            "MATCH (a {id:$id})",
            "MATCH (b {id:$id})",
            'MATCH (g:GLOSSARY {id:"glossary"})',
            '"MATCH (n) WHERE',
            '"MATCH (t:TABLE) WHERE',
        ):
            self.assertNotIn(forbidden, sources)
        self.assertGreaterEqual(sources.count("graph_owner"), 30)


if __name__ == "__main__":
    unittest.main()
