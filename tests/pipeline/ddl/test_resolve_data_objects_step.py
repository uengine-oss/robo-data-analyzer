from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import pipeline.stages.ddl.resolve_data_objects_step as subject
from graph.schema.graph_nodes import CodeNode
from pipeline.models.data_object_usage import DataObjectUsageEvidence, QualifiedColumnUsage


def _config():
    return {"configurable": {
        "codebase": SimpleNamespace(datasource="water"),
        "neo_client": None, "stream": None,
    }}


def _select() -> CodeNode:
    node = CodeNode(
        id="code:p:SELECT@239", signature="SELECT", file_path="p.sql",
        start_line=239, end_line=245, label="SELECT",
    )
    node.extra["data_object_evidence_version"] = 1
    node.extra["data_object_usages"] = (
        DataObjectUsageEvidence(
            source_id=node.id, raw_reference="RWIS.RDIB2EN_TB", schema="RWIS",
            name="RDIB2EN_TB", alias="A", access="READ", evidence_file="p.sql",
            evidence_line=241, qualified_columns=(QualifiedColumnUsage(
                raw_reference="A.BNB_CODE", qualifier="A", name="BNB_CODE", evidence_line=239,
            ),),
        ),
        DataObjectUsageEvidence(
            source_id=node.id, raw_reference="RWIS.RDD01DD_TB", schema="RWIS",
            name="RDD01DD_TB", alias="B", access="READ", evidence_file="p.sql",
            evidence_line=242, qualified_columns=(QualifiedColumnUsage(
                raw_reference="B.VAL", qualifier="B", name="VAL", evidence_line=239,
            ),),
        ),
    )
    return node


class ResolveDataObjectsStepTest(unittest.IsolatedAsyncioTestCase):
    async def test_confirmed_results_materialize_exact_tables_columns_samples_and_usage_ids(self):
        node = _select()

        async def fake_resolve(_http, _base, _datasource, references, **_kwargs):
            assert [item["qualified_columns"] for item in references] == [["BNB_CODE"], ["VAL"]]
            return {
                "datasource": "water",
                "results": [
                    {
                        "key": references[0]["key"], "status": "confirmed",
                        "schema_name": "RWIS", "object_name": "RDIB2EN_TB",
                        "columns": [{"name": "BNB_CODE", "type": "VARCHAR"}],
                        "sample_rows": [{"BNB_CODE": "B1"}],
                    },
                    {
                        "key": references[1]["key"], "status": "confirmed",
                        "schema_name": "RWIS", "object_name": "RDD01DD_TB",
                        "columns": [{"name": "VAL", "type": "NUMBER"}],
                        "sample_rows": [{"VAL": 3}],
                    },
                ],
                "metrics": {"provider_calls": 5},
            }

        with (
            patch.object(subject, "catalog_base_url_or_skip", return_value="http://catalog/robo"),
            patch.object(subject, "post_object_resolution_context", side_effect=fake_resolve),
        ):
            result = await subject.resolve_data_objects_step({
                "code_nodes": [node], "tables": [], "columns": [], "has_column_edges": [],
            }, _config())

        assert [table.name for table in result["tables"]] == ["RDIB2EN_TB", "RDD01DD_TB"]
        assert [column.name for column in result["columns"]] == ["BNB_CODE", "VAL"]
        assert [usage.table_id for usage in node.extra["data_object_usages"]] == [
            "db:water.RWIS.RDIB2EN_TB", "db:water.RWIS.RDD01DD_TB",
        ]
        assert result["table_sample_data"]["db:water.RWIS.RDD01DD_TB"]["sample_rows"] == [{"VAL": 3}]
        assert result["data_object_resolution_metrics"]["provider_calls"] == 5

    async def test_unresolved_result_keeps_parser_identity_without_creating_table(self):
        node = _select()

        async def fake_resolve(_http, _base, _datasource, references, **_kwargs):
            return {
                "datasource": "water",
                "results": [{
                    "key": item["key"], "status": "unresolved",
                    "reason": "database_link_metadata_unavailable",
                } for item in references],
                "metrics": {"provider_calls": 1},
            }

        with (
            patch.object(subject, "catalog_base_url_or_skip", return_value="http://catalog/robo"),
            patch.object(subject, "post_object_resolution_context", side_effect=fake_resolve),
        ):
            result = await subject.resolve_data_objects_step({
                "code_nodes": [node], "tables": [], "columns": [], "has_column_edges": [],
            }, _config())

        assert result["tables"] == []
        assert all(usage.resolution == "unresolved" for usage in node.extra["data_object_usages"])
        assert all(usage.datasource == "water" for usage in node.extra["data_object_usages"])
        assert len(node.extra["unresolved_data_object_usages"]) == 2

    async def test_confirmed_quoted_request_without_provider_quote_proof_fails_closed(self):
        node = _select()
        quoted = DataObjectUsageEvidence(
            source_id=node.id, raw_reference='"App"."Orders"', schema="App",
            schema_quoted=True, name="Orders", name_quoted=True, access="READ",
            evidence_file="p.sql", evidence_line=241,
        )
        node.extra["data_object_usages"] = (quoted,)

        async def fake_resolve(_http, _base, _datasource, references, **_kwargs):
            self.assertTrue(references[0]["case_sensitive_identifiers"])
            return {"results": [{
                "key": references[0]["key"], "status": "confirmed",
                "schema_name": "App", "object_name": "Orders",
                "columns": [], "sample_rows": [],
            }]}

        with (
            patch.object(subject, "catalog_base_url_or_skip", return_value="http://catalog/robo"),
            patch.object(subject, "post_object_resolution_context", side_effect=fake_resolve),
            self.assertRaisesRegex(ValueError, "quoted identifier.*provider.*proof"),
        ):
            await subject.resolve_data_objects_step({
                "code_nodes": [node], "tables": [], "columns": [],
                "has_column_edges": [],
            }, _config())

    async def test_confirmed_catalog_request_without_provider_catalog_proof_fails_closed(self):
        node = _select()
        usage = DataObjectUsageEvidence(
            source_id=node.id, raw_reference="warehouse.app.orders",
            catalog="warehouse", schema="app", name="orders", access="READ",
            evidence_file="p.sql", evidence_line=241,
        )
        node.extra["data_object_usages"] = (usage,)

        async def fake_resolve(_http, _base, _datasource, references, **_kwargs):
            return {"results": [{
                "key": references[0]["key"], "status": "confirmed",
                "schema_name": "app", "object_name": "orders",
                "columns": [], "sample_rows": [],
            }]}

        with (
            patch.object(subject, "catalog_base_url_or_skip", return_value="http://catalog/robo"),
            patch.object(subject, "post_object_resolution_context", side_effect=fake_resolve),
            self.assertRaisesRegex(ValueError, "catalog/quoted identifier.*provider.*proof"),
        ):
            await subject.resolve_data_objects_step({
                "code_nodes": [node], "tables": [], "columns": [],
                "has_column_edges": [],
            }, _config())


if __name__ == "__main__":
    unittest.main()
