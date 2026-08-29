"""Run generation isolation contracts (spec 131 C-032)."""
from __future__ import annotations

import inspect
import unittest
from pathlib import Path

from graph.node_upsert import merge_query
from graph.publication_schema import PRODUCT_NODE_TYPES
from graph.schema import constraints
from pipeline.execution import run_executor
from search.corpus import corpus, node_detail


class GenerationIsolationRedContractTests(unittest.TestCase):
    _ROOT = Path(__file__).resolve().parents[2]

    def test_node_identity_is_generation_scoped(self) -> None:
        query = merge_query("FUNCTION")
        self.assertIn("analysis_scope_id: $analysis_scope_id", query)
        self.assertIn("generation_id: $generation_id", query)

    def test_schema_uniqueness_is_generation_scoped(self) -> None:
        analyzer_constraints = "\n".join(constraints.CONSTRAINTS)
        self.assertIn(
            "(n.graph_owner, n.analysis_scope_id, n.generation_id, n.id) IS UNIQUE",
            analyzer_constraints,
        )
        self.assertNotIn("REQUIRE n.id IS UNIQUE", analyzer_constraints)
        for label in PRODUCT_NODE_TYPES:
            self.assertIn(
                f"FOR (n:{label}) REQUIRE "
                "(n.analysis_scope_id, n.generation_id, n.stable_id) IS UNIQUE",
                analyzer_constraints,
            )

    def test_schema_bootstrap_contains_no_runtime_migration(self) -> None:
        source = inspect.getsource(constraints.ensure_schema)
        self.assertNotIn("SHOW CONSTRAINTS", source)
        self.assertNotIn("DROP CONSTRAINT", source)
        self.assertNotIn("MATCH (", source)
        self.assertFalse(hasattr(constraints, "LEGACY_ID_CONSTRAINT_NAMES"))
        self.assertFalse(hasattr(constraints, "LEGACY_ANALYZER_STABLE_LABELS"))

    def test_schema_bootstrap_has_no_obsolete_table_index(self) -> None:
        self.assertEqual([], constraints.INDEXES)

    def test_run_does_not_destroy_published_graph_before_success(self) -> None:
        source = inspect.getsource(run_executor.execute_run)
        self.assertNotIn("wipe_graph", source)
        self.assertIn("publish_generation", source)
        self.assertIn("terminate_generation", source)

    def test_search_corpus_is_scoped_to_active_completed_generation(self) -> None:
        self.assertIn("generation_id", corpus._CORPUS_QUERY)
        self.assertIn("generation_id", corpus._FAN_IN_QUERY)
        self.assertIn("completed_generation_session", inspect.getsource(corpus.load_corpus))

    def test_node_detail_requires_active_completed_generation(self) -> None:
        source = inspect.getsource(node_detail.read_node_detail)
        self.assertIn("completed_generation_session", source)
        self.assertIn("GRAPH_NOT_PUBLISHED", source)

    def test_every_graph_identity_query_is_generation_scoped(self) -> None:
        files = [
            *sorted((self._ROOT / "graph" / "edges").glob("*.py")),
            *sorted((self._ROOT / "graph" / "nodes").glob("*.py")),
            *sorted(
                (self._ROOT / "pipeline" / "steps" / "step8_post_processing").glob("*.py")
            ),
        ]
        violations: list[str] = []
        for path in files:
            for line_number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1,
            ):
                if (
                    ("MATCH (" in line or "MERGE (" in line)
                    and "graph_owner" in line
                    and (
                        "analysis_scope_id" not in line
                        or "generation_id" not in line
                    )
                ):
                    violations.append(f"{path.relative_to(self._ROOT)}:{line_number}")
        self.assertEqual([], violations)

    def test_every_relationship_writer_records_scope_and_generation(self) -> None:
        violations: list[str] = []
        for path in sorted((self._ROOT / "graph" / "edges").glob("*.py")):
            source = path.read_text(encoding="utf-8")
            if "MERGE (" in source and "]->" in source:
                if (
                    "analysis_scope_id = $analysis_scope_id" not in source
                    or "generation_id = $generation_id" not in source
                ):
                    violations.append(str(path.relative_to(self._ROOT)))
        self.assertEqual([], violations)


if __name__ == "__main__":
    unittest.main()
