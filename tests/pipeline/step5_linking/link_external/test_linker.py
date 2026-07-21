from __future__ import annotations

import unittest

from graph.schema.graph_edges import CallsSql, ReferTo, UsesConfig
from graph.schema.graph_nodes import CodeNode, Constant, Module, Table
from graph.schema.node_ids import project_file_id
from pipeline.steps.step3_load_external.chunking import make_external_chunks
from pipeline.steps.step5_linking.link_external.linker import ExternalLinkContractError, link_external


class ExternalLinkerTest(unittest.TestCase):
    def test_direct_const_fqn_sql_chain_and_unresolved_are_exact(self) -> None:
        sql_chunks, _ = make_external_chunks(
            file_id=project_file_id("shop", "mapper.xml"), key="shop.Order.find", kind="sql",
            text="SELECT * FROM ORDERS; UPDATE ORDER_TOTAL SET amount=1",
            file_path="mapper.xml", start_line=3, end_line=3, sql_type="select",
        )
        cfg_chunks, _ = make_external_chunks(
            file_id=project_file_id("shop", "app.properties"), key="shop.title", kind="config",
            text="쇼핑몰", file_path="app.properties", start_line=1, end_line=1,
        )
        artifact_chunks, _ = make_external_chunks(
            file_id=project_file_id("shop", "beans.xml"), key="orderService", kind="artifact",
            text='<bean class="shop.service.OrderService"/>', file_path="beans.xml",
            start_line=2, end_line=2, related_fqns=("shop.service.OrderService",),
        )
        missing_chunks, _ = make_external_chunks(
            file_id=project_file_id("shop", "other.json"), key="never.used", kind="config",
            text="x", file_path="other.json", start_line=1, end_line=1,
        )
        code = CodeNode(
            id="code:shop.service:OrderService:find", signature="find()", file_path="OrderService.java",
            start_line=20, end_line=30, label="METHOD", name="find",
            code_text='// "never.used"\nmapper.use(SQL_KEY); config.get("shop.title");',
            owner_id="code:shop.service:OrderService",
        )
        constant = Constant(
            id="code:shop.service:OrderService:SQL_KEY", owner_id=code.owner_id, name="SQL_KEY",
            file_path=code.file_path, start_line=5, end_line=5, label="CONSTANT_FIELD",
            value='"shop.Order.find"',
        )
        tables = [
            Table("", "", "ORDERS"),
            Table("", "", "ORDER_TOTAL"),
        ]
        outcome = link_external(
            code_nodes=[code],
            modules=[Module(code.owner_id, "OrderService", code.file_path, "CLASS")],
            variables=[], constants=[constant],
            chunks=[*sql_chunks, *cfg_chunks, *artifact_chunks, *missing_chunks],
            tables=tables,
            routine_ids={code.id},
        )
        self.assertEqual(1, sum(isinstance(edge, CallsSql) for edge in outcome.external_edges))
        self.assertEqual(1, sum(isinstance(edge, UsesConfig) for edge in outcome.external_edges))
        self.assertEqual(1, sum(isinstance(edge, ReferTo) and edge.source_id == artifact_chunks[0].id for edge in outcome.external_edges))
        calls_sql = next(edge for edge in outcome.external_edges if isinstance(edge, CallsSql))
        self.assertEqual("SQL_KEY", calls_sql.via_const)
        rw = {(edge.source_id, edge.target_id, edge.relation) for edge in outcome.reads_writes_edges}
        for table in tables:
            relation = "READS" if table.name == "ORDERS" else "WRITES"
            self.assertIn((sql_chunks[0].id, table.id, relation), rw)
            self.assertIn((code.id, table.id, relation), rw)
        reasons = {(item.key, item.reason) for item in outcome.unresolved_refs}
        self.assertIn(("never.used", "key_not_found_in_code"), reasons)
        self.assertNotIn(("shop.Order.find", "key_not_found_in_code"), reasons)

    def test_missing_and_ambiguous_ddl_tables_remain_unresolved(self) -> None:
        chunks, _ = make_external_chunks(
            file_id=project_file_id("p", "m.xml"), key="m.q", kind="sql",
            text="SELECT * FROM DUP JOIN MISSING ON 1=1", file_path="m.xml", start_line=1, end_line=1,
        )
        outcome = link_external(
            code_nodes=[], modules=[], variables=[], constants=[], chunks=chunks,
            tables=[Table("", "A", "DUP"), Table("", "B", "DUP")],
            routine_ids=set(),
        )
        missing = [item for item in outcome.unresolved_refs if item.reason == "table_not_in_ddl"]
        self.assertEqual({"DUP", "MISSING"}, {item.key for item in missing})
        self.assertTrue(any("ambiguous" in item.detail for item in missing if item.key == "DUP"))

    def test_damaged_chunk_parts_fail_before_partial_sql_linking(self) -> None:
        chunks, _ = make_external_chunks(
            file_id=project_file_id("p", "m.xml"), key="m.q", kind="sql",
            text="SELECT * FROM T " + "x" * 1300, file_path="m.xml", start_line=1, end_line=1,
        )
        chunks[1] = type(chunks[1])(**{**chunks[1].__dict__, "text": "tampered"})
        with self.assertRaises(ExternalLinkContractError):
            link_external(
                code_nodes=[], modules=[], variables=[], constants=[], chunks=chunks,
                tables=[Table("", "", "T")], routine_ids=set(),
            )


if __name__ == "__main__":
    unittest.main()
