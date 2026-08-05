"""spec 112 — 뷰 펼치기 (VIEW 엣지 → 기저 테이블 엣지 추가)."""
from __future__ import annotations

import unittest

from graph.schema.graph_edges import ReadsWrites
from graph.schema.graph_nodes import Table
from pipeline.steps.step5_linking.link_tables.view_expansion import expand_view_edges


def _t(name: str, ttype: str = "TABLE", definition: str = "") -> Table:
    return Table(db="d", schema="S", name=name, table_type=ttype,
                 view_definition=definition)


def _edge(src: str, target: Table, relation: str = "READS") -> ReadsWrites:
    return ReadsWrites(source_id=src, target_id=target.id, relation=relation,
                       dml_type="SELECT", evidence_file="f.sql", evidence_line=3)


class ViewExpansionTest(unittest.TestCase):
    def test_view_read_adds_base_table_reads(self):
        orders, cust = _t("ORDERS"), _t("CUST")
        view = _t("V_SUMMARY", "VIEW",
                  "SELECT o.id, c.name FROM ORDERS o JOIN CUST c ON o.cid = c.id")
        added = expand_view_edges([_edge("fn:1", view)], [orders, cust, view])
        got = {(e.target_id, e.relation) for e in added}
        self.assertEqual(got, {(orders.id, "READS"), (cust.id, "READS")})
        # evidence 는 원본 코드 위치 승계
        self.assertTrue(all(e.evidence_line == 3 for e in added))

    def test_nested_view_expands_to_leaf_tables(self):
        base = _t("ORDERS")
        inner = _t("V_INNER", "VIEW", "SELECT * FROM ORDERS")
        outer = _t("V_OUTER", "VIEW", "SELECT * FROM V_INNER")
        added = expand_view_edges([_edge("fn:1", outer)], [base, inner, outer])
        self.assertEqual({e.target_id for e in added}, {base.id})

    def test_cycle_is_safe(self):
        a = _t("V_A", "VIEW", "SELECT * FROM V_B")
        b = _t("V_B", "VIEW", "SELECT * FROM V_A")
        added = expand_view_edges([_edge("fn:1", a)], [a, b])
        self.assertEqual(added, [])            # 순환 — 기저 없음, 무한루프 없음

    def test_writes_through_updatable_view(self):
        """뷰에 WRITES → 정의상 write 대상 기저에만 WRITES 전파."""
        orders = _t("ORDERS")
        view = _t("V_ORD", "VIEW", "SELECT * FROM ORDERS")
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
