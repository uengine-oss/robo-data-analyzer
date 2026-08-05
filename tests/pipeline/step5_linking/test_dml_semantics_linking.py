"""spec 106 US1·US2·US3 — 의미표 기반 R/W 판정 케이스 (T007·T008·T011).

실측 결함 재현을 그대로 고정한다: AMS TRUNCATE 46건 누락 / GIOS FROM 생략 DELETE
READS 오분류. 모든 케이스는 실코퍼스 원문 형태를 축약 없이 따른다.
"""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from graph.schema.graph_nodes import CodeNode, Table
from shared.files.cache import SourceFileCache
from pipeline.steps.step5_linking.link_tables.link_dbms_tables_step import _link
from pipeline.steps.step5_linking.link_tables.regex_table_linker import (
    CodeLocator,
    _c_sql_semantic_edges,
)


def _node(
    node_id: str, file_path: str, start: int, end: int, *, label: str = "PROCEDURE",
) -> CodeNode:
    return CodeNode(id=node_id, signature=node_id, file_path=file_path,
                    start_line=start, end_line=end, label=label, name=node_id)


class DbmsSemanticsLinkingTest(unittest.TestCase):
    """US1·US2 — DBMS 경로 (자기 줄 스캔)."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def _run(self, sql: str, table_names: list[str]):
        src = self.dir / "case.sql"
        src.write_text(sql, encoding="utf-8")
        tables = [Table(db="d", schema="S", name=n) for n in table_names]
        n_lines = sql.count("\n") + 1
        edges = _link([_node("p:1", str(src), 1, n_lines)], tables, [], SourceFileCache())
        return {t.name: t.id for t in tables}, edges

    # ── US1: TRUNCATE (AS 1~3) ──

    def test_truncate_literal_is_writes_with_truncate_dml(self):
        ids, edges = self._run(
            "BEGIN\n  EXECUTE IMMEDIATE 'TRUNCATE TABLE AMS_MIG.TD_AB00008';\nEND;",
            ["TD_AB00008"],
        )
        self.assertEqual(
            [(e.relation, e.dml_type) for e in edges if e.target_id == ids["TD_AB00008"]],
            [("WRITES", "TRUNCATE")],
        )

    def test_select_and_truncate_same_body_are_judged_separately(self):
        ids, edges = self._run(
            "BEGIN\n"
            "  SELECT COUNT(*) INTO v FROM TI_SRC;\n"
            "  EXECUTE IMMEDIATE 'TRUNCATE TABLE TD_AB00008';\n"
            "END;",
            ["TD_AB00008", "TI_SRC"],
        )
        by_target = {e.target_id: (e.relation, e.dml_type) for e in edges}
        self.assertEqual(by_target[ids["TD_AB00008"]], ("WRITES", "TRUNCATE"))
        self.assertEqual(by_target[ids["TI_SRC"]], ("READS", "SELECT"))

    def test_commented_truncate_makes_no_edge(self):
        _, edges = self._run(
            "BEGIN\n  /*EXECUTE IMMEDIATE 'TRUNCATE TABLE TD_AB00035';*/\n  NULL;\nEND;",
            ["TD_AB00035"],
        )
        self.assertEqual(edges, [])

    # ── US2: FROM 생략 DELETE + 회귀 ──

    def test_fromless_delete_is_writes(self):
        ids, edges = self._run(
            "BEGIN\n  EXECUTE IMMEDIATE 'DELETE SGIOS.ARREO_SMS_KAKAO'||v_cond;\nEND;",
            ["ARREO_SMS_KAKAO"],
        )
        self.assertEqual(
            [(e.relation, e.dml_type) for e in edges],
            [("WRITES", "DELETE")],
        )

    def test_delete_from_unchanged(self):
        ids, edges = self._run("DELETE FROM ORDERS WHERE ID=1;", ["ORDERS"])
        self.assertEqual([(e.relation, e.dml_type) for e in edges], [("WRITES", "DELETE")])

    def test_collection_method_delete_is_not_a_write_target(self):
        """`v_tab.DELETE(i)` — 컬렉션 메서드. 대상 추출 불발, 우연 동명 테이블은 READS 유지."""
        ids, edges = self._run(
            "BEGIN\n  SELECT COUNT(*) INTO v FROM ORDERS;\n  v_tab.DELETE(3);\nEND;",
            ["ORDERS"],
        )
        self.assertEqual([e.relation for e in edges], ["READS"])

    def test_ctas_target_writes_source_reads(self):
        ids, edges = self._run(
            "CREATE TABLE T_NEW AS SELECT * FROM T_OLD;", ["T_NEW", "T_OLD"],
        )
        by_target = {e.target_id: (e.relation, e.dml_type) for e in edges}
        self.assertEqual(by_target[ids["T_NEW"]], ("WRITES", "CREATE"))
        self.assertEqual(by_target[ids["T_OLD"]][0], "READS")

    def test_procedure_header_create_is_not_dml(self):
        """`CREATE OR REPLACE PROCEDURE` 헤더만으로는 DML 판정 금지 (detect 구문 계약)."""
        _, edges = self._run(
            "CREATE OR REPLACE PROCEDURE P AS\nBEGIN\n  NULL;\nEND;", ["ORDERS"],
        )
        self.assertEqual(edges, [])


class CSemanticParityTest(unittest.TestCase):
    """US3 — C 리터럴 SQL 경로가 DBMS 경로와 같은 표에서 같은 판정 (SC-005)."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.transform = SimpleNamespace(ddl_naming_rule="as_is", fallback_prefixes=[])

    def _run_c(self, c_code: str, table_names: list[str]):
        src = self.dir / "case.c"
        src.write_text(c_code, encoding="utf-8")
        lookup = {n: f"db:d.S.{n}" for n in table_names}
        locator = CodeLocator(
            [_node("f:1", str(src), 1, c_code.count("\n") + 1)]
        )
        return lookup, _c_sql_semantic_edges(c_code, src, locator, lookup, self.transform)

    def test_c_merge_using_source_is_reads(self):
        """D6 — C 경로 USING 소스 READS (종전 누락, 의도된 개선)."""
        lookup, edges = self._run_c(
            'void f(void) {\n'
            '  exec_sql("MERGE INTO T_DST D USING T_SRC S ON (D.ID=S.ID) "\n'
            '           "WHEN MATCHED THEN UPDATE SET D.X = S.X");\n'
            '}\n',
            ["T_DST", "T_SRC"],
        )
        by_target = {e.target_id: e.relation for e in edges}
        self.assertEqual(by_target[lookup["T_DST"]], "WRITES")
        self.assertEqual(by_target[lookup["T_SRC"]], "READS")

    def test_c_truncate_literal_writes(self):
        lookup, edges = self._run_c(
            'void f(void) { run("TRUNCATE TABLE TD_LOG"); }\n', ["TD_LOG"],
        )
        self.assertEqual(
            [(e.relation, e.dml_type) for e in edges], [("WRITES", "TRUNCATE")],
        )

    def test_dbms_and_c_lane_agree_on_same_sql(self):
        """같은 SQL 텍스트 → 두 경로 relation 동일 (SC-005)."""
        sql = "MERGE INTO T_DST D USING T_SRC S ON (D.ID=S.ID) WHEN MATCHED THEN UPDATE SET D.X=S.X"
        # DBMS 경로
        src = self.dir / "same.sql"
        src.write_text(sql, encoding="utf-8")
        tables = [Table(db="d", schema="S", name=n) for n in ("T_DST", "T_SRC")]
        dbms = {
            e.target_id.rsplit(".", 1)[-1]: e.relation
            for e in _link([_node("p:1", str(src), 1, 1)], tables, [], SourceFileCache())
        }
        # C 경로
        lookup, c_edges = self._run_c(f'void f(void) {{ run("{sql}"); }}\n', ["T_DST", "T_SRC"])
        c_lane = {e.target_id.rsplit(".", 1)[-1]: e.relation for e in c_edges}
        self.assertEqual(dbms, c_lane)


class CodeLocatorOwnershipTest(unittest.TestCase):
    def test_component_suffix_does_not_confuse_similar_directory_name(self) -> None:
        locator = CodeLocator([_node("right", "a/x.c", 1, 5)])
        self.assertIsNone(locator.locate("ba/x.c", 2))

    def test_duplicate_basename_is_explicitly_ambiguous(self) -> None:
        locator = CodeLocator([
            _node("left", "left/x.c", 1, 5),
            _node("right", "right/x.c", 1, 5),
        ])
        with self.assertRaisesRegex(ValueError, "ambiguous CodeNode file ownership"):
            locator.locate("x.c", 2)

    def test_nested_code_range_uses_narrowest_owner(self) -> None:
        locator = CodeLocator([
            _node("outer", "x.c", 1, 20),
            _node("inner", "x.c", 5, 10),
        ])
        self.assertEqual("inner", locator.locate("x.c", 7))

    def test_equal_code_ranges_are_explicitly_ambiguous(self) -> None:
        locator = CodeLocator([
            _node("one", "x.c", 1, 5),
            _node("two", "x.c", 1, 5),
        ])
        with self.assertRaisesRegex(ValueError, "ambiguous CodeNode line ownership"):
            locator.locate("x.c", 2)

    def test_control_ranges_do_not_compete_with_routine_ownership(self) -> None:
        locator = CodeLocator([
            _node("routine", "x.c", 1, 20),
            _node("if", "x.c", 5, 10, label="IF"),
            _node("else", "x.c", 5, 10, label="ELSE"),
        ])
        self.assertEqual("routine", locator.locate("x.c", 7))


if __name__ == "__main__":
    unittest.main()
