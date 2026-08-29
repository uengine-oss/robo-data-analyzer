"""spec 115 — embedded_sql 제4형 (울타리 블록 → DML 의미표 재판정).

Pro*C / COBOL 실전 형태를 코퍼스 그대로 고정한다. R/W 판정 로직은 의미표 하나
(중복 0) — 블록 추출만 규칙 데이터(delimiter 2칸)다.
"""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agents.contracts import DdlTransform, EmbeddedSqlRule
from agents.rule_compiler import RuleCompileError, compile_rule, split_rules
from graph.schema.graph_nodes import CodeNode, Table
from pipeline.steps.step5_linking.link_tables.regex_table_linker import RegexTableLinker


def _node(path: str, n_lines: int) -> CodeNode:
    return CodeNode(id="f:1", signature="f", file_path=path,
                    start_line=1, end_line=n_lines, label="FUNCTION")


class EmbeddedSqlLinkTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    async def _link(self, filename: str, source: str, tables: list[str], rule: EmbeddedSqlRule):
        src = self.root / filename
        src.write_text(source, encoding="utf-8")
        return await RegexTableLinker().link(
            project_root=self.root,
            code_nodes=[_node(str(src), source.count("\n") + 1)],
            tables=[Table(db="d", schema="S", name=n) for n in tables],
            patterns=[],
            ddl_transform=DdlTransform(ddl_naming_rule="as_is"),
            embedded_rules=[rule],
        )

    async def test_cobol_exec_sql_update_is_writes(self):
        """COBOL 형태 — 여러 줄 블록, 의미표가 UPDATE=WRITES 판정."""
        outcome = await self._link(
            "prog.cbl",
            "PROCEDURE DIVISION.\n"
            "    EXEC SQL\n"
            "      UPDATE ORDERS SET STATUS = '20'\n"
            "    END-EXEC.\n",
            ["ORDERS"],
            EmbeddedSqlRule(id="cobol", delimiter_start="EXEC SQL", delimiter_end="END-EXEC"),
        )
        self.assertEqual(
            [(e.relation, e.dml_type) for e in outcome.edges], [("WRITES", "UPDATE")],
        )

    async def test_proc_exec_sql_select_reads_and_truncate_writes(self):
        """Pro*C 형태 — 블록 2개, SELECT=READS·TRUNCATE=WRITES (의미표 커버리지 그대로)."""
        outcome = await self._link(
            "prog.pc",
            'void f() {\n'
            '  EXEC SQL SELECT ENAME INTO :n FROM EMP;\n'
            '  EXEC SQL TRUNCATE TABLE TMP_LOG;\n'
            '}\n',
            ["EMP", "TMP_LOG"],
            EmbeddedSqlRule(id="proc", delimiter_start="EXEC SQL", delimiter_end=";"),
        )
        by_target = {e.target_id.rsplit(".", 1)[-1]: (e.relation, e.dml_type)
                     for e in outcome.edges}
        self.assertEqual(by_target["EMP"], ("READS", "SELECT"))
        self.assertEqual(by_target["TMP_LOG"], ("WRITES", "TRUNCATE"))

    async def test_non_dml_block_is_skipped(self):
        """DECLARE 류 비 DML 블록은 무시 (의미표 first_statement 판정)."""
        outcome = await self._link(
            "prog.pc",
            "EXEC SQL DECLARE C1 CURSOR WITH HOLD;\n",
            ["EMP"],
            EmbeddedSqlRule(id="proc", delimiter_start="EXEC SQL", delimiter_end=";"),
        )
        self.assertEqual(outcome.edges, [])

    async def test_unknown_table_counts_as_unresolved_table_name(self):
        """현재 업로드 DDL에 없는 이름은 로컬 미해소로 계수한다."""
        outcome = await self._link(
            "prog.pc",
            "EXEC SQL SELECT A FROM NOT_A_TABLE;\n",
            ["EMP"],
            EmbeddedSqlRule(id="proc", delimiter_start="EXEC SQL", delimiter_end=";"),
        )
        self.assertEqual(outcome.edges, [])
        self.assertEqual(outcome.unresolved_table_names, 1)

    async def test_cobol_commented_exec_sql_is_not_linked(self):
        """고정형 7열·인라인 주석의 SQL은 실행 근거가 아니다."""
        outcome = await self._link(
            "prog.cbl",
            "000100* EXEC SQL UPDATE ORDERS SET STATUS = '20' END-EXEC\n"
            "      *> EXEC SQL UPDATE ORDERS SET STATUS = '30' END-EXEC\n",
            ["ORDERS"],
            EmbeddedSqlRule(id="cobol", delimiter_start="EXEC SQL", delimiter_end="END-EXEC"),
        )
        self.assertEqual(outcome.edges, [])

    async def test_proc_uses_c_lexing_without_claiming_parser_support(self):
        """Pro*C는 C 주석을 가리되 AST parser 지원 목록에는 섞이지 않는다."""
        from pipeline.languages import is_supported, lang_of

        self.assertEqual(lang_of(".pc"), "c")
        self.assertFalse(is_supported(".pc"))
        outcome = await self._link(
            "prog.pc",
            "/* EXEC SQL UPDATE ORDERS SET STATUS = '20'; */\n",
            ["ORDERS"],
            EmbeddedSqlRule(id="proc", delimiter_start="EXEC SQL", delimiter_end=";"),
        )
        self.assertEqual(outcome.edges, [])


class SplitAndCompileContractTest(unittest.TestCase):
    def test_split_separates_embedded(self):
        rule = EmbeddedSqlRule(id="e", delimiter_start="EXEC SQL", delimiter_end=";")
        compilable, embedded = split_rules([rule])
        self.assertEqual(compilable, [])
        self.assertEqual(embedded, [rule])

    def test_compile_rule_refuses_embedded(self):
        """embedded 는 정규식 컴파일 대상이 아님 — 명시 오류 (조용한 통과 금지)."""
        with self.assertRaises(RuleCompileError):
            compile_rule(EmbeddedSqlRule(id="e", delimiter_start="EXEC SQL", delimiter_end=";"))


if __name__ == "__main__":
    unittest.main()
