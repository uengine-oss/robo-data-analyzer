from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from graph.schema.graph_nodes import CodeNode
from pipeline.models.analysis_context import AnalysisContext
from pipeline.models.data_object_usage import (
    DataObjectUsageEvidence,
    QualifiedColumnUsage,
)
from pipeline.steps.step7_analyze_code.column_usage_projection import (
    build_column_usage_projections,
)
from shared.files.cache import SourceFileCache


class ColumnUsageProjectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def _source(self, text: str) -> Path:
        path = Path(self.temp.name) / "sample.sql"
        path.write_text(text, encoding="utf-8")
        return path

    def test_single_direct_table_projects_unqualified_ddl_identifiers(self) -> None:
        source = self._source(
            "SELECT TAGSN, COMPUTE\n"
            "FROM CALC_TB\n"
            "WHERE TAGSN = tagNo;\n"
        )
        node = CodeNode(
            id="select:1", name="SELECT", signature="SELECT",
            file_path=str(source), start_line=1, end_line=3, label="SELECT",
        )
        context = AnalysisContext(
            id=node.id, signature=node.signature, file_path=str(source),
            start_line=1, end_line=3, direct_reads=("db:CALC_TB",),
            extra={
                "source_language": "oracle",
                "ddl_columns_by_table": {"db:CALC_TB": ("TAGSN", "COMPUTE", "ETC")},
            },
        )

        got = build_column_usage_projections(
            {node.id: context}, {node.id: node},
            strategy_name="dbms", source_cache=SourceFileCache(),
        )[node.id]["db:CALC_TB"]

        self.assertEqual([1, 3], got["TAGSN"]["evidence_lines"])
        self.assertEqual([1], got["COMPUTE"]["evidence_lines"])
        self.assertNotIn("ETC", got)
        self.assertEqual("sole_direct_table", got["TAGSN"]["ownership"])

    def test_multiple_tables_keep_qualified_and_ddl_unique_ownership(self) -> None:
        source = self._source("SELECT A.ID, B.STATUS, NAME FROM A JOIN B ON A.ID=B.ID;\n")
        node = CodeNode(
            id="select:1", name="SELECT", signature="SELECT",
            file_path=str(source), start_line=1, end_line=1, label="SELECT",
        )
        context = AnalysisContext(
            id=node.id, signature=node.signature, file_path=str(source),
            start_line=1, end_line=1,
            direct_reads=("db:A", "db:B"),
            extra={
                "source_language": "oracle",
                "ddl_columns_by_table": {
                    "db:A": ("ID", "NAME"),
                    "db:B": ("ID", "STATUS"),
                },
                "data_object_usages": (
                    DataObjectUsageEvidence(
                        source_id=node.id, raw_reference="A", name="A", alias="A",
                        access="READ", evidence_file=str(source), evidence_line=1,
                        table_id="db:A", resolution="exact",
                        qualified_columns=(QualifiedColumnUsage(
                            raw_reference="A.ID", qualifier="A", name="ID", evidence_line=1,
                        ),),
                    ),
                    DataObjectUsageEvidence(
                        source_id=node.id, raw_reference="B", name="B", alias="B",
                        access="READ", evidence_file=str(source), evidence_line=1,
                        table_id="db:B", resolution="exact",
                        qualified_columns=(QualifiedColumnUsage(
                            raw_reference="B.STATUS", qualifier="B", name="STATUS", evidence_line=1,
                        ),),
                    ),
                ),
            },
        )

        got = build_column_usage_projections(
            {node.id: context}, {node.id: node},
            strategy_name="dbms", source_cache=SourceFileCache(),
        )[node.id]

        self.assertEqual(["ID", "NAME"], list(got["db:A"]))
        self.assertEqual(["STATUS"], list(got["db:B"]))
        self.assertEqual("qualified", got["db:A"]["ID"]["ownership"])
        self.assertEqual("ddl_unique_in_scope", got["db:A"]["NAME"]["ownership"])

    def test_multiple_tables_project_only_ddl_unique_unqualified_identifier(self) -> None:
        source = self._source("SELECT SHARED_ID, COMPUTE FROM A JOIN B ON A.ID=B.ID;\n")
        node = CodeNode(
            id="select:1", name="SELECT", signature="SELECT",
            file_path=str(source), start_line=1, end_line=1, label="SELECT",
        )
        context = AnalysisContext(
            id=node.id, signature=node.signature, file_path=str(source),
            start_line=1, end_line=1, direct_reads=("db:A", "db:B"),
            extra={
                "source_language": "oracle",
                "ddl_columns_by_table": {
                    "db:A": ("ID", "SHARED_ID"),
                    "db:B": ("ID", "SHARED_ID", "COMPUTE"),
                },
            },
        )

        got = build_column_usage_projections(
            {node.id: context}, {node.id: node},
            strategy_name="dbms", source_cache=SourceFileCache(),
        )[node.id]

        self.assertNotIn("SHARED_ID", got["db:A"])
        self.assertNotIn("SHARED_ID", got["db:B"])
        self.assertEqual("ddl_unique_in_scope", got["db:B"]["COMPUTE"]["ownership"])

    def test_select_output_alias_is_not_projected_as_a_physical_column(self) -> None:
        source = self._source(
            "SELECT VAL AS TOTAL, COUNT(*) AS CNT, 1 AS FIXED_VALUE,\n"
            "       COUNT(*) CNT_IMPLICIT, 1 FIXED_IMPLICIT FROM A;\n"
        )
        node = CodeNode(
            id="select:1", name="SELECT", signature="SELECT",
            file_path=str(source), start_line=1, end_line=2, label="SELECT",
        )
        context = AnalysisContext(
            id=node.id, signature=node.signature, file_path=str(source),
            start_line=1, end_line=2, direct_reads=("db:A",),
            extra={
                "source_language": "oracle",
                "ddl_columns_by_table": {
                    "db:A": (
                        "VAL", "TOTAL", "CNT", "FIXED_VALUE",
                        "CNT_IMPLICIT", "FIXED_IMPLICIT",
                    ),
                },
            },
        )

        got = build_column_usage_projections(
            {node.id: context}, {node.id: node},
            strategy_name="dbms", source_cache=SourceFileCache(),
        )[node.id]["db:A"]

        self.assertIn("VAL", got)
        self.assertNotIn("TOTAL", got)
        self.assertNotIn("CNT", got)
        self.assertNotIn("FIXED_VALUE", got)
        self.assertNotIn("CNT_IMPLICIT", got)
        self.assertNotIn("FIXED_IMPLICIT", got)

    def test_same_range_nodes_fail_closed_for_unqualified_columns(self) -> None:
        source = self._source("SELECT ID FROM A;\n")
        nodes = {
            node_id: CodeNode(
                id=node_id, name="SELECT", signature="SELECT", file_path=str(source),
                start_line=1, end_line=1, label="SELECT",
            )
            for node_id in ("select:outer", "select:inner")
        }
        contexts = {
            node_id: AnalysisContext(
                id=node_id, signature="SELECT", file_path=str(source),
                start_line=1, end_line=1, direct_reads=(f"db:{node_id}",),
                extra={
                    "source_language": "oracle",
                    "ddl_columns_by_table": {f"db:{node_id}": ("ID",)},
                },
            )
            for node_id in nodes
        }

        got = build_column_usage_projections(
            contexts, nodes, strategy_name="dbms", source_cache=SourceFileCache(),
        )

        self.assertEqual({}, got["select:outer"]["db:select:outer"])
        self.assertEqual({}, got["select:inner"]["db:select:inner"])

    def test_outer_select_use_is_linked_to_one_unique_child_projection(self) -> None:
        source = self._source(
            "SELECT DECODE(COMPUTE, '+', 1, '-', -1)\n"
            "FROM (SELECT B.COMPUTE FROM CALC_TB B);\n"
        )
        parent = CodeNode(
            id="select:outer", signature="SELECT", file_path=str(source),
            start_line=1, end_line=2, label="SELECT",
        )
        child = CodeNode(
            id="select:child", signature="SELECT", file_path=str(source),
            start_line=2, end_line=2, label="SELECT", parent_id=parent.id,
        )
        contexts = {
            parent.id: AnalysisContext(
                id=parent.id, signature="SELECT", file_path=str(source),
                start_line=1, end_line=2, direct_reads=("db:OTHER_TB",),
                extra={
                    "source_language": "oracle",
                    "ddl_columns_by_table": {"db:OTHER_TB": ("OTHER_VALUE",)},
                },
            ),
            child.id: AnalysisContext(
                id=child.id, signature="SELECT", file_path=str(source),
                start_line=2, end_line=2, direct_reads=("db:CALC_TB",),
                extra={
                    "source_language": "oracle",
                    "ddl_columns_by_table": {"db:CALC_TB": ("COMPUTE",)},
                    "data_object_usages": (
                        DataObjectUsageEvidence(
                            source_id=child.id, raw_reference="CALC_TB", name="CALC_TB",
                            alias="B", access="READ", evidence_file=str(source),
                            evidence_line=2, table_id="db:CALC_TB", resolution="exact",
                            qualified_columns=(QualifiedColumnUsage(
                                raw_reference="B.COMPUTE", qualifier="B", name="COMPUTE",
                                evidence_line=2,
                            ),),
                        ),
                    ),
                },
            ),
        }

        got = build_column_usage_projections(
            contexts, {parent.id: parent, child.id: child},
            strategy_name="dbms", source_cache=SourceFileCache(),
        )[child.id]["db:CALC_TB"]["COMPUTE"]

        self.assertEqual([1, 2], got["evidence_lines"])
        self.assertEqual(parent.id, got["source_lines"][0]["source_id"])
        self.assertEqual("derived_output_unique", got["source_lines"][0]["ownership"])
        self.assertEqual(child.id, got["source_lines"][1]["source_id"])

    def test_framework_does_not_lexically_assign_columns(self) -> None:
        source = self._source('run("SELECT ID FROM A");\n')
        node = CodeNode(
            id="function:1", name="f", signature="f", file_path=str(source),
            start_line=1, end_line=1, label="FUNCTION",
        )
        context = AnalysisContext(
            id=node.id, signature=node.signature, file_path=str(source),
            start_line=1, end_line=1, direct_reads=("db:A",),
            extra={"ddl_columns_by_table": {"db:A": ("ID",)}},
        )

        got = build_column_usage_projections(
            {node.id: context}, {node.id: node},
            strategy_name="framework", source_cache=SourceFileCache(),
        )

        self.assertEqual({}, got[node.id]["db:A"])

    def test_postgresql_without_qualified_parser_evidence_fails_closed(self) -> None:
        source = self._source("SELECT ID FROM A;\n")
        node = CodeNode(
            id="select:pg", name="SELECT", signature="SELECT", file_path=str(source),
            start_line=1, end_line=1, label="SELECT",
        )
        context = AnalysisContext(
            id=node.id, signature=node.signature, file_path=str(source),
            start_line=1, end_line=1, direct_reads=("db:A",),
            extra={
                "source_language": "postgresql",
                "ddl_columns_by_table": {"db:A": ("ID",)},
            },
        )

        got = build_column_usage_projections(
            {node.id: context}, {node.id: node},
            strategy_name="dbms", source_cache=SourceFileCache(),
        )

        self.assertEqual({}, got[node.id]["db:A"])


if __name__ == "__main__":
    unittest.main()
