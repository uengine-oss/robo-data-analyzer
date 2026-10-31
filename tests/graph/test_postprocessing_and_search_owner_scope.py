"""Analyzer post-processing and search queries must not cross graph-owner boundaries."""

import unittest
from pathlib import Path

from graph.ownership import ANALYZER_GRAPH_OWNER
from pipeline.steps.step8_post_processing.build_components_step import _SET_QUERY as COMPONENT_SET_QUERY
from pipeline.steps.step8_post_processing.build_glossary_step import _UPSERT_QUERY as GLOSSARY_UPSERT_QUERY
from pipeline.steps.step8_post_processing.embed_step import (
    _create_vector_index_query,
    _embedded_code_query,
    _embedding_query,
    _metadata_upsert_query,
)
from pipeline.steps.step8_post_processing.health_metrics_step import _SET_QUERY as HEALTH_SET_QUERY
from search.corpus.corpus import _CORPUS_QUERY, _FAN_IN_QUERY
from search.corpus.embed_material import FETCH_MATERIAL, VECTOR_INDEX_NAME, compose_embed_text, group_material
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
        self.assertIn(
            "MERGE (g:ANALYZER_OWNED:GLOSSARY {id: \"glossary\", graph_owner:", GLOSSARY_UPSERT_QUERY,
        )
        self.assertIn("MERGE (m:ANALYZER_OWNED:EMBED_META {graph_owner:", _metadata_upsert_query())

    def test_search_vector_marker_is_a_property_not_a_label(self) -> None:
        """임베딩은 분석 끝에 붙는다 — Ontological DB 는 그때 라벨을 더할 수 없다."""
        query = _embedded_code_query()
        self.assertIn("n.search_embedding = item.vector", query)
        self.assertIn("n.embedded = true", query)
        self.assertNotIn("n:EMBEDDED", query)
        index_query = _create_vector_index_query(8)
        self.assertIn("FOR (n:ANALYZER_OWNED) ON (n.search_embedding)", index_query)
        self.assertIn(VECTOR_INDEX_NAME, index_query)

    def test_material_rows_are_grouped_per_node_in_python(self) -> None:
        """관계 묶음은 파이썬이 한다 — Ontological DB 는 collect(DISTINCT 문자열 이어붙이기)를 거절한다."""
        self.assertNotIn("collect(", FETCH_MATERIAL)
        base = {"label": "METHOD", "name": "place", "stereotype": "Command", "summary": "주문 접수", "search_text": ""}
        rows = [
            {"id": "b", **base, "rel_type": None, "table_name": None},
            {"id": "a", **base, "rel_type": "WRITES", "table_name": "orders"},
            {"id": "a", **base, "rel_type": "READS", "table_name": "menu"},
            {"id": "a", **base, "rel_type": "WRITES", "table_name": "orders"},
            {"id": "a", **base, "rel_type": "READS", "table_name": None},
        ]
        grouped = group_material(rows)
        self.assertEqual([row["id"] for row in grouped], ["a", "b"])
        self.assertEqual(grouped[0]["rels"], ["READS:menu", "WRITES:orders"])
        self.assertEqual(grouped[1]["rels"], [])
        self.assertNotIn("rel_type", grouped[0])
        self.assertIn("쓰는테이블:orders", compose_embed_text(grouped[0]))
        self.assertIn("읽는테이블:menu", compose_embed_text(grouped[0]))

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
