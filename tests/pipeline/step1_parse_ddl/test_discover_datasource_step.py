"""Datasource discovery contracts for merge, guards, and unknown sources."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import pipeline.steps.step1_parse_ddl.discover_datasource_step as step_module
from graph.schema.graph_nodes import Table
from pipeline.steps.step1_parse_ddl.discover_datasource_step import (
    _merge,
    discover_datasource_step,
)


def _payload(tables):
    return {
        "schema_version": "catalog-table-discovery-page/v1",
        "datasource": "prod_main",
        "snapshot": "sealed",
        "total_tables": len(tables),
        "tables": tables,
    }


def _cfg(datasource="prod_main"):
    return {"configurable": {
        "codebase": SimpleNamespace(datasource=datasource),
        "run": None, "stream": None, "neo_client": None, "cost_budget": None,
        "source_cache": None,
    }}


class DatasourceMergeTest(unittest.TestCase):
    def test_creates_tables_and_columns_when_ddl_is_empty(self):
        merged, new_tables, new_columns, new_edges, samples = _merge(
            [], _payload([{
                "name": "ORDERS", "schema": "PUBLIC",
                "columns": [{"name": "ORDER_ID", "dtype": "NUMBER", "nullable": False}],
                "sample_rows": [{"ORDER_ID": 1}], "degraded": False,
            }]), "prod_main",
        )
        self.assertEqual(["ORDERS"], [table.name for table in merged])
        self.assertEqual("prod_main", new_tables[0].db)
        self.assertEqual("catalog", new_tables[0].description_source)
        self.assertEqual("ORDER_ID", new_columns[0].name)
        self.assertFalse(new_columns[0].nullable)
        self.assertEqual(new_tables[0].id, new_edges[0].parent_id)
        self.assertEqual([{"ORDER_ID": 1}], samples[new_tables[0].id]["sample_rows"])

    def test_union_is_case_insensitive_and_ddl_wins(self):
        ddl = [Table(db="shop", schema="PUBLIC", name="ORDERS", comment="DDL comment")]
        merged, new_tables, new_columns, _, samples = _merge(
            ddl, _payload([
                {"name": "orders", "schema": "PUBLIC", "columns": [{"name": "X"}],
                 "sample_rows": [{"X": 9}], "degraded": False},
                {"name": "USERS", "schema": "", "columns": [],
                 "sample_rows": [], "degraded": True},
            ]), "prod_main",
        )
        self.assertEqual(["ORDERS", "USERS"], [table.name for table in merged])
        self.assertEqual("DDL comment", merged[0].comment)
        self.assertEqual(["USERS"], [table.name for table in new_tables])
        self.assertEqual([], new_columns)
        self.assertEqual([{"X": 9}], samples[ddl[0].id]["sample_rows"])

    def test_degraded_table_enters_dictionary_without_samples(self):
        merged, _, _, _, samples = _merge(
            [], _payload([{
                "name": "BROKEN", "schema": "", "columns": [],
                "sample_rows": [], "degraded": True,
            }]), "ds",
        )
        self.assertEqual(["BROKEN"], [table.name for table in merged])
        self.assertEqual({}, samples)

    def test_same_table_name_in_different_schemas_receives_only_its_own_sample(self):
        public = Table(db="prod_main", schema="PUBLIC", name="ORDERS")
        tenant = Table(db="prod_main", schema="TENANT", name="ORDERS")

        _, _, _, _, samples = _merge(
            [tenant, public], _payload([{
                "name": "orders", "schema": "tenant", "columns": [{"name": "ID"}],
                "sample_rows": [{"ID": 7}], "degraded": False,
            }]), "prod_main",
        )

        self.assertEqual({tenant.id}, set(samples))
        self.assertEqual([{"ID": 7}], samples[tenant.id]["sample_rows"])

    def test_catalog_quoted_identity_and_column_are_preserved(self):
        merged, new_tables, new_columns, _, _ = _merge(
            [], _payload([{
                "name": "Orders", "name_quoted": True,
                "schema": "App", "schema_quoted": True,
                "catalog": "Main", "catalog_quoted": True,
                "columns": [{"name": "OrderId", "name_quoted": True}],
                "sample_rows": [], "degraded": False,
            }]), "prod_main",
        )
        self.assertEqual(merged, new_tables)
        self.assertTrue(new_tables[0].catalog_quoted)
        self.assertTrue(new_tables[0].schema_quoted)
        self.assertTrue(new_tables[0].name_quoted)
        self.assertTrue(new_columns[0].name_quoted)


class DatasourceStepTest(unittest.IsolatedAsyncioTestCase):
    async def test_skips_without_datasource(self):
        with patch.object(step_module, "catalog_base_url_or_skip", return_value="http://cat"):
            result = await discover_datasource_step({"tables": []}, _cfg(datasource=""))
        self.assertEqual({}, result)

    async def test_skips_when_catalog_is_disabled(self):
        with patch.object(step_module, "catalog_base_url_or_skip", return_value=None):
            result = await discover_datasource_step({"tables": []}, _cfg())
        self.assertEqual({}, result)

    async def test_unknown_datasource_warns_and_keeps_ddl_state(self):
        async def fake_discovery(_http, _base, _datasource, **_kwargs):
            return None

        warned = []
        with (
            patch.object(step_module, "catalog_base_url_or_skip", return_value="http://cat"),
            patch.object(
                step_module, "get_complete_table_discovery", side_effect=fake_discovery,
            ),
            patch.object(step_module, "log_warning", side_effect=lambda *args, **_kwargs: warned.append(args)),
        ):
            result = await discover_datasource_step({"tables": []}, _cfg())
        self.assertEqual({}, result)
        self.assertTrue(warned)

    async def test_merges_and_returns_step7_state_shape(self):
        async def fake_discovery(_http, _base, _datasource, **_kwargs):
            return _payload([{
                "name": "ORDERS", "schema": "PUBLIC",
                "columns": [{"name": "ORDER_ID", "dtype": "NUMBER", "nullable": False}],
                "sample_rows": [{"ORDER_ID": 1}], "degraded": False,
            }])

        with (
            patch.object(step_module, "catalog_base_url_or_skip", return_value="http://cat"),
            patch.object(
                step_module, "get_complete_table_discovery", side_effect=fake_discovery,
            ),
        ):
            result = await discover_datasource_step({"tables": []}, _cfg())
        self.assertEqual(["ORDERS"], [table.name for table in result["tables"]])
        table_id = result["tables"][0].id
        self.assertEqual([{"ORDER_ID": 1}], result["table_sample_data"][table_id]["sample_rows"])
        self.assertEqual(
            {"columns", "sample_rows"}, set(result["table_sample_data"][table_id]),
        )

    async def test_page_contract_failure_prevents_partial_merge(self):
        async def fake_discovery(_http, _base, _datasource, **_kwargs):
            raise RuntimeError("catalog discovery snapshot changed between pages")

        with (
            patch.object(step_module, "catalog_base_url_or_skip", return_value="http://cat"),
            patch.object(
                step_module, "get_complete_table_discovery", side_effect=fake_discovery,
            ),
            patch.object(step_module, "_merge", wraps=step_module._merge) as merge,
        ):
            with self.assertRaisesRegex(RuntimeError, "snapshot changed"):
                await discover_datasource_step({"tables": []}, _cfg())

        merge.assert_not_called()


if __name__ == "__main__":
    unittest.main()
