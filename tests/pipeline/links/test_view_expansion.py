"""spec 112 — 뷰 펼치기 (VIEW 엣지 → 기저 테이블 엣지 추가)."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from graph.schema.graph_edges import ReadsWrites
from graph.schema.graph_nodes import Table
from pipeline.stages.ddl.ddl_parser import parse_ddl_file
from pipeline.stages.links.tables.view_expansion import expand_view_edges


def _t(
    name: str, ttype: str = "TABLE", definition: str = "",
    dependencies: tuple[str, ...] = (),
) -> Table:
    return Table(db="d", schema="S", name=name, table_type=ttype,
                 view_definition=definition, view_dependency_ids=dependencies)


def _edge(src: str, target: Table, relation: str = "READS") -> ReadsWrites:
    return ReadsWrites(source_id=src, target_id=target.id, relation=relation,
                       dml_type="SELECT", evidence_file="f.sql", evidence_line=3)


def _parse_tables(ddl: str) -> list[Table]:
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "views.sql"
        path.write_text(ddl, encoding="utf-8")
        return parse_ddl_file(path, db="d", dialect="postgres").tables


class ViewExpansionTest(unittest.TestCase):
    def test_view_read_adds_base_table_reads(self):
        orders, cust = _t("ORDERS"), _t("CUST")
        view = _t("V_SUMMARY", "VIEW",
                  "SELECT o.id, c.name FROM ORDERS o JOIN CUST c ON o.cid = c.id",
                  (orders.id, cust.id))
        added = expand_view_edges([_edge("fn:1", view)], [orders, cust, view])
        got = {(e.target_id, e.relation) for e in added}
        self.assertEqual(got, {(orders.id, "READS"), (cust.id, "READS")})
        # evidence 는 원본 코드 위치 승계
        self.assertTrue(all(e.evidence_line == 3 for e in added))

    def test_nested_view_expands_to_leaf_tables(self):
        base = _t("ORDERS")
        inner = _t("V_INNER", "VIEW", "SELECT * FROM ORDERS", (base.id,))
        outer = _t("V_OUTER", "VIEW", "SELECT * FROM V_INNER", (inner.id,))
        added = expand_view_edges([_edge("fn:1", outer)], [base, inner, outer])
        self.assertEqual({e.target_id for e in added}, {base.id})

    def test_view_chain_has_no_arbitrary_depth_cutoff(self) -> None:
        tables = _parse_tables("""\
CREATE TABLE S.BASE (ID INTEGER);
CREATE VIEW S.V1 AS SELECT * FROM S.BASE;
CREATE VIEW S.V2 AS SELECT * FROM S.V1;
CREATE VIEW S.V3 AS SELECT * FROM S.V2;
CREATE VIEW S.V4 AS SELECT * FROM S.V3;
CREATE VIEW S.V5 AS SELECT * FROM S.V4;
""")
        by_name = {table.name: table for table in tables}

        added = expand_view_edges([_edge("fn:deep", by_name["V5"])], tables)

        self.assertEqual({by_name["BASE"].id}, {edge.target_id for edge in added})

    def test_view_ast_preserves_schema_quote_identity_and_excludes_cte_alias(self) -> None:
        tables = _parse_tables('''\
CREATE TABLE "App"."Foo" ("Id" INTEGER);
CREATE TABLE "App"."foo" ("Id" INTEGER);
CREATE TABLE "Other"."Foo" ("Id" INTEGER);
CREATE VIEW "App"."V" AS
WITH "Local" AS (SELECT * FROM "App"."Foo")
SELECT * FROM "Local" JOIN "App"."foo" ON 1 = 1;
''')
        by_id = {table.id: table for table in tables}
        view = next(table for table in tables if table.name == "V")

        added = expand_view_edges([_edge("fn:quoted", view)], tables)

        self.assertEqual(
            {('App', 'Foo'), ('App', 'foo')},
            {(by_id[edge.target_id].schema, by_id[edge.target_id].name) for edge in added},
        )
        self.assertNotIn(("", "Local"), {
            (by_id[edge.target_id].schema, by_id[edge.target_id].name) for edge in added
        })

    def test_cycle_is_safe(self):
        a = _t("V_A", "VIEW", "SELECT * FROM V_B")
        b = _t("V_B", "VIEW", "SELECT * FROM V_A")
        a = replace(a, view_dependency_ids=(b.id,))
        b = replace(b, view_dependency_ids=(a.id,))
        added = expand_view_edges([_edge("fn:1", a)], [a, b])
        self.assertEqual(added, [])            # 순환 — 기저 없음, 무한루프 없음

    def test_writes_through_updatable_view(self):
        """뷰에 WRITES → 정의상 write 대상 기저에만 WRITES 전파."""
        orders = _t("ORDERS")
        view = _t("V_ORD", "VIEW", "SELECT * FROM ORDERS", (orders.id,))
        added = expand_view_edges(
            [ReadsWrites(source_id="fn:1", target_id=view.id, relation="WRITES",
                         dml_type="UPDATE", evidence_file="f", evidence_line=1)],
            [orders, view],
        )
        # 정의는 SELECT…FROM 뿐 → 기저는 read 모드 → 보수적으로 READS 전파
        self.assertEqual([(e.target_id, e.relation) for e in added],
                         [(orders.id, "READS")])

    def test_plain_table_edges_untouched(self):
        orders = _t("ORDERS")
        edges = [_edge("fn:1", orders)]
        self.assertEqual(expand_view_edges(edges, [orders]), [])


if __name__ == "__main__":
    unittest.main()
